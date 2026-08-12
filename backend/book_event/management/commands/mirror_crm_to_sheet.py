"""
Mirror every CRM module into the "CRM data" spreadsheet, one tab per module.

Runs nightly at 05:30 IST via CRONJOBS (settings.py), and on demand from the
Google Sync page's "Sheet sync" button. Both paths go through SyncOrchestrator,
so a run started here is visible in the sync history and holds the same lock
that blocks a concurrent bookings/events push.
"""
from django.core.management.base import BaseCommand

from google_sync.models import GoogleSheetSyncLog
from google_sync.services import SyncOrchestrator


class Command(BaseCommand):
    help = "Full-replace mirror of all CRM modules into the 'CRM data' Google Sheet"

    def add_arguments(self, parser):
        parser.add_argument(
            "--tab",
            action="append",
            dest="tabs",
            help="Mirror only this tab (repeatable). Defaults to every module.",
        )

    def handle(self, *args, **options):
        tabs = options.get("tabs")

        if tabs:
            # Single-tab runs bypass the orchestrator: they're a debugging aid,
            # not a scheduled sync, and shouldn't land in the sync history.
            from sync.crm_mirror import CRM_MODULES, mirror_all

            selected = [m for m in CRM_MODULES if m[0] in set(tabs)]
            unknown = set(tabs) - {m[0] for m in CRM_MODULES}
            if unknown:
                self.stderr.write(self.style.ERROR(
                    f"Unknown tab(s): {', '.join(sorted(unknown))}. "
                    f"Valid: {', '.join(m[0] for m in CRM_MODULES)}"
                ))
                return

            summary, errors = mirror_all(modules=selected)
            self._report(summary, errors)
            return

        try:
            log = SyncOrchestrator.run(
                sync_type=GoogleSheetSyncLog.SyncType.CRM_MIRROR,
                triggered_by="cron",
                trigger_source=GoogleSheetSyncLog.TriggerSource.SCHEDULER,
            )
        except RuntimeError as exc:
            # Another sync holds the lock — expected under CRONTAB_LOCK_JOBS.
            self.stdout.write(self.style.WARNING(str(exc)))
            return

        summary = (log.sync_summary or {}).get("tabs", {})
        errors = [e for e in (log.error_message or "").split("\n") if e]
        self._report(summary, errors, duration=log.duration_seconds)

    def _report(self, summary, errors, duration=None):
        for tab, count in summary.items():
            self.stdout.write(f"  {tab:<22} {count:>7,} rows")

        for err in errors:
            self.stderr.write(self.style.ERROR(f"  {err}"))

        total = sum(summary.values())
        tail = f" in {duration:.1f}s" if duration else ""

        if not summary:
            self.stderr.write(self.style.ERROR(f"CRM mirror wrote nothing{tail}."))
            return

        line = f"Mirrored {total:,} rows across {len(summary)} tab(s){tail}"
        style = self.style.WARNING if errors else self.style.SUCCESS
        self.stdout.write(style(line + (f" — {len(errors)} tab(s) failed" if errors else "")))
