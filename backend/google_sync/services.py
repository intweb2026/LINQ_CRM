"""
google_sync/services.py
────────────────────────
SyncOrchestrator: wraps existing sync functions with full per-run audit logging.
Preserves 100% of existing sync behavior — just adds logging around it.
"""
import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from book_event.models import SyncLog
from .models import GoogleSheetSyncLog, SheetSyncTarget

logger = logging.getLogger("book_event")

# Share the same lock as the management command so both respect each other
SYNC_LOCK_KEY = "sync_to_sheets_lock"
SYNC_LOCK_TTL = 3600  # 1 hour max


class SyncOrchestrator:

    @classmethod
    def is_running(cls) -> bool:
        return bool(cache.get(SYNC_LOCK_KEY))

    @classmethod
    def run(
        cls,
        sync_type: str,
        full: bool = False,
        triggered_by: str = "",
        trigger_source: str = GoogleSheetSyncLog.TriggerSource.SYSTEM,
    ) -> GoogleSheetSyncLog:
        """
        Acquire lock, execute sync, release lock.
        Returns the completed GoogleSheetSyncLog.
        Raises RuntimeError if already locked.
        """
        if not cache.add(SYNC_LOCK_KEY, "true", SYNC_LOCK_TTL):
            raise RuntimeError(
                "A sync is already in progress. Please wait for it to complete before starting another."
            )
        try:
            return cls._execute(sync_type, full, triggered_by, trigger_source)
        finally:
            cache.delete(SYNC_LOCK_KEY)

    @classmethod
    def run_target(
        cls,
        target: SheetSyncTarget,
        triggered_by: str = "",
        trigger_source: str = GoogleSheetSyncLog.TriggerSource.SYSTEM,
    ) -> GoogleSheetSyncLog:
        """
        Run one user-defined target under the same lock as every other sync.

        Sharing the lock matters because a target and the CRM mirror can be
        pointed at the same spreadsheet, and the Sheets API is the scarce thing
        here, not the database.
        """
        if not cache.add(SYNC_LOCK_KEY, "true", SYNC_LOCK_TTL):
            raise RuntimeError(
                "A sync is already in progress. Please wait for it to complete "
                "before starting another."
            )
        try:
            return cls._execute_sheet_target(target, triggered_by, trigger_source)
        finally:
            cache.delete(SYNC_LOCK_KEY)

    @classmethod
    def _execute_sheet_target(cls, target, triggered_by, trigger_source):
        """
        Full-replace one tab with one module's selected columns.

        Always a full replace, so sync_mode is FULL. A target names a set of
        columns rather than a set of rows, so there is nothing for an
        incremental pass to carry forward.
        """
        from services.google_sheets import GoogleSheetsService
        from sync.catalog import build_rows

        log = GoogleSheetSyncLog.objects.create(
            sync_type=GoogleSheetSyncLog.SyncType.SHEET_TARGET,
            sheet_name=target.tab_name,
            status=GoogleSheetSyncLog.Status.RUNNING,
            sync_mode=GoogleSheetSyncLog.SyncMode.FULL,
            triggered_by=triggered_by,
            trigger_source=trigger_source,
            started_at=timezone.now(),
        )

        start = time.time()
        summary = {
            "target":         target.name,
            "module":         target.module,
            "columns":        list(target.columns),
            "spreadsheet_id": target.spreadsheet_id,
            "tab":            target.tab_name,
        }

        try:
            headers, rows = build_rows(target.module, target.columns)
            service = GoogleSheetsService(spreadsheet_id=target.spreadsheet_id)
            service.ensure_tabs([target.tab_name])
            count = service.replace_data_chunked(target.tab_name, headers, rows)
        except Exception as exc:
            err = str(exc)
            logger.error(
                "Sheet target [%s] failed: %s", target.name, err, exc_info=True
            )
            log.status           = GoogleSheetSyncLog.Status.FAILED
            log.completed_at     = timezone.now()
            log.duration_seconds = round(time.time() - start, 2)
            log.error_message    = err
            log.sync_summary     = summary
            log.save()

            target.last_status = SheetSyncTarget.Status.FAILED
            target.last_error  = err
            target.save(update_fields=["last_status", "last_error", "updated_at"])
            return log

        summary["headers"] = headers
        log.status            = GoogleSheetSyncLog.Status.SUCCESS
        log.completed_at      = timezone.now()
        log.duration_seconds  = round(time.time() - start, 2)
        log.records_processed = count
        log.error_message     = ""
        log.sync_summary      = summary
        log.last_synced_at    = timezone.now()
        log.save()

        target.last_status     = SheetSyncTarget.Status.SUCCESS
        target.last_error      = ""
        target.last_synced_at  = timezone.now()
        target.records_synced  = count
        target.save(update_fields=[
            "last_status", "last_error", "last_synced_at", "records_synced",
            "updated_at",
        ])

        logger.info(
            "Sheet target [%s] wrote %d rows x %d cols to %s in %.2fs",
            target.name, count, len(headers), target.tab_name, log.duration_seconds,
        )
        return log

    @classmethod
    def _execute(cls, sync_type, full, triggered_by, trigger_source):
        from sync.bookings_sync import sync_bookings
        from sync.events_sync import sync_events
        from services.google_sheets import google_sheets as gs

        # The CRM mirror writes to its own spreadsheet and builds its own client,
        # so it bypasses the `gs` singleton (and its single GOOGLE_SHEET_ID)
        # entirely. Handled before the checks below for that reason.
        if sync_type == GoogleSheetSyncLog.SyncType.CRM_MIRROR:
            return cls._execute_crm_mirror(triggered_by, trigger_source)

        sync_mode = (
            GoogleSheetSyncLog.SyncMode.FULL
            if full
            else GoogleSheetSyncLog.SyncMode.INCREMENTAL
        )

        sheet_label = cls._sheet_label(sync_type)

        log = GoogleSheetSyncLog.objects.create(
            sync_type=sync_type,
            sheet_name=sheet_label,
            status=GoogleSheetSyncLog.Status.RUNNING,
            sync_mode=sync_mode,
            triggered_by=triggered_by,
            trigger_source=trigger_source,
            started_at=timezone.now(),
        )

        if not gs:
            log.status = GoogleSheetSyncLog.Status.FAILED
            log.completed_at = timezone.now()
            log.duration_seconds = 0
            log.error_message = (
                "Google Sheets service is not initialised. "
                "Check GOOGLE_SHEETS_CREDENTIALS and GOOGLE_SHEET_ID settings."
            )
            log.save()
            return log

        start = time.time()
        errors = []
        total_processed = 0
        summary = {}

        try:
            if sync_type in ("bookings", "full_sync"):
                sync_bookings(full=full)
                bl = SyncLog.objects.filter(dataset="bookings").first()
                if bl:
                    if bl.last_status == SyncLog.Status.FAILED:
                        errors.append(f"Bookings: {bl.error_message}")
                    else:
                        total_processed += bl.records_synced
                        summary["bookings"] = {
                            "records": bl.records_synced,
                            "last_synced_at": str(bl.last_synced_at),
                        }

            if sync_type in ("events", "full_sync"):
                sync_events(full=full)
                el = SyncLog.objects.filter(dataset="events").first()
                if el:
                    if el.last_status == SyncLog.Status.FAILED:
                        errors.append(f"Events: {el.error_message}")
                    else:
                        total_processed += el.records_synced
                        summary["events"] = {
                            "records": el.records_synced,
                            "last_synced_at": str(el.last_synced_at),
                        }

            duration = time.time() - start

            if errors and total_processed == 0:
                final_status = GoogleSheetSyncLog.Status.FAILED
            elif errors:
                final_status = GoogleSheetSyncLog.Status.PARTIAL
            else:
                final_status = GoogleSheetSyncLog.Status.SUCCESS

            log.status            = final_status
            log.completed_at      = timezone.now()
            log.duration_seconds  = round(duration, 2)
            log.records_processed = total_processed
            log.error_message     = "\n".join(errors)
            log.sync_summary      = summary
            log.last_synced_at    = timezone.now() if final_status == GoogleSheetSyncLog.Status.SUCCESS else None
            log.save()

            logger.info(
                "GoogleSync [%s] %s in %.2fs — %d records",
                sync_type, final_status, duration, total_processed,
            )

        except Exception as exc:
            duration = time.time() - start
            err = str(exc)
            logger.error("GoogleSync [%s] unhandled exception: %s", sync_type, err, exc_info=True)
            log.status           = GoogleSheetSyncLog.Status.FAILED
            log.completed_at     = timezone.now()
            log.duration_seconds = round(duration, 2)
            log.error_message    = err
            log.save()

        return log

    @classmethod
    def _execute_crm_mirror(cls, triggered_by, trigger_source):
        """
        Full replace of every CRM module into the 'CRM data' spreadsheet.

        Always a full replace, so sync_mode is FULL regardless of the caller's
        `full` flag — there is no incremental mode for a mirror.
        """
        from sync.crm_mirror import mirror_all, CRM_MODULES

        log = GoogleSheetSyncLog.objects.create(
            sync_type=GoogleSheetSyncLog.SyncType.CRM_MIRROR,
            sheet_name=cls._sheet_label(GoogleSheetSyncLog.SyncType.CRM_MIRROR),
            status=GoogleSheetSyncLog.Status.RUNNING,
            sync_mode=GoogleSheetSyncLog.SyncMode.FULL,
            triggered_by=triggered_by,
            trigger_source=trigger_source,
            started_at=timezone.now(),
        )

        start = time.time()
        try:
            summary, errors = mirror_all()
        except Exception as exc:
            logger.error("CRM mirror aborted: %s", exc, exc_info=True)
            log.status           = GoogleSheetSyncLog.Status.FAILED
            log.completed_at     = timezone.now()
            log.duration_seconds = round(time.time() - start, 2)
            log.error_message    = str(exc)
            log.save()
            return log

        total = sum(summary.values())

        if errors and not summary:
            final_status = GoogleSheetSyncLog.Status.FAILED
        elif errors:
            final_status = GoogleSheetSyncLog.Status.PARTIAL
        else:
            final_status = GoogleSheetSyncLog.Status.SUCCESS

        log.status            = final_status
        log.completed_at      = timezone.now()
        log.duration_seconds  = round(time.time() - start, 2)
        log.records_processed = total
        log.error_message     = "\n".join(errors)
        log.sync_summary      = {"tabs": summary, "modules": len(CRM_MODULES)}
        log.last_synced_at    = timezone.now() if final_status == GoogleSheetSyncLog.Status.SUCCESS else None
        log.save()

        logger.info(
            "CRM mirror %s in %.2fs — %d rows across %d tabs",
            final_status, log.duration_seconds, total, len(summary),
        )
        return log

    @staticmethod
    def _sheet_label(sync_type: str) -> str:
        bookings_tab = getattr(settings, "GOOGLE_SHEET_BOOKINGS_TAB", "Bookings")
        events_tab   = getattr(settings, "GOOGLE_SHEET_EVENTS_TAB",   "Events")
        return {
            "bookings":   bookings_tab,
            "events":     events_tab,
            "full_sync":  f"{bookings_tab} + {events_tab}",
            "crm_mirror": "CRM data (all modules)",
        }.get(sync_type, "")
