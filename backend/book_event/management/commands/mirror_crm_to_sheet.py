"""
Mirror every CRM module into the "CRM data" spreadsheet, one tab per module.

Runs nightly at 05:30 IST via CRONJOBS (settings.py), and on demand from the
Google Sync page's "Sheet sync" button. Both paths go through SyncOrchestrator,
so a run started here is visible in the sync history and holds the same lock
that blocks a concurrent bookings/events push.

Which modules run, and which of their columns, is CRM_MODULES in sync/crm_mirror.py.
--tab and --columns here override it for one run without touching the file, so a
narrowed tab can be seen in the spreadsheet before it is committed to the constant.
--list-columns prints the field names those lists have to be written in terms of.
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
        parser.add_argument(
            "--columns",
            help=(
                "Comma-separated field names to mirror, in the order they should "
                "appear in the sheet. Requires exactly one --tab, and overrides "
                "that tab's column list for this run only."
            ),
        )
        parser.add_argument(
            "--list-columns",
            action="store_true",
            help=(
                "Print each tab's available field names and current selection, then exit. "
                "These are the names CRM_MODULES and --columns are written in terms of."
            ),
        )

    def handle(self, *args, **options):
        tabs = options.get("tabs")
        columns = options.get("columns")

        if options.get("list_columns"):
            self._list_columns(tabs)
            return

        if columns and len(set(tabs or [])) != 1:
            # Two tabs share no field names, so one list cannot mean both.
            self.stderr.write(self.style.ERROR(
                "--columns applies to one tab, so it needs exactly one --tab."
            ))
            return

        if tabs:
            # Single-tab runs bypass the orchestrator: they're a debugging aid,
            # not a scheduled sync, and shouldn't land in the sync history.
            from sync.crm_mirror import CRM_MODULES, _normalise, mirror_all

            unknown = set(tabs) - {m[0] for m in CRM_MODULES}
            if unknown:
                self.stderr.write(self.style.ERROR(
                    f"Unknown tab(s): {', '.join(sorted(unknown))}. "
                    f"Valid: {', '.join(m[0] for m in CRM_MODULES)}"
                ))
                return

            selected = [_normalise(m) for m in CRM_MODULES if m[0] in set(tabs)]
            if columns:
                override = [c.strip() for c in columns.split(",") if c.strip()]
                selected = [(t, p, override) for t, p, _ in selected]

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

    def _list_columns(self, tabs=None):
        """Print what --columns and CRM_MODULES can be written in terms of."""
        from sync.crm_mirror import (
            ALL_COLUMNS, CRM_MODULES, _available_fields, _get_model, _normalise,
        )

        wanted = set(tabs or [])
        for tab, path, cols in (_normalise(m) for m in CRM_MODULES):
            if wanted and tab not in wanted:
                continue
            available = _available_fields(_get_model(path), path)
            state = (
                "all columns" if cols is ALL_COLUMNS
                else f"{len(cols)} of {len(available)} selected"
            )
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n{tab}  ({path})  {state}"))
            for f in available:
                mark = (
                    "  " if cols is ALL_COLUMNS
                    else ("* " if (f.name in cols or f.attname in cols) else "  ")
                )
                self.stdout.write(f"    {mark}{f.name}")

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
