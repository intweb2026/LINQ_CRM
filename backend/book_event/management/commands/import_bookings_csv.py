import csv
import os
from django.core.management.base import BaseCommand
from django.utils.timezone import make_aware
from django.db import transaction, models
from accounts.import_common import parse_import_date, parse_import_datetime
from book_event.models import BookEvent
from book_delegate.models import BookDelegate
from accounts.models import User
from companies.models import Company
from events.models import Event

class Command(BaseCommand):
    help = "Import bookings from C:\\Users\\harrison peck\\Downloads\\Event Bookings Report (1).csv"

    def handle(self, *args, **options):
        file_path = r"C:\Users\harrison peck\Downloads\Event Bookings Report (1).csv"
        if not os.path.exists(file_path):
            self.stdout.write(self.style.ERROR(f"File not found: {file_path}"))
            return

        # Clear existing data to ensure matching counts
        self.stdout.write("Clearing existing bookings and delegates...")
        BookDelegate.objects.all().delete()
        BookEvent.objects.all().delete()

        with open(file_path, mode='r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            
            success_count = 0
            error_count = 0
            # Every date this run could not read. An unreadable date does NOT
            # fail its row — the booking and its delegates are worth more than
            # one column — but it is summarised at the end so a bad date column
            # cannot pass for an empty one.
            date_warnings = []
            
            for row in reader:
                invoice_num = row.get('Invoice Number', '').strip()
                if not invoice_num:
                    continue

                try:
                    with transaction.atomic():
                        # 1. Handle Sales Executive
                        sales_name = row.get('Sales Executive', '').strip()
                        sales_user = None
                        if sales_name:
                            _parts = sales_name.split()
                            sales_user = (
                                (User.objects.filter(
                                    first_name__iexact=_parts[0],
                                    last_name__iexact=" ".join(_parts[1:]),
                                ).first() if len(_parts) >= 2 else None)
                                or (User.objects.filter(
                                    first_name__icontains=_parts[0],
                                    last_name__icontains=_parts[-1],
                                ).first() if len(_parts) >= 2 else None)
                                or User.objects.filter(
                                    username__iexact=sales_name.replace(" ", ".").lower()
                                ).first()
                                or User.objects.filter(first_name__iexact=sales_name).first()
                                or User.objects.filter(last_name__iexact=sales_name).first()
                            )

                        # 2. Parse Dates and Numbers
                        # Both of these used to accept exactly ONE format,
                        # "%d-%b-%Y", and return None for anything else — so a
                        # column of dates written any other way imported as a
                        # column of blanks, indistinguishable from a column the
                        # source left empty. They now go through
                        # accounts.import_common, this codebase's single
                        # authority on reading a date out of an import file, and
                        # a value that still cannot be read is REPORTED rather
                        # than silently dropped.
                        def parse_date(d):
                            parsed, error = parse_import_date(d)
                            if error:
                                date_warnings.append(error)
                            return parsed

                        def parse_datetime(dt):
                            parsed, error = parse_import_datetime(dt)
                            if error:
                                date_warnings.append(error)
                            # Naive by contract; the CSV is written in local time.
                            return make_aware(parsed) if parsed else None

                        def parse_float(val):
                            if not val: return 0.0
                            val = val.strip().replace('%', '').replace(',', '')
                            try: return float(val)
                            except: return 0.0

                        # 3. Create/Update BookEvent
                        raw_status = row.get('Payment Status', 'Pending').strip()
                        
                        event_defaults = {
                            'event_code': row.get('Event Code', '').strip(),
                            'event_name': row.get('Event Name', '').strip(),
                            'booking_code': row.get('Booking Code', '').strip(),
                            'invoice_date': parse_date(row.get('Invoice Date')),
                            'contact_name': row.get('Name', '').strip(),
                            'company_name': row.get('Delegate Company', '').strip(),
                            'contact_email': row.get('Delegate Email', '').strip(),
                            'contact_phone': row.get('Direct Line', '').strip(),
                            'accounts_contact_email': row.get('Accounts Contact', '').strip(),
                            'payment_status': raw_status,
                            'payment_date': parse_date(row.get('Date Paid')),
                            'payment_type': row.get('Payment Type', '').strip(),
                            'ticket_tier': row.get('Ticket Tier', '').strip(),
                            'discount': parse_float(row.get('Discount')),
                            'paid_free': row.get('Paid/Free', '').strip(),
                            'add_ons': row.get('Add-Ons', '').strip(),
                            'reference': row.get('Ref', '').strip(),
                            'sales_executive': sales_user,
                        }
                        
                        added_time = parse_datetime(row.get('Added Time'))
                        if added_time:
                            event_defaults['created_at'] = added_time

                        be, created = BookEvent.objects.get_or_create(
                            invoice_number=invoice_num,
                            defaults=event_defaults
                        )

                        # 4. Handle Delegate
                        email = row.get('Delegate Email', '').strip().lower()
                        if email:
                            co_name = row.get('Delegate Company', '').strip()
                            company = None
                            if co_name:
                                company, _ = Company.objects.get_or_create(name=co_name)

                            name_parts = row.get('Name', '').strip().split(' ', 1)
                            first_name = name_parts[0]
                            last_name = name_parts[1] if len(name_parts) > 1 else ""

                            BookDelegate.objects.create(
                                invoice=be,
                                email=email,
                                event_code=be.event_code,
                                company=company,
                                company_name_raw=co_name,
                                first_name=first_name,
                                last_name=last_name,
                                phone_number=row.get('Direct Line', '').strip(),
                                delegate_number=int(row.get('Delegate Number', 1) or 1),
                                attendance=BookDelegate.Attendance.CONFIRMED if row.get('Attendance - IN?') == 'true' else BookDelegate.Attendance.PENDING
                            )

                        success_count += 1
                        if success_count % 500 == 0:
                            self.stdout.write(f"Processed {success_count}...")

                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Error on {invoice_num}: {e}"))
                    error_count += 1

            self.stdout.write(self.style.SUCCESS(f"Finished. Success: {success_count}, Errors: {error_count}"))

            if date_warnings:
                self.stdout.write(self.style.WARNING(
                    f"{len(date_warnings)} date value(s) could not be read and were "
                    f"stored as empty. Distinct values:"
                ))
                for value in sorted(set(date_warnings))[:20]:
                    self.stdout.write(self.style.WARNING(f"  {value}"))
                distinct = len(set(date_warnings))
                if distinct > 20:
                    self.stdout.write(self.style.WARNING(
                        f"  ... and {distinct - 20} more distinct value(s)."
                    ))
