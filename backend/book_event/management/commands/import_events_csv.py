import csv
import os
from datetime import datetime
from django.core.management.base import BaseCommand
from django.db import transaction
from events.models import Event
from accounts.models import User

class Command(BaseCommand):
    help = "Import events from C:\\Users\\harrison peck\\Downloads\\Events.csv"

    def handle(self, *args, **options):
        file_path = r"C:\Users\harrison peck\Downloads\Events.csv"
        if not os.path.exists(file_path):
            self.stdout.write(self.style.ERROR(f"File not found: {file_path}"))
            return

        with open(file_path, mode='r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            
            success_count = 0
            error_count = 0
            
            # Clear old data to ensure matching state
            self.stdout.write("Clearing existing events and assignments...")
            Event.objects.all().delete()
            
            for row in reader:
                event_code = row.get('Event Code', '').strip()
                if not event_code:
                    continue

                try:
                    with transaction.atomic():
                        # 1. Parse Dates
                        def parse_date(d):
                            if not d or d.lower() == 'nan': return None
                            try: return datetime.strptime(d, "%d-%b-%Y").date()
                            except: return None

                        start_date = parse_date(row.get('Start Date'))
                        end_date = parse_date(row.get('End Date'))

                        # 2. Create/Update Event
                        event, created = Event.objects.update_or_create(
                            event_code=event_code,
                            defaults={
                                'name': row.get('Event Name', '').strip(),
                                'event_date': start_date or datetime.now().date(),
                                'end_date': end_date,
                            }
                        )

                        # 3. Handle Teams
                        # Speaker Sales is merged into SCA, so the sheet ships one
                        # column for both; 'Sales Team' stays readable for older files.
                        team_mapping = {
                            'SCA': User.Team.SALES,
                            'Sales Team': User.Team.SALES,
                            'SpEx Team': User.Team.SPEX,
                            'Tele Marketing Team': User.Team.TELE_MARKET,
                            'Market Research Team': User.Team.MARKET_RESEARCH,
                        }

                        for col, team_val in team_mapping.items():
                            names_str = row.get(col, '').strip()
                            if names_str:
                                names = [n.strip() for n in names_str.split(',') if n.strip()]
                                for name in names:
                                    # Create or get user
                                    username = name.lower().replace(" ", ".")
                                    user, u_created = User.objects.get_or_create(
                                        username=username,
                                        defaults={
                                            'first_name': name.split(' ')[0],
                                            'last_name': ' '.join(name.split(' ')[1:]) if ' ' in name else '',
                                            'team': team_val,
                                            'role': User.Role.SALES
                                        }
                                    )
                                    # Ensure team is correct if user already exists
                                    if not u_created:
                                        user.team = team_val
                                        user.save()
                                    
                                    # Assign to event
                                    event.assigned_users.add(user)

                        success_count += 1
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Error on {event_code}: {e}"))
                    error_count += 1

            self.stdout.write(self.style.SUCCESS(f"Finished Events Import. Success: {success_count}, Errors: {error_count}"))
