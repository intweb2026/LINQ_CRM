import logging

from django.db import transaction
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.bulk_update import BulkUpdateMixin, build_bulk_update_fields
from accounts.filter_spec import FilterSpecMixin, build_filter_spec_fields
from accounts.ordering import StableOrderingFilter
from accounts.permissions import RBACMixin, IsAdminRole
from accounts.crm_permissions import crm_permission, has_module_action
from book_event.models import BookEvent
from .models import BookDelegate
from .serializers import (
    BookDelegateListSerializer, BookDelegateDetailSerializer, BookDelegateWriteSerializer,
)
from .filters import BookDelegateFilter

logger = logging.getLogger(__name__)


from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters


# ── Transfer breadcrumbs ─────────────────────────────────────────────────────
# The wording is not invented here — it is the convention already in the data,
# written by hand in Zoho across ~200 transfers. Two directions, two dominant
# spellings, kept exactly so a transfer made in this CRM is indistinguishable from
# the ones already recorded:
#
#     source row      "Transferred to FAU'25"        (29 rows use the "from -" form
#     destination row "Transferred from - AIU25"      below; both forms appear)
#
# The edition is rendered two-digit, as every existing example does.

def _yy(edition):
    """2026 → '26'. Empty when there is no edition to name."""
    return str(edition)[-2:] if edition else ""


def _transferred_to(event_code, edition):
    suffix = _yy(edition)
    return f"Transferred to {event_code}'{suffix}" if suffix else f"Transferred to {event_code}"


def _transferred_from(event_code, edition):
    return f"Transferred from - {event_code}{_yy(edition)}"


def _append_reference(current, note):
    """
    Add `note` to a reference without discarding what is already there.

    " / " is the separator the data uses — "OC250722019137000 / Transferred to
    FAU'25" keeps a payment reference AND the transfer note on one row. Overwriting
    would throw away the bank reference that proves the credit exists.
    """
    current = (current or "").strip()
    if not current:
        return note
    if note in current:
        return current
    return f"{current} / {note}"


