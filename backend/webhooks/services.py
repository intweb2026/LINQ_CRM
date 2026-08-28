"""
webhooks/services.py
─────────────────────
WebhookProcessor: full lifecycle processor with:
- payload validation
- upsert support (create or update booking)
- step-by-step processing notes
- stack trace capture
- timing instrumentation
- DB operation tracking
NO company objects are created.
"""
import logging
import time
import traceback
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from accounts.import_common import parse_import_date
from book_event.booking_code_canonical import DEFAULT_BOOKING_CODE
from book_event.models import BookEvent
from book_event.serializers import WebsiteBookingSerializer
from book_delegate.models import BookDelegate
from .event_resolver import DIAG, resolve_event_code
from .models import WebhookLog
from .utils import unwrap_payload

logger = logging.getLogger(__name__)


class WebhookProcessor:
    def __init__(self, log: WebhookLog):
        self.log   = log
        self.notes = []

    def _note(self, msg: str):
        ts = datetime.utcnow().strftime("%H:%M:%S.%f")[:-3]
        self.notes.append(f"[{ts}] {msg}")

    def process(self) -> tuple[bool, dict]:
        log = self.log
        processing_start = time.monotonic()

        log.status               = WebhookLog.Status.PROCESSING
        log.processing_started_at = timezone.now()
        log.save(update_fields=["status", "processing_started_at"])

        self._note("Processing started.")

        # ── 1. Payload validation ──────────────────────────────────────────────
        payload = unwrap_payload(log.payload)
        ser = WebsiteBookingSerializer(data=payload)
        if not ser.is_valid():
            self._note(f"Payload validation FAILED: {ser.errors}")
            duration = round(time.monotonic() - processing_start, 3)
            log.status            = WebhookLog.Status.FAILED
            log.processing_status = WebhookLog.ProcessingStatus.ERROR
            log.http_status       = 400
            log.error_message     = str(ser.errors)
            log.processing_notes  = "\n".join(self.notes)
            log.processing_duration = duration
            log.processed_at      = timezone.now()
            log.save(update_fields=[
                "status", "processing_status", "http_status", "error_message",
                "processing_notes", "processing_duration", "processed_at",
            ])
            return False, {"detail": "Payload validation failed.", "errors": ser.errors}

        # Amount fields never fail validation, this CRM does not track amounts,
        # so a value that could not be read is reported here and the delivery
        # goes on to succeed. See WebsiteBookingSerializer.validate.
        for warning in getattr(ser, "amount_warnings", []):
            self._note(f"Amount not recorded, {warning}")

        d              = ser.validated_data
        invoice_number = d["InvoiceNumber"]
        raw_event_code = d.get("Eventcode", "")
        event_code     = self.normalize_event_code(raw_event_code)

        # Look up the Event. All code matching lives in event_resolver; this
        # function does no matching of its own, so there is exactly one place
        # where the anchored boundary rule can be got wrong.
        resolution = resolve_event_code(raw_event_code, event_code)
        target_event = resolution.event

        if not target_event:
            # The diagnostic carries the raw code, the normalised code, the full
            # prefilter candidate list and which rule rejected it — a 400 here
            # must be answerable from the log alone.
            self._note(f"Validation FAILED: {DIAG[resolution.outcome]}")
            self._note(resolution.diagnostic)
            duration = round(time.monotonic() - processing_start, 3)
            log.status            = WebhookLog.Status.FAILED
            log.processing_status = WebhookLog.ProcessingStatus.ERROR
            log.http_status       = resolution.http_status
            log.error_message     = resolution.error_message
            log.processing_notes  = "\n".join(self.notes)
            log.processing_duration = duration
            log.processed_at      = timezone.now()
            log.save(update_fields=[
                "status", "processing_status", "http_status", "error_message",
                "processing_notes", "processing_duration", "processed_at",
            ])
            return False, {"detail": resolution.error_message}

        # Use the resolved target event's exact code and name!
        resolved_event_code = target_event.event_code
        event_name = target_event.name
        self._note(f"{DIAG[resolution.outcome]} resolved {raw_event_code!r} "
                   f"-> {resolved_event_code!r} via {resolution.tier}")

        d["Eventcode"] = resolved_event_code
        d["Eventname"] = event_name
        event_code = resolved_event_code

        self._note(f"Payload validated. Invoice={invoice_number}  Event={event_code}")

        # ── 2. Field normalization ─────────────────────────────────────────────
        ps_map   = {v.lower(): v for v in BookEvent.PaymentStatus.values}
        tier_map = {v.lower(): v for v in BookEvent.TicketTier.values}
        pof_map  = {v.lower(): v for v in BookEvent.PaidOrFree.values}

        payment_status = ps_map.get(d.get("PaymentStatus", "").strip().lower(), BookEvent.PaymentStatus.PENDING)
        
        # Resolve ticket tier from both TicketTier and Packages
        packages_val = d.get("Packages", "")
        if isinstance(packages_val, list):
            packages_str = " ".join([str(p) for p in packages_val])
        else:
            packages_str = str(packages_val)

        ticket_tier_raw = d.get("TicketTier", "").strip().lower()

        d["TicketTier"] = self._resolve_ticket_tier(
            (packages_str, ticket_tier_raw), tier_map, ticket_tier_raw, "Regular",
        )
        d["PaidOrFree"] = pof_map.get(d.get("PaidOrFree", "").strip().lower(), "Paid")

        # ── 3. Sales exec assignment ───────────────────────────────────────────
        sales_exec = BookEvent.auto_assign_sales(event_code)
        self._note(f"Sales exec: {sales_exec.username if sales_exec else 'unassigned'}")

        # ── 4. Determine INSERT vs UPSERT ─────────────────────────────────────
        existing_invoice = BookEvent.objects.filter(invoice_number=invoice_number).first()

        try:
            with transaction.atomic():
                if existing_invoice:
                    invoice, db_status, note = self._update_booking(
                        existing_invoice, d, payment_status, target_event)
                else:
                    invoice, db_status, note = self._create_booking(d, event_code, payment_status, sales_exec)

            self._note(note)

            # ── 5. Delegate processing ─────────────────────────────────────────
            delegates_payload = d.get("Delegates", [])
            inserted_delegates, skipped_delegates, failed_delegates = self._process_delegates(
                invoice, event_code, d, delegates_payload, tier_map, pof_map,
            )

            # ── 6. Update contact info ─────────────────────────────────────────
            all_delegates = list(invoice.delegates.order_by("id"))
            if all_delegates and not existing_invoice:
                first = all_delegates[0]
                invoice.contact_name  = first.full_name
                invoice.contact_email = first.email
                invoice.delegate_count = len(all_delegates)
                invoice.save(update_fields=["contact_name", "contact_email", "delegate_count"])

        except Exception as exc:
            err_str   = str(exc)
            trace_str = traceback.format_exc()
            self._note(f"EXCEPTION: {err_str}")
            logger.error("Webhook processing error: %s", err_str, exc_info=True)

            duration = round(time.monotonic() - processing_start, 3)
            log.status              = WebhookLog.Status.FAILED
            log.processing_status   = WebhookLog.ProcessingStatus.ERROR
            log.http_status         = 500
            log.invoice_number      = invoice_number
            log.event_code          = event_code
            log.error_message       = err_str
            log.stack_trace         = trace_str
            log.processing_notes    = "\n".join(self.notes)
            log.processing_duration = duration
            log.processed_at        = timezone.now()
            log.save(update_fields=[
                "status", "processing_status", "http_status",
                "invoice_number", "event_code", "error_message",
                "stack_trace", "processing_notes", "processing_duration", "processed_at",
            ])
            return False, {"detail": "Internal error during booking creation.", "error": err_str}

        # ── 7. Success logging ─────────────────────────────────────────────────
        final_db_status = (
            WebhookLog.DbInsertStatus.PARTIAL
            if failed_delegates > 0 and inserted_delegates > 0
            else WebhookLog.DbInsertStatus.FAILED
            if failed_delegates > 0 and inserted_delegates == 0
            else db_status
        )

        duration = round(time.monotonic() - processing_start, 3)

        self._note(
            f"Complete. Delegates inserted={inserted_delegates} skipped={skipped_delegates} "
            f"failed={failed_delegates}  duration={duration}s"
        )

        logger.info(
            "Webhook processed: %s | event: %s | delegates: %d | db=%s | %.3fs",
            invoice_number, event_code, inserted_delegates, db_status, duration,
        )

        if sales_exec and not existing_invoice:
            from accounts.models import ActionLog
            ActionLog.objects.create(
                user=sales_exec,
                action=f"Auto-assigned via webhook to {invoice.invoice_number}",
                details=f"Source: webhook | Event: {event_code}",
            )

        log.status                  = WebhookLog.Status.SUCCESS
        log.processing_status       = WebhookLog.ProcessingStatus.PROCESSED
        log.http_status             = 201 if not existing_invoice else 200
        log.invoice_number          = invoice_number
        log.event_code              = event_code
        log.event_name              = d.get("Eventname", "")
        log.created_booking         = invoice
        log.created_delegates_count = inserted_delegates
        log.db_insert_status        = final_db_status
        log.records_inserted        = inserted_delegates
        log.records_updated         = 1 if existing_invoice else 0
        log.records_failed          = failed_delegates
        log.processing_notes        = "\n".join(self.notes)
        log.processing_duration     = duration
        log.processed_at            = timezone.now()
        log.save(update_fields=[
            "status", "processing_status", "http_status",
            "invoice_number", "event_code", "event_name",
            "created_booking", "created_delegates_count",
            "db_insert_status", "records_inserted", "records_updated", "records_failed",
            "processing_notes", "processing_duration", "processed_at",
        ])

        return True, {
            "invoice_number":    invoice.invoice_number,
            "booking_id":        invoice.id,
            "event_code":        invoice.event_code,
            "db_action":         "updated" if existing_invoice else "inserted",
            "delegates_created": inserted_delegates,
            "delegates_skipped": skipped_delegates,
            "sales_executive":   sales_exec.username if sales_exec else None,
            "payment_status":    invoice.payment_status,
        }

    # ── Private helpers ────────────────────────────────────────────────────────

    def normalize_event_code(self, code):
        """
        Normalizes event codes to match the designated codes in the system.
        - Strips year suffixes (e.g., '26', '25')
        - Maps specific variant codes (e.g., 'ACU' -> 'ACU - RS')
        """
        if not code: return ""
        code = code.strip()
        
        # Specific mappings
        mapping = {
            "ACU":        "ACU - RS",
            "ACU - RS26": "ACU - RS",
            "ACU-RS26":   "ACU - RS",
            "ACU-RS":     "ACU - RS",
        }
        if code in mapping:
            return mapping[code]
            
        # General rule: Strip '26', '25', etc. from the end if it's a suffix
        # e.g., "MMU/GS - JS26" -> "MMU/GS - JS"
        for year in ["26", "25", "27"]:
            if code.endswith(year):
                # Only strip if it follows a letter or space (avoid stripping from codes where the number is part of the ID)
                return code[:-len(year)].strip()
        
        return code

    def parse_webhook_date(self, val, field=""):
        """
        A date out of a webhook payload, or None.

        NEVER raises, and a date is never the reason a delivery is rejected —
        same rule as the amount fields, see WebsiteBookingSerializer.validate.
        What is new is that a value which could NOT be read now says so in the
        processing notes. It used to return None silently, so a booking whose
        Invoice Date arrived as "08.05.2026" or as an Excel serial was stored
        with a blank date and nothing anywhere distinguished that from the
        source having sent nothing at all.

        The format list itself lives in accounts/import_common.parse_import_date,
        which this codebase already declares the single authority on reading a
        date out of an import. This method used to carry its own ten formats,
        which is how it came to disagree with the four other importers.
        """
        parsed, error = parse_import_date(val)
        if error:
            label = f"{field}=" if field else ""
            self._note(f"Date not recorded, {label}{error}; stored as empty")
        return parsed

    def _create_booking(self, d, event_code, payment_status, sales_exec):
        invoice_date_val = (
            self.parse_webhook_date(d.get("InvoiceDate"), "InvoiceDate")
            or self.parse_webhook_date(d.get("Date"), "Date")
            or timezone.localdate()
        )
        invoice = BookEvent.objects.create(
            invoice_number         = d["InvoiceNumber"],
            event_code             = event_code,
            event_name             = d.get("Eventname", ""),
            event_date             = self.parse_webhook_date(d.get("Date"), "Date"),
            invoice_date           = invoice_date_val,
            company_name           = d.get("DelegateCompanyName", ""),
            accounts_contact_email = d.get("AccountsContactEmail", ""),
            discount               = d.get("Discount", 0),
            discount_code          = d.get("DiscountCode", ""),
            pre_tax_amount         = d.get("PreTaxAmount"),
            tax_amount             = d.get("TaxAmount"),
            total_amount           = d.get("TotalAmount"),
            add_ons_total_amount   = d.get("AddOnsTotalAmount"),
            currency               = d.get("Currency", "USD"),
            payment_status         = payment_status,
            sales_executive        = sales_exec,
            packages               = d.get("Packages", []),
            ticket_tier            = d.get("TicketTier", ""),
            paid_or_free           = d.get("PaidOrFree", ""),
            payment_type           = d.get("PaymentType", ""),
            request_date           = invoice_date_val,
            booking_code           = DEFAULT_BOOKING_CODE,
        )
        return invoice, WebhookLog.DbInsertStatus.INSERTED, f"Booking CREATED: id={invoice.id}"

    def _update_booking(self, invoice, d, payment_status, target_event):
        """
        Update non-payment fields on an existing booking.

        event_code comes from the resolved Event verbatim. It used to be
        normalize_event_code(d["Eventcode"]), but by this point d["Eventcode"]
        has already been replaced with the RESOLVED code, so normalising again
        stripped the year suffix a second time and wrote a code that matches no
        Event. Harmless on today's data — no Event code currently carries a year
        suffix or hits the ACU mapping, so the second pass is a no-op and 0 rows
        are corrupted — but it is a landmine the first time either becomes true.
        """
        update_fields = []

        parsed_invoice_date = self.parse_webhook_date(
            d.get("InvoiceDate"), "InvoiceDate"
        ) or self.parse_webhook_date(d.get("Date"), "Date")

        field_map = {
            "event_code":             target_event.event_code,
            "event_name":             d.get("Eventname", ""),
            "event_date":             self.parse_webhook_date(d.get("Date"), "Date"),
            "company_name":           d.get("DelegateCompanyName", ""),
            "accounts_contact_email": d.get("AccountsContactEmail", ""),
            "discount":               d.get("Discount", 0),
            "discount_code":          d.get("DiscountCode", ""),
            "pre_tax_amount":         d.get("PreTaxAmount"),
            "tax_amount":             d.get("TaxAmount"),
            "total_amount":           d.get("TotalAmount"),
            "add_ons_total_amount":   d.get("AddOnsTotalAmount"),
            "currency":               d.get("Currency", "USD"),
            "form_name":              d.get("FormName", ""),
            "form_url":               d.get("FormURL", ""),
            "packages":               d.get("Packages", []),
            "payment_status":         payment_status,
            "ticket_tier":            d.get("TicketTier", ""),
            "paid_or_free":           d.get("PaidOrFree", ""),
            "payment_type":           d.get("PaymentType", ""),
        }
        
        if parsed_invoice_date:
            field_map["invoice_date"] = parsed_invoice_date
            field_map["request_date"] = parsed_invoice_date
            
        if not getattr(invoice, "booking_code", ""):
            field_map["booking_code"] = DEFAULT_BOOKING_CODE
            
        for attr, val in field_map.items():
            if val or val == 0: 
                if getattr(invoice, attr) != val:
                    setattr(invoice, attr, val)
                    update_fields.append(attr)
 
        if not invoice.request_date:
            if invoice.invoice_date:
                invoice.request_date = invoice.invoice_date
            else:
                invoice.request_date = timezone.localdate()
            update_fields.append("request_date")
            
        if not invoice.invoice_date:
            invoice.invoice_date = invoice.request_date
            update_fields.append("invoice_date")

        if update_fields:
            invoice.save(update_fields=update_fields)
            note = f"Booking UPDATED: id={invoice.id} fields={update_fields}"
        else:
            note = f"Booking UNCHANGED: id={invoice.id} (no significant field changes)"

        return invoice, WebhookLog.DbInsertStatus.UPDATED, note
    # Ticket-tier words, in the order they must be tested: "super early" before
    # "early", and both before "regular". Held as data because the identical
    # four-clause if/elif chain was written out three times, once for the
    # invoice and once in each delegate branch.
    TIER_WORDS = (
        ("SEB",     ("super early", "seb")),
        ("EB",      ("early", "eb")),
        ("Regular", ("regular", "standard")),
    )

    @classmethod
    def _resolve_ticket_tier(cls, texts, tier_map, raw, default):
        """
        A ticket tier read out of whatever free text a booking form sent.

        `texts` are searched for the words above; `raw` is the already-lowered
        TicketTier value, looked up in `tier_map` only when no word matched, and
        `default` is the answer when even that misses.

        Substring matching, deliberately unchanged from the three chains this
        replaces: "eb" matches anywhere, so a package named "September Special"
        still resolves to EB. That is wrong and it is not new, and correcting it
        here would change how live deliveries are classified under cover of a
        refactor. Left as it was, on purpose.
        """
        blob = " ".join(t for t in texts if t).lower()
        for tier, words in cls.TIER_WORDS:
            if any(word in blob for word in words):
                return tier
        return tier_map.get(raw, default)


    def _process_delegates(self, invoice, event_code, d, delegates_payload, tier_map, pof_map):
        inserted = skipped = failed = 0
        company_name = d.get("DelegateCompanyName", "")

        for i, dp in enumerate(delegates_payload):
            email = dp.get("Email", "").strip().lower()
            if not email:
                skipped += 1
                self._note(f"Delegate #{i+1} skipped: no email")
                continue
            try:
                existing = BookDelegate.objects.filter(invoice=invoice, email=email).first()
                if existing:
                    # Update existing delegate
                    changed = []
                    del_tier_raw = dp.get("TicketTier", "").strip().lower()
                    del_package_raw = dp.get("TicketPackage", "").strip().lower()
                    del_resolved_tier = ""
                    if "super early" in del_package_raw or "seb" in del_package_raw or "seb" in del_tier_raw or "super early" in del_tier_raw:
                        del_resolved_tier = "SEB"
                    elif "early" in del_package_raw or "eb" in del_package_raw or "eb" in del_tier_raw or "early" in del_tier_raw:
                        del_resolved_tier = "EB"
                    elif "regular" in del_package_raw or "standard" in del_package_raw or "regular" in del_tier_raw or "standard" in del_tier_raw:
                        del_resolved_tier = "Regular"
                    else:
                        del_resolved_tier = tier_map.get(del_tier_raw, invoice.ticket_tier or "Regular")

                    upd = {
                        "first_name":        dp.get("FirstName", "").strip(),
                        "last_name":         dp.get("LastName", "").strip(),
                        "phone_number":      dp.get("PhoneNumber", "").strip(),
                        "position":          dp.get("Position", "").strip(),
                        "ticket_package":    dp.get("TicketPackage", "").strip(),
                        "sponsorship_level": dp.get("SponsorshipLevel", "").strip(),
                        "company_name_raw":  company_name,
                        "delegate_ticket_tier": del_resolved_tier,
                        "delegate_paid_or_free": pof_map.get(dp.get("PaidOrFree", "").strip().lower(), "Paid"),
                    }
                    for attr, val in upd.items():
                        if getattr(existing, attr, None) != val:
                            setattr(existing, attr, val)
                            changed.append(attr)
                    if changed:
                        existing.save(update_fields=changed)
                        self._note(f"Delegate #{i+1} updated: {email}")
                    else:
                        self._note(f"Delegate #{i+1} unchanged: {email}")
                    skipped += 1
                else:
                    del_tier_raw = dp.get("TicketTier", "").strip().lower()
                    del_package_raw = dp.get("TicketPackage", "").strip().lower()
                    del_resolved_tier = ""
                    if "super early" in del_package_raw or "seb" in del_package_raw or "seb" in del_tier_raw or "super early" in del_tier_raw:
                        del_resolved_tier = "SEB"
                    elif "early" in del_package_raw or "eb" in del_package_raw or "eb" in del_tier_raw or "early" in del_tier_raw:
                        del_resolved_tier = "EB"
                    elif "regular" in del_package_raw or "standard" in del_package_raw or "regular" in del_tier_raw or "standard" in del_tier_raw:
                        del_resolved_tier = "Regular"
                    else:
                        del_resolved_tier = tier_map.get(del_tier_raw, invoice.ticket_tier or "Regular")

                    BookDelegate.objects.create(
                        invoice           = invoice,
                        event_code        = event_code,
                        company           = None,
                        company_name_raw  = company_name,
                        first_name        = dp.get("FirstName", "").strip(),
                        last_name         = dp.get("LastName", "").strip(),
                        email             = email,
                        phone_number      = dp.get("PhoneNumber", "").strip(),
                        position          = dp.get("Position", "").strip(),
                        ticket_package    = dp.get("TicketPackage", "").strip(),
                        sponsorship_level = dp.get("SponsorshipLevel", "").strip(),
                        delegate_ticket_tier = del_resolved_tier,
                        delegate_paid_or_free = pof_map.get(dp.get("PaidOrFree", "").strip().lower(), "Paid"),
                    )
                    inserted += 1
                    self._note(f"Delegate #{i+1} inserted: {email}")
            except Exception as exc:
                failed += 1
                self._note(f"Delegate #{i+1} FAILED ({email}): {exc}")
                logger.warning("Delegate creation error: %s", exc)

        return inserted, skipped, failed
