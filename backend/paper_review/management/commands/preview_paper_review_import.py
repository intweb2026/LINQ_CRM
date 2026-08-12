"""
paper_review/management/commands/preview_paper_review_import.py
───────────────────────────────────────────────────────────────
E1 — run a REAL .xlsx/.csv through the paper-review import classification and
print the per-row result. Writes NOTHING.

WHY THIS EXISTS
Four passes of import work were validated against the JSON-row CONTRACT only: the
tests post dicts, because that is what the browser posts. No actual spreadsheet had
ever been parsed. That left one class of defect permanently invisible — anything
that goes wrong between "a cell in a workbook" and "a value in a JSON row":
a trailing space in a header, a date Excel stored as a serial, a numeric cell that
arrives as 9.0 rather than 9, a stray formatting-only row below the data. This
command closes that gap, and gives a way to test a real Zoho export without a
browser.

IT REUSES THE IMPORTER, IT DOES NOT REIMPLEMENT IT
map_headers, file_has_mr_content, classify_rows and summarise are imported from
paper_review/importer.py — the same functions the two API endpoints call, in the
same order, with the same arguments. Nothing about classification lives here. If
this command and the endpoint ever disagree, that is a bug in one of them, not a
difference of intent.

READ-ONLY, STRUCTURALLY
classify_rows() writes nothing by construction, and this command never constructs
a PaperReview, never calls .save() and never opens a transaction. The only queries
it issues are the SELECTs that build `existing_pairs` (for the duplicate warning)
and the event-catalogue reads inside event-code resolution.

WHAT IT SEES THAT THE BROWSER DOES NOT
Dates. accounts/import_common.py:read_import_rows reads the workbook with openpyxl,
so a date cell arrives as a real datetime and a serial arrives as an int, whereas
the browser's SheetJS `raw: false` read turns every cell into displayed text first.
parse_import_date accepts all three, which is what makes both paths safe — but
they are not the same input, and this one is stricter about nothing and more
revealing about everything. See the note in read_import_rows.

Usage:
    python manage.py preview_paper_review_import path/to/export.xlsx
    python manage.py preview_paper_review_import export.csv --user someone
    python manage.py preview_paper_review_import export.xlsx --only errors
    python manage.py preview_paper_review_import export.xlsx --format csv
"""
import csv

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from accounts.import_common import read_import_rows

# Absolute imports, not relative: inside a management command `..` resolves to
# paper_review.management, not paper_review. Matches
# report_paper_review_recipients.py in this same directory.
from paper_review.access import may_see_mr_fields, scope_queryset
from paper_review.importer import (
    CREATE, CREATE_WITH_WARNING, ERROR, MAX_ROWS,
    classify_rows, file_has_mr_content, map_headers, summarise,
)
from paper_review.models import PaperReview


class _Unrestricted:
    """
    The principal used when --user is not given.

    A real user is required for an honest answer: classify_rows() asks
    has_full_visibility() and permitted_event_codes(), and passing None would make
    BOTH answer "no access", turning every row into an out-of-scope ERROR and
    reporting a clean file as 500 failures. This sentinel is the opposite default —
    full visibility, MR fields readable — so a bare invocation reports what the
    FILE contains rather than what one account may import. The output says which
    principal was used, every time, so the distinction is never silent.
    """
    is_authenticated = True
    is_admin = True
    username = "(unrestricted)"
    role = "admin"
    custom_role = None
    assigned_events = None

    @property
    def pk(self):
        return None