class BookDelegateViewSet(FilterSpecMixin, BulkUpdateMixin, RBACMixin, viewsets.ModelViewSet):
    permission_classes = [crm_permission("bookings")]

    # ── Compound filter spec ──────────────────────────────────────────────────
    # The five person-level fields are RESOLVED: the table shows the delegate
    # override when set, else the invoice's value, so filtering the raw override
    # column would miss every inheriting row — currently all of them, since
    # delegate_payment_status is NULL on every row in the database.
    _RESOLVED = {
        "payment_status": ("delegate_payment_status", "invoice__payment_status",
                           "choice", "Payment Status", list(BookEvent.PaymentStatus.values)),
        "payment_type":   ("delegate_payment_type", "invoice__payment_type",
                           "choice", "Payment Type", list(BookEvent.PaymentType.values)),
        "ticket_tier":    ("delegate_ticket_tier", "invoice__ticket_tier",
                           "choice", "Ticket Tier", list(BookEvent.TicketTier.values)),
        "paid_or_free":   ("delegate_paid_or_free", "invoice__paid_or_free",
                           "choice", "Paid / Free", list(BookEvent.PaidOrFree.values)),
        "payment_date":   ("delegate_payment_date", "invoice__payment_date",
                           "date", "Payment Date", None),
    }

    filter_spec_fields = {
        **build_filter_spec_fields(
            BookDelegate,
            # invoice is the FK object itself; its columns are exposed by name below
            exclude={"invoice", "delegate_number"},
            labels={"company_name_raw": "Company (raw)", "event_code": "Event Code"},
        ),
        # Person-level resolved fields
        **{
            key: {
                "type": ftype, "label": label,
                **({"choices": choices} if choices else {}),
                "resolved": {"override": override, "invoice": invoice},
            }
            for key, (override, invoice, ftype, label, choices) in _RESOLVED.items()
        },
        # Invoice-sourced scalars the table shows alongside each delegate
        "invoice_number": {"type": "text", "label": "Invoice Number",
                           "source": "invoice__invoice_number"},
        # booking_code is NOT declared here any more: it is a concrete column on
        # BookDelegate, so build_filter_spec_fields() above registers it against the
        # delegate's own value. Keeping the invoice__booking_code source would have
        # filtered a different column from the one the table now displays.
        "company_name":   {"type": "text", "label": "Company",
                           "source": "invoice__company_name"},
        "event_name":     {"type": "text", "label": "Event Name",
                           "source": "invoice__event_name"},
        "request_date":   {"type": "date", "label": "Request Date",
                           "source": "invoice__request_date", "nullable": True},
        "invoice_date":   {"type": "date", "label": "Invoice Date",
                           "source": "invoice__invoice_date", "nullable": True},
        "total_amount":   {"type": "number", "label": "Total Amount",
                           "source": "invoice__total_amount", "nullable": True},
        "currency":       {"type": "choice", "label": "Currency",
                           "source": "invoice__currency",
                           "choices": list(BookEvent.Currency.values)},
        "source":         {"type": "choice", "label": "Source",
                           "source": "invoice__source",
                           "choices": list(BookEvent.Source.values)},
    }

    # ── Mass update ───────────────────────────────────────────────────────────
    # Both groups are derived from their model's own columns, which is what keeps
    # the person-level keys honest: payment_status / payment_date /
    # invoice_number are read-only @property on BookDelegate (models.py:131-145)
    # rather than columns, so they cannot appear here at all — only the real
    # delegate_* OVERRIDE columns do. Hand-written, the bare name was one typo
    # away, and it fails QUIETLY: the 21 invoice-sourced fields on
    # BookDelegateListSerializer (serializers.py:48-76) are read_only, so DRF
    # discards a write to them without complaint.
    #
    # Row group  -> the delegate's own columns; touches exactly the selected rows.
    # Parent group -> the shared invoice; ALSO changes delegates on the same
    #                 invoice that the caller never selected. That is the
    #                 `collateral` set the preview enumerates before Apply.
    bulk_update_label       = "delegates"
    bulk_update_parent_path = "invoice"
    bulk_update_fields = {
        # ── Row group: the delegate's own columns ─────────────────────────────
        # nullable mirrors null=True per column, so clearing one of the five
        # delegate_* overrides makes the delegate inherit from the invoice again
        # — a real thing a rep needs, to undo a mistaken override.
        **build_bulk_update_fields(
            BookDelegate,
            exclude=(
                # identity: email is half of unique_together (invoice, email),
                # and a name is not a batch property of anybody.
                "email", "first_name", "last_name",
                # derived in save() (models.py:88-97): event_code is re-parsed
                # into itself plus edition, or inherited from the invoice.
                "event_code", "edition",
                # positional, assigned per invoice rather than edited
                "delegate_number",
            ),
            # The delegate_* overrides are bare CharFields with no choices of
            # their own (models.py:107-111), so each list is sourced from the
            # corresponding BookEvent enum — the invoice value it shadows.
            # attendance is the exception: it has its own choices on models.py:46.
            choices={
                "delegate_payment_status": list(BookEvent.PaymentStatus.values),
                "delegate_payment_type":   list(BookEvent.PaymentType.values),
                "delegate_ticket_tier":    list(BookEvent.TicketTier.values),
                "delegate_paid_or_free":   list(BookEvent.PaidOrFree.values),
            },
            labels={
                "delegate_payment_status": "Payment Status (override)",
                "delegate_payment_type":   "Payment Type (override)",
                "delegate_ticket_tier":    "Ticket Tier (override)",
                "delegate_paid_or_free":   "Paid / Free (override)",
                "delegate_payment_date":   "Payment Date (override)",
                "company_name_raw":        "Company (raw)",
                "delegate_count":          "Counts Towards Headcount",
                "add_ons":                 "Add-ons",
            },
        ),
        # ── Parent group: written on the shared invoice ───────────────────────
        # EVERY key here writes the invoice, so it also changes delegates the
        # caller did not select. The mixin counts that blast radius as
        # `collateral` and the modal refuses the two-click path because of it.
        **build_bulk_update_fields(
            BookEvent,
            prefix="invoice",
            group="parent",
            exclude=(
                # identity — unique, and pre-generated by the website
                "invoice_number",
                # derived in BookEvent.save() (models.py:167-183): event_code is
                # re-parsed into itself plus edition, and event_name is rebuilt
                # from the Event catalogue.
                "event_code", "edition", "event_name",
                # website intake provenance — writing these would falsify where
                # a booking came from
                "source", "form_name", "form_url",
                # superseded by paid_or_free, kept only for historical rows
                "paid_free",
            ),
            labels={
                "accounts_contact_email": "Accounts Contact Email",
                "add_ons_total_amount":   "Add-ons Total Amount",
                "pre_tax_amount":         "Pre-tax Amount",
                "parent_code":            "Parent Code",
                "add_ons":                "Add-ons",
                "paid_or_free":           "Paid / Free",
                "delegate_count":         "Delegate Count (invoice)",
                "attendance":             "Attendance (invoice)",
                "discount_code":          "Discount Code",
            },
        ),
    }
    bulk_update_side_effects = {
        ("delegate_payment_status", "Cancelled"): "also sets delegate_count → 0",
    }

    def get_bulk_update_side_effects(self, field, raw_value):
        """
        Two consequences fire for ANY value, so neither can be keyed by
        (field, value) in the static dict above.

        booking_code: the Bookings modal keeps the invoice's own copy in step by
        writing the delegates' shared code back whenever they all agree
        (frontend/src/api/bookings.js). Nothing does that here, so a bulk write
        leaves invoice.booking_code holding the old value while revenue
        classification still reads it (book_event/views.py, config/views.py).

        Any parent write: BookEvent.save() re-derives event_name from the Event
        catalogue and re-parses edition out of event_code on every save, whatever
        column was actually set.
        """
        if field == "booking_code":
            return [
                "the invoice's own booking_code is NOT updated; revenue "
                "classification and the sync export still read that column"
            ]
        config = self.bulk_update_fields.get(field) or {}
        if config.get("group") == "parent":
            return [
                "saving the invoice also re-derives its event_name from the "
                "Events catalogue and re-parses its edition from the event code"
            ]
        return super().get_bulk_update_side_effects(field, raw_value)
    # StableOrderingFilter, not the stock one: the default sort
    # (-_sort_request_date) is heavily tied, and without a pk tiebreaker
    # pagination duplicates and skips rows. See accounts/ordering.py.
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, StableOrderingFilter]
    filterset_class = BookDelegateFilter
    search_fields   = [
        "first_name", "last_name", "email", "position",
        "invoice__invoice_number", "event_code", "company_name_raw",
    ]
    ordering_fields = [
        "id",   # was silently dropped as an unknown field before
        "_sort_invoice", "_sort_status", "_sort_date", "_sort_name", "_sort_request_date",
        "first_name", "last_name", "email", "event_code", "attendance", "created_at",
        "position", "company_name_raw",
        # Resolved person-level ordering. These are what the table must use for the
        # five override-backed columns — _sort_status orders by the invoice value and
        # therefore disagrees with the displayed cell. DRF silently DROPS an ordering
        # term that is not listed here, which is why they are named explicitly.
        "_sort_effective_payment_status", "_sort_effective_payment_type",
        "_sort_effective_paid_or_free", "_sort_effective_ticket_tier",
        "_sort_effective_payment_date",
    ]
    ordering        = ["-_sort_request_date"]

    def get_queryset(self):
        from django.db.models import F, Value
        from django.db.models.functions import Coalesce, Concat, NullIf
        qs = BookDelegate.objects.select_related("invoice__sales_executive", "company")
        qs = qs.annotate(
            _sort_invoice=F("invoice__invoice_number"),
            # NOTE: _sort_status orders by the INVOICE column, which is NOT what the
            # Payment Status cell displays. Kept because existing callers pass it,
            # but the table should use _sort_effective_payment_status below.
            _sort_status=F("invoice__payment_status"),
            _sort_date=F("invoice__invoice_date"),
            _sort_request_date=F("invoice__request_date"),
            _sort_name=Concat(F("first_name"), Value(" "), F("last_name")),
            # ── Ordering over the RESOLVED person-level values ────────────────
            # Same expression as accounts/filter_spec.py _resolved_expression and
            # the serializer's effective_* fields:
            #   COALESCE(NULLIF(<override>, ''), <invoice column>)
            # Without these, sorting Payment Status ordered by the invoice value
            # while the cell showed the resolved one, so the header claimed an order
            # the rows did not have as soon as any delegate carried an override.
            # See accounts/tests_resolved_ordering.py for the reproduction.
            _sort_effective_payment_status=Coalesce(
                NullIf("delegate_payment_status", Value("")), "invoice__payment_status"),
            _sort_effective_payment_type=Coalesce(
                NullIf("delegate_payment_type", Value("")), "invoice__payment_type"),
            _sort_effective_paid_or_free=Coalesce(
                NullIf("delegate_paid_or_free", Value("")), "invoice__paid_or_free"),
            _sort_effective_ticket_tier=Coalesce(
                NullIf("delegate_ticket_tier", Value("")), "invoice__ticket_tier"),
            # A DateField cannot hold '', so NULLIF would be a type error here.
            _sort_effective_payment_date=Coalesce(
                "delegate_payment_date", "invoice__payment_date"),
        )
        return self.rbac_filter_invoice(qs)

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return BookDelegateWriteSerializer
        if self.action == "retrieve":
            return BookDelegateDetailSerializer
        return BookDelegateListSerializer

    @action(detail=False, methods=["get"], url_path=r"by_invoice/(?P<invoice_number>[^/.]+)")
    def by_invoice(self, request, invoice_number=None):
        """GET /api/delegates/by_invoice/{invoice_number}/"""
        qs = self.get_queryset().filter(invoice__invoice_number=invoice_number)
        return Response(BookDelegateListSerializer(qs, many=True).data)

    @action(detail=False, methods=["post"], url_path="bulk_delete",
            permission_classes=[IsAdminRole])
    def bulk_delete(self, request):
        """
        Delete up to 1000 delegate records by ID, RBAC-SCOPED.

        Previously this ran `BookDelegate.objects.filter(id__in=ids)` — the default
        manager, not the scoped queryset — so any caller who passed the IsAdminRole
        gate could delete ANY delegate row by guessing its id, regardless of event
        assignment. IsAdminRole admits HP, any `is_admin` user, and any custom role
        with is_all_access, so that was wider than the role's read access.

        Resolving through self.get_queryset() routes the deletion through
        rbac_filter_invoice(), so a caller can only ever delete rows already inside
        their scope. IDs outside it are silently skipped rather than 403ing the
        whole batch — the response reports requested vs permitted so a partial
        delete is visible rather than looking like a success.

        Two-step (ids first, then a clean manager) because get_queryset() carries
        annotations and select_related, and .delete() on an annotated queryset is
        not reliable. permitted_ids is already scoped, so scoping is preserved.
        """
        from accounts.models import ActionLog
        ids = request.data.get("ids", [])
        if not isinstance(ids, list) or not ids:
            return Response({"detail": "ids list required"}, status=400)
        if len(ids) > 1000:
            return Response({"detail": "Maximum 1000 IDs per request"}, status=400)

        permitted_ids = list(
            self.get_queryset().filter(id__in=ids).values_list("id", flat=True)
        )
        skipped = len(set(ids)) - len(permitted_ids)
        if skipped:
            logger.warning(
                "bulk_delete: %s requested %d delegate ids, %d were out of scope",
                request.user.username, len(set(ids)), skipped,
            )
        if not permitted_ids:
            return Response(
                {"detail": "None of the requested records are in your scope.",
                 "deleted": 0, "requested": len(ids), "permitted": 0},
                status=403,
            )

        with transaction.atomic():
            qs = BookDelegate.objects.filter(id__in=permitted_ids)
            count = qs.count()
            ActionLog.objects.create(
                user    = request.user,
                action  = f"Bulk deleted {count} booking delegates",
                # Full permitted list, not ids[:50]: the audit trail should record
                # what was actually deleted, not a truncated sample of what was asked.
                details = (
                    f"requested={len(ids)} permitted={count} out_of_scope={skipped}\n"
                    f"ids={sorted(permitted_ids)}"
                ),
            )
            qs.delete()
        return Response({
            "deleted": count, "requested": len(ids), "permitted": count,
            "out_of_scope": skipped,
        })

    @action(detail=True, methods=["patch"], url_path="update_attendance")
    def update_attendance(self, request, pk=None):
        """PATCH /api/delegates/{id}/update_attendance/"""
        delegate   = self.get_object()
        attendance = request.data.get("attendance")
        choices    = dict(BookDelegate.Attendance.choices)
        if attendance not in choices:
            return Response({"detail": f"Invalid attendance: {attendance}"}, status=400)
        delegate.attendance = attendance
        delegate.save(update_fields=["attendance", "updated_at"])
        return Response({"id": delegate.id, "attendance": delegate.attendance})

    @action(detail=True, methods=["post"], url_path="transfer")
    def transfer(self, request, pk=None):
        """
        POST /api/delegates/{id}/transfer/  {"target_event_code", "invoice_number"}

        Move one delegate's credit to another event: the row transferred away
        becomes "Credit Transferred", and a NEW booking appears on the target event
        as "Paid (Transferred)". Both rows survive — the pair IS the audit trail, and
        it is the shape ~200 transfers already in the database take, done by hand in
        Zoho (book_delegate/tests_delegate_transfer.py pins the chain this mirrors).

        ONE ENDPOINT, NOT THREE CALLS
        A transfer is a create plus two updates. Driven from the browser as separate
        requests, a failure between them leaves a delegate credited on two events at
        once, or transferred away to nowhere. It is therefore one atomic action.

        WHAT IS NOT DERIVED HERE
        The target invoice number. Existing transfers use the destination event's own
        numbering (AIU25HOU-2804 → FAU25USA-2587), which this code has no way to
        generate, so the caller supplies it and the collision rules below decide
        whether it may be used.
        """
        from datetime import date
        from django.db.utils import IntegrityError
        from events.models import Event

        delegate = self.get_object()          # RBAC-scoped by rbac_filter_invoice
        source_invoice = delegate.invoice

        # Gated on create by the permission class (POST falls through to
        # can_create); the update half has to be asserted here. See
        # accounts/crm_permissions.has_module_action.
        if not has_module_action(request.user, "bookings", "update"):
            return Response(
                {"detail": "Transferring a booking also changes the booking it leaves, "
                           "which needs update permission on bookings."},
                status=status.HTTP_403_FORBIDDEN,
            )

        target_code = (request.data.get("target_event_code") or "").strip()
        new_number  = (request.data.get("invoice_number") or "").strip()
        if not target_code:
            return Response({"detail": "target_event_code is required."}, status=400)
        if not new_number:
            return Response({"detail": "invoice_number is required."}, status=400)

        target_event = Event.objects.filter(event_code=target_code).first()
        if target_event is None:
            return Response(
                {"detail": f"No event with code '{target_code}'."}, status=400)
        if target_code == source_invoice.event_code:
            return Response(
                {"detail": f"This booking is already on {target_code}."}, status=400)

        # An invoice number already in use may be REUSED, but only when it is the
        # same event — that is how a second delegate joins a transfer already made.
        # Anywhere else it would silently file this delegate under another event.
        existing = BookEvent.objects.filter(invoice_number=new_number).first()
        if existing is not None:
            if existing.event_code != target_code:
                return Response(
                    {"detail": f"Invoice {new_number} already exists on "
                               f"{existing.event_code}. Use a different number."},
                    status=409,
                )
            if existing.delegates.filter(email__iexact=delegate.email).exists():
                return Response(
                    {"detail": f"{delegate.email} is already on invoice {new_number}."},
                    status=409,
                )

        # The destination edition is the TARGET event's year, not the source's:
        # BookEvent.save() only derives an edition from trailing digits in the code,
        # and catalogue codes carry none, so an unset edition would stay null and the
        # booking would be missing from every per-edition report.
        target_edition = (
            target_event.event_date.year if target_event.event_date else source_invoice.edition
        )
        today = date.today()

        try:
            with transaction.atomic():
                if existing is None:
                    dest_invoice = BookEvent.objects.create(
                        invoice_number = new_number,
                        event_code     = target_code,
                        edition        = target_edition,
                        event_date     = target_event.event_date,
                        # Dates are the TRANSFER's, not the original booking's —
                        # matching the existing pairs, where the destination carries
                        # the date the transfer was made.
                        request_date   = today,
                        invoice_date   = today,
                        booking_code   = delegate.booking_code or source_invoice.booking_code,
                        company_name   = source_invoice.company_name,
                        contact_name   = source_invoice.contact_name,
                        contact_email  = source_invoice.contact_email,
                        contact_phone  = source_invoice.contact_phone,
                        accounts_contact_email = source_invoice.accounts_contact_email,
                        currency       = source_invoice.currency,
                        ticket_tier    = delegate.delegate_ticket_tier or source_invoice.ticket_tier,
                        payment_type   = delegate.delegate_payment_type or source_invoice.payment_type,
                        payment_date   = delegate.delegate_payment_date or source_invoice.payment_date,
                        paid_or_free   = delegate.delegate_paid_or_free or source_invoice.paid_or_free,
                        payment_status = BookEvent.PaymentStatus.PAID_TRANSFERRED,
                        # Created by hand in the CRM, whatever the original arrived as.
                        source         = BookEvent.Source.MANUAL,
                        sales_executive = BookEvent.auto_assign_sales(target_code),
                        # reference is NOT copied: in the existing pairs the
                        # destination carries only the "Transferred from" breadcrumb,
                        # never the source's payment reference, which belongs to the
                        # money received against the OTHER invoice.
                        #
                        # parent_code is left alone too. It is empty on all 11,042
                        # invoices and nothing reads it, so what it was meant to hold
                        # ("parent event code"? parent invoice?) is a guess — and a
                        # link recorded in the wrong field is worse than one recorded
                        # only in the references and the action log, as here.
                    )
                else:
                    dest_invoice = existing

                # Reusing someone else's invoice must not silently change what THIS
                # row promises. The invoice's own status is left alone — other
                # delegates are booked against it — so where it is not already
                # Paid (Transferred), this delegate carries the transferred status as
                # an override. Without this, joining a Pending invoice would land the
                # transfer as Pending, which is not what the transfer said it would do.
                dest_override = (
                    None
                    if dest_invoice.payment_status == BookEvent.PaymentStatus.PAID_TRANSFERRED
                    else BookEvent.PaymentStatus.PAID_TRANSFERRED
                )

                new_delegate = BookDelegate.objects.create(
                    invoice         = dest_invoice,
                    event_code      = target_code,
                    edition         = target_edition,
                    company         = delegate.company,
                    company_name_raw = delegate.company_name_raw,
                    first_name      = delegate.first_name,
                    last_name       = delegate.last_name,
                    email           = delegate.email,
                    phone_number    = delegate.phone_number,
                    position        = delegate.position,
                    ticket_package  = delegate.ticket_package,
                    sponsorship_level = delegate.sponsorship_level,
                    booking_code    = delegate.booking_code,
                    delegate_number = delegate.delegate_number,
                    discount        = delegate.discount,
                    add_ons         = delegate.add_ons,
                    dietary_requirements = delegate.dietary_requirements,
                    notes           = delegate.notes,
                    # A new event has its own door: nobody has attended it yet.
                    attendance      = BookDelegate.Attendance.PENDING,
                    reference       = _transferred_from(source_invoice.event_code,
                                                       source_invoice.edition),
                    # Normally None: a freshly created destination invoice carries
                    # Paid (Transferred) itself, so the resolved status reads from one
                    # place. See dest_override above for the reuse case.
                    delegate_payment_status = dest_override,
                )

                # ── The row transferred away ─────────────────────────────────
                # Where it is the invoice's only delegate the status belongs ON the
                # invoice (which is what the existing transferred pairs look like,
                # and what every report reading invoice.payment_status sees). With
                # siblings still booked, only this person moved, so the status has to
                # be a per-delegate override or it would relabel their bookings too.
                sibling_count = source_invoice.delegates.exclude(pk=delegate.pk).count()
                if sibling_count == 0:
                    source_invoice.payment_status = BookEvent.PaymentStatus.CREDIT_TRANSFERRED
                    # Cleared, not left: an override would shadow the invoice value
                    # and the row would still read as whatever it was before.
                    delegate.delegate_payment_status = None
                    scope = "invoice"
                else:
                    delegate.delegate_payment_status = BookEvent.PaymentStatus.CREDIT_TRANSFERRED
                    scope = "delegate"

                delegate.reference = _append_reference(
                    delegate.reference, _transferred_to(target_code, target_edition))
                delegate.save(update_fields=[
                    "delegate_payment_status", "reference", "updated_at",
                ])
                source_invoice.updated_by = request.user
                source_invoice.save()

                from accounts.models import ActionLog
                ActionLog.objects.create(
                    user=request.user,
                    action=f"Transferred delegate {delegate.email} to {target_code}",
                    details=(
                        f"from invoice {source_invoice.invoice_number} "
                        f"({source_invoice.event_code}) -> {dest_invoice.invoice_number} "
                        f"({target_code}); source scope={scope}; "
                        f"new delegate id={new_delegate.id}"
                    ),
                )
        except IntegrityError as exc:
            # The uniqueness checks above are not a lock: a concurrent transfer can
            # take the number in between. Reported as a conflict rather than a 500.
            logger.warning("transfer collision for %s: %s", new_number, exc)
            return Response(
                {"detail": f"Invoice {new_number} was just taken. Try another number."},
                status=409,
            )

        return Response({
            "source": {
                "delegate_id": delegate.id,
                "invoice_number": source_invoice.invoice_number,
                "event_code": source_invoice.event_code,
                "payment_status": BookEvent.PaymentStatus.CREDIT_TRANSFERRED,
                "scope": scope,
            },
            "created": {
                "delegate_id": new_delegate.id,
                "invoice_id": dest_invoice.id,
                "invoice_number": dest_invoice.invoice_number,
                "event_code": dest_invoice.event_code,
                "payment_status": BookEvent.PaymentStatus.PAID_TRANSFERRED,
                "reused_invoice": existing is not None,
            },
        }, status=status.HTTP_201_CREATED)
