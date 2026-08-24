import json
import os
from django.core.management.base import BaseCommand
from django.utils import timezone
from webhooks.models import WebhookLog
from webhooks.services import WebhookProcessor

class Command(BaseCommand):
    """
    DEPRECATED FOR BULK LOADS — use `load_zoho_export` instead.

    Kept because it is the only path that replays rows through WebhookProcessor,
    which is occasionally what you want for reprocessing a handful of webhook
    payloads. It is the WRONG tool for the Zoho load, on four counts:

      * NO INVOICE NUMBER POLICY CONFLICT. This command SKIPS a row with no
        invoice number (see the `continue` below). Assumption A2 says the load
        must GENERATE one. Running both against the same file therefore produces
        different row counts, and this one loses those rows silently — the skip
        is only printed for every hundredth row.
      * Not atomic. Each row is processed independently, so a failure part-way
        leaves everything before it committed.
      * No import_batch_id, so its rows cannot be identified or rolled back
        without a full restore.
      * Writes one WebhookLog per row — 35,690 extra rows in a table that is
        already 130k, for an import that is not a webhook.

    `load_zoho_export` has a --dry-run, one transaction for the whole load, a
    batch id, an ActionLog, bounded date/edition parsing and anchored event-code
    resolution. Reach for that.
    """

    help = ("DEPRECATED for bulk loads (use load_zoho_export). Replays bookings "
            "from a JSON file through WebhookProcessor, one WebhookLog per row.")

    def add_arguments(self, parser):
        parser.add_argument('json_file', type=str, help='Path to the JSON file')

    def handle(self, *args, **options):
        file_path = options['json_file']
        if not os.path.exists(file_path):
            self.stdout.write(self.style.ERROR(f"File not found: {file_path}"))
            return

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                raw_data = json.load(f)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to parse JSON: {e}"))
            return

        # Handle "Event_Bookings_Report" wrapping or plain list
        if isinstance(raw_data, dict) and "Event_Bookings_Report" in raw_data:
            data = raw_data["Event_Bookings_Report"]
        elif isinstance(raw_data, list):
            data = raw_data
        else:
            self.stdout.write(self.style.ERROR("Invalid JSON structure. Expected a list or {'Event_Bookings_Report': [...]}"))
            return

        total = len(data)
        self.stdout.write(f"Starting bulk import of {total} records...")

        success_count = 0
        update_count = 0
        fail_count = 0

        for i, item in enumerate(data):
            # Mapping for Zoho Report format vs standard format
            invoice_number = (item.get("Invoice_Number.Invoice_Number") or item.get("InvoiceNumber") or "").strip()
            event_code = (item.get("Event_Name.Event_Code_with_Year") or item.get("Eventcode") or "UNKNOWN").strip()
            
            if not invoice_number:
                if total < 100 or i % 100 == 0:
                    self.stdout.write(self.style.WARNING(f"[{i+1}/{total}] Skipped: No invoice number"))
                continue

            # Construct the payload for WebsiteBookingSerializer
            payload = {
                "InvoiceNumber": invoice_number,
                "Eventcode": event_code or "UNKNOWN",
                "Eventname": item.get("Event_Name") or item.get("Eventname", ""),
                # Handed over RAW. This used to be pre-normalised here by a parser that
                # accepted only "%d-%b-%Y" and returned None for everything else,
                # which THREW AWAY dates that the receiving end can read perfectly
                # well — WebhookProcessor.parse_webhook_date now goes through
                # accounts.import_common.parse_import_date, and it reports what it
                # cannot read instead of silently blanking it.
                "Date": item.get("Invoice_Number.Invoice_Date") or item.get("Date") or "",
                "DelegateCompanyName": item.get("Sub_Company") or item.get("DelegateCompanyName", ""),
                "AccountsContactEmail": item.get("Account_Emails") or item.get("AccountsContactEmail", ""),
                "PaymentStatus": item.get("Status") or item.get("PaymentStatus", ""),
                "PaymentType": item.get("Payment_Type") or item.get("PaymentType", ""),
                "PaidOrFree": item.get("Booking_code_type") or item.get("PaidOrFree", ""),
                "TicketTier": item.get("Ticket_Tier") or item.get("TicketTier", ""),
                "booking_code": item.get("Packages") or item.get("Booking_code") or item.get("Ticket_Tier") or "",
                "Discount": item.get("Discount") or "0",
                "Delegates": [
                    {
                        "FirstName": item.get("Name") or item.get("FirstName", ""),
                        "Email": item.get("Delegate_Email") or item.get("Email", ""),
                        "PhoneNumber": item.get("Direct_Line") or item.get("PhoneNumber", ""),
                        "TicketTier": item.get("Ticket_Tier") or item.get("TicketTier", ""),
                        "PaidOrFree": item.get("Booking_code_type") or item.get("PaidOrFree", ""),
                    }
                ]
            }

            try:
                # Create a WebhookLog for audit/traceability
                log = WebhookLog.objects.create(
                    source="bulk_import_json",
                    payload=payload,
                    status=WebhookLog.Status.RECEIVED,
                    processing_status=WebhookLog.ProcessingStatus.PENDING,
                    invoice_number=invoice_number,
                    event_code=event_code or "UNKNOWN",
                    received_at=timezone.now(),
                )

                processor = WebhookProcessor(log)
                success, result = processor.process()

                if success:
                    if result.get('db_action') == 'updated':
                        update_count += 1
                        if total < 100 or i % 100 == 0:
                            self.stdout.write(self.style.SUCCESS(f"[{i+1}/{total}] UPDATED: {invoice_number}"))
                    else:
                        success_count += 1
                        if total < 100 or i % 100 == 0:
                            self.stdout.write(self.style.SUCCESS(f"[{i+1}/{total}] CREATED: {invoice_number}"))
                else:
                    fail_count += 1
                    self.stdout.write(self.style.ERROR(f"[{i+1}/{total}] FAILED: {invoice_number} - {result.get('detail') or result.get('errors')}"))

            except Exception as e:
                fail_count += 1
                self.stdout.write(self.style.ERROR(f"[{i+1}/{total}] EXCEPTION: {invoice_number} - {e}"))

        self.stdout.write(self.style.SUCCESS(
            f"Finished. Created: {success_count}, Updated: {update_count}, Failed: {fail_count}"
        ))