class Command(BaseCommand):
    help = (
        "Read-only: run an .xlsx/.xlsm/.csv/.json file through the paper-review "
        "import classifier and print the per-row plan. Writes nothing."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path to the .xlsx / .xlsm / .csv / .json file")
        parser.add_argument(
            "--user",
            help="Username to evaluate RBAC scope and MR-column access as. "
                 "Omitted: unrestricted (reports what the file contains).",
        )
        parser.add_argument(
            "--only", choices=["errors", "warnings", "create"],
            help="Show only rows in this category. The summary still counts all.",
        )
        parser.add_argument(
            "--limit", type=int,
            help="Classify only the first N data rows.",
        )
        parser.add_argument(
            "--format", choices=["text", "csv"], default="text",
            help="text (default) or csv to stdout",
        )

    # ── principal ────────────────────────────────────────────────────────────
    def _resolve_user(self, username):
        if not username:
            return _Unrestricted(), "unrestricted (no --user given)"
        user = get_user_model().objects.filter(username=username).first()
        if user is None:
            raise CommandError(f"No user with username {username!r}.")
        return user, f"{user.username} (role={getattr(user, 'role', '?')})"

    def handle(self, *args, **options):
        path = options["path"]
        user, principal = self._resolve_user(options.get("user"))

        try:
            rows = read_import_rows(path)
        except FileNotFoundError:
            raise CommandError(f"File not found: {path}")
        except ImportError as exc:
            raise CommandError(f"Cannot read {path}: {exc}")

        if not rows:
            raise CommandError(f"{path} contains no data rows.")

        if options.get("limit"):
            rows = rows[: options["limit"]]

        # Column order as the file declares it, first occurrence wins — the same
        # loop the viewset's _build_plan uses, so unrecognised-column reporting is
        # identical.
        columns = []
        for row in rows:
            for key in row:
                if key not in columns:
                    columns.append(key)
        mapping, unrecognised = map_headers(columns)

        # The API refuses the WHOLE file for a non-MR user carrying MR content
        # (B7). Reported rather than enforced here: this command writes nothing, so
        # the useful answer is "the endpoint would refuse this, and why".
        mr_blocked = []
        if not may_see_mr_fields(user):
            mr_blocked = file_has_mr_content(rows, mapping)

        existing_pairs = set()
        if mapping:
            scoped = scope_queryset(PaperReview.objects.all(), user)
            existing_pairs = {
                (email.lower(), code)
                for email, code in scoped.values_list("email", "event_code")
            }

        plan = classify_rows(rows, mapping, user, existing_pairs) if mapping else []
        counts = summarise(plan) if plan else {CREATE: 0, CREATE_WITH_WARNING: 0, ERROR: 0}

        if options["format"] == "csv":
            self._emit_csv(plan)
            return

        self._emit_text(path, principal, rows, columns, mapping, unrecognised,
                        mr_blocked, plan, counts, options.get("only"))

    # ── output ───────────────────────────────────────────────────────────────
    def _emit_csv(self, plan):
        writer = csv.DictWriter(
            self.stdout,
            fieldnames=["row", "classification", "event_code", "speaker_name",
                        "email", "warning", "errors"])
        writer.writeheader()
        for entry in plan:
            writer.writerow({
                "row": entry["row"],
                "classification": entry["classification"],
                "event_code": entry["event_code"],
                "speaker_name": entry["speaker_name"],
                "email": entry["email"],
                "warning": entry.get("warning", ""),
                "errors": "; ".join(
                    f"{e['field']}: {e['problem']} [{e['value']}]"
                    for e in entry["errors"]),
            })

    def _emit_text(self, path, principal, rows, columns, mapping, unrecognised,
                   mr_blocked, plan, counts, only):
        self.stdout.write(f"File            {path}")
        self.stdout.write(f"Data rows       {len(rows)}")
        self.stdout.write(f"Principal       {principal}")
        self.stdout.write(f"Columns         {len(columns)} "
                          f"({len(mapping)} mapped, {len(unrecognised)} unrecognised)")
        if len(rows) > MAX_ROWS:
            self.stdout.write(self.style.WARNING(
                f"  NOTE: {len(rows)} rows exceeds the {MAX_ROWS}-row per-call API "
                f"cap; the browser would send this in "
                f"{-(-len(rows) // MAX_ROWS)} chunks. Classification below is for "
                f"the whole file."))
        if unrecognised:
            self.stdout.write(self.style.WARNING(
                "  Unrecognised columns (would NOT be imported): "
                + ", ".join(repr(c) for c in unrecognised)))
        if not mapping:
            self.stdout.write(self.style.ERROR(
                "  No recognisable columns at all — the endpoint would answer 400."))
            return
        if mr_blocked:
            self.stdout.write(self.style.ERROR(
                "  WHOLE-FILE REFUSAL: this principal may not import "
                + ", ".join(mr_blocked)
                + ". The endpoint would answer 400 naming the column, and no row "
                  "would be written."))

        self.stdout.write("")
        shown = [
            e for e in plan
            if not only
            or (only == "errors" and e["classification"] == ERROR)
            or (only == "warnings" and e["classification"] == CREATE_WITH_WARNING)
            or (only == "create" and e["classification"] == CREATE)
        ]
        for entry in shown:
            tag = {
                CREATE: self.style.SUCCESS("CREATE              "),
                CREATE_WITH_WARNING: self.style.WARNING("CREATE_WITH_WARNING "),
                ERROR: self.style.ERROR("ERROR               "),
            }[entry["classification"]]
            self.stdout.write(
                f"row {entry['row']:>4}  {tag} "
                f"{entry['event_code'] or '(unresolved)':<16} "
                f"{entry['speaker_name'][:24]:<24} {entry['email'][:32]}")
            for err in entry["errors"]:
                self.stdout.write(
                    f"              - {err['field']}: {err['problem']} "
                    f"[raw: {err['value']!r}]")
            if entry.get("warning"):
                self.stdout.write(f"              ! {entry['warning']}")

        if only:
            self.stdout.write(
                f"\n({len(shown)} of {len(plan)} shown - filtered to {only}.)")

        self.stdout.write("")
        self.stdout.write("Classification:")
        for name in (CREATE, CREATE_WITH_WARNING, ERROR):
            self.stdout.write(f"  {name:20s} {counts[name]}")
        importable = counts[CREATE] + counts[CREATE_WITH_WARNING]
        self.stdout.write(f"  {'importable':20s} {importable}")
        self.stdout.write(f"  {'would be skipped':20s} {counts[ERROR]}")
        self.stdout.write("")
        # ASCII only: the Windows console this is run from renders an em-dash as a
        # replacement character, and a report that looks corrupted invites doubt
        # about the numbers next to it.
        self.stdout.write(
            "Nothing was written. An import through the API would also fire "
            "NEITHER workflow: no proposal submissions, no production-team emails.")
