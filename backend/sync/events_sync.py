from django.utils import timezone
from django.conf import settings
from events.models import Event
from book_event.models import SyncLog
from services.google_sheets import google_sheets
import logging

logger = logging.getLogger('book_event')

def sync_events(full=False):
    if not google_sheets:
        return

    # 1. Get sync log
    log, _ = SyncLog.objects.get_or_create(dataset="events")
    last_sync = log.last_synced_at if not full else None

    try:
        # 2. Query data
        query = Event.objects.all().order_by('updated_at')
        if last_sync:
            query = query.filter(updated_at__gt=last_sync)
        
        query = query.prefetch_related("assigned_users")
        
        events = list(query)
        if not events and not full:
            return

        # 3. Transform
        headers = [
            "ID", "Event Name", "Event Code", "Event Date", "Event Status",
            "SCA", "SpEx Team", "Tele Marketing Team", "Market Research Team",
            "City", "Country", "Venue", "End Date", "Capacity", "Expected Revenue"
        ]
        
        rows = []
        for e in events:
            assigned = list(e.assigned_users.all())
            
            def get_team_str(team_val):
                return ", ".join(u.username for u in assigned if u.team == team_val)

            rows.append([
                e.id,
                e.name,
                e.event_code,
                str(e.event_date) if e.event_date else "",
                e.event_status,
                # Speaker Sales is merged into SCA, so both team values land in
                # the one column rather than the sheet keeping a dead one.
                get_team_str("sales") or get_team_str("speaker_sales"),
                get_team_str("spex"),
                get_team_str("tele_market"),
                get_team_str("market_research"),
                e.city,
                e.country,
                e.venue,
                str(e.end_date) if e.end_date else "",
                e.capacity,
                float(e.expected_revenue)
            ])

        # 4. Push
        if full:
            count = google_sheets.replace_data(settings.GOOGLE_SHEET_EVENTS_TAB, headers, rows)
        else:
            count = google_sheets.sync_data(settings.GOOGLE_SHEET_EVENTS_TAB, headers, rows)
        
        # 5. Success update
        log.last_synced_at = timezone.now()
        log.last_status = SyncLog.Status.SUCCESS
        log.records_synced = count
        log.error_message = ""
        log.save()
        logger.info(f"Successfully synced {count} events (Full={full}).")

    except Exception as e:
        log.last_status = SyncLog.Status.FAILED
        log.error_message = str(e)
        log.save()
        logger.error(f"Failed to sync events: {e}")
