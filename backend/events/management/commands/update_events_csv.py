import csv
import os
from django.core.management.base import BaseCommand
from events.models import Event
from accounts.models import User
from django.db import models
from datetime import datetime

class Command(BaseCommand):
    help = "Update events from C:\\Users\\harrison peck\\Downloads\\Events.csv"

    def handle(self, *args, **options):
        file_path = r"C:\Users\harrison peck\Downloads\Events.csv"
        
        if not os.path.exists(file_path):
            self.stdout.write(self.style.ERROR(f"File not found: {file_path}"))
            return

        try:
            with open(file_path, mode='r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                updated_count = 0
                
                for row in reader:
                    event_code = row.get('Event Code')
                    if not event_code:
                        continue
                    
                    event_code = str(event_code).strip().upper()
                    
                    try:
                        event = Event.objects.get(event_code=event_code)
                    except Event.DoesNotExist:
                        # Skip if not found (matching previous logic)
                        continue

                    # Update Basic Info
                    if row.get('Event Name'):
                        event.name = row['Event Name'].strip()
                    
                    if row.get('Official Name'):
                        event.official_name = row['Official Name'].strip()

                    # Dates
                    def parse_date(date_str):
                        if not date_str: return None
                        # Expected format: 03-Feb-2025
                        try:
                            return datetime.strptime(date_str.strip(), "%d-%b-%Y").date()
                        except:
                            return None

                    start_date = parse_date(row.get('Start Date'))
                    if start_date:
                        event.event_date = start_date
                    
                    end_date = parse_date(row.get('End Date'))
                    if end_date:
                        event.end_date = end_date

                    # SCA (Primary ForeignKey mapping). The sheet still ships
                    # the column under its old header, so both spellings are read.
                    sales_name = str(row.get('SCA', '') or row.get('Sales Team', '')).strip()
                    if sales_name:
                        user = User.objects.filter(role='sales').filter(
                            models.Q(first_name__icontains=sales_name) | 
                            models.Q(last_name__icontains=sales_name) |
                            models.Q(username__icontains=sales_name.replace(" ", ".").lower())
                        ).first()
                        if user:
                            event.sales_executive = user

                    # Team & Checks (String Fields)
                    event.spex_team            = row.get('SpEx Team', '').strip()
                    event.tele_marketing_team  = row.get('Tele Marketing Team', '').strip()
                    event.market_research_team = row.get('Market Research Team', '').strip()
                    event.content_check        = row.get('Content Check', '').strip()
                    event.marketing_check      = row.get('Marketing Check', '').strip()
                    event.sales_check          = row.get('Sales Check', '').strip()
                    
                    # Boolean Field
                    web_bookings = str(row.get('Accepting Web Bookings', '')).lower().strip()
                    event.accepting_web_bookings = (web_bookings == 'true')

                    event.save()

                    # Access Users (M2M)
                    access_emails_str = row.get('Access User', '')
                    if access_emails_str:
                        access_emails = [e.strip().lower() for e in access_emails_str.split(',') if e.strip()]
                        user_ids = list(User.objects.filter(email__in=access_emails).values_list('id', flat=True))
                        if user_ids:
                            event.assigned_users.set(user_ids)

                    updated_count += 1
                    self.stdout.write(f"Updated {event_code}")

                self.stdout.write(self.style.SUCCESS(f"Successfully updated {updated_count} events from CSV."))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error processing CSV: {e}"))
