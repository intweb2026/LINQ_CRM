"""
events/management/commands/import_events_xlsx.py
─────────────────────────────────────────────────
Loader for the historical editions workbook ("23,24,25 Events Data.xlsx").

Three things about that sheet decide the design.

  EVENT CODE IS NOT UNIQUE. 358 rows carry 273 distinct codes, and 104 of those
  codes name live 2026 editions already in the CRM. An event legitimately repeats
  its code — cancelled and re-run in the same year, or simply run again the next
  year — but `events.event_code` is unique in the database, and a dozen call
  sites across bookings, delegates and the historical matcher do
  `Event.objects.get(event_code=...)` or count bookings by that raw string. So
  the code cannot repeat in the column, and the row still has to be findable by
  the code a human recognises. On a historical edition the MRE initials come off
  and the year goes on, "DDU - PT" 2025 -> "DDU 25". The initials name the
  market research person rather than the event, which is noise once an edition is
  closed; "DDU 24" is the spelling the sheet's own Nearest Related Event column
  already uses, so those references now name a real row; and every one of the 154
  live 2026 codes carries initials, so nothing minted here can collide with them.
  `base_code` keeps the bare "DDU" and `year` keeps 2025, and that pair is what
  the Performance Matrix keys on (see events/codes.py). CODE_STYLE is the switch.

  THE "STATUS" COLUMN HOLDS VERDICTS. "Going Ahead" and "Postponed" are members
  of Event.Verdict, not Event.Status, so the column lands on `verdict` and
  `status` is derived through STATUS_FROM_VERDICT.

  OWNER COLUMNS NAME PEOPLE THE CRM HAS NEVER HEARD OF. 111 cells name 17 people
  with no account. A name stored as plain text owns nothing and grants nobody
  sight of the row, so an account is created for each — one that EXISTS but
  cannot sign in, the same shape seed_dmd_assignees creates. A blank cell stays
  blank and creates nothing.

Every run writes a per-row CSV report saying what happened to each Excel row and
why, next to the workbook. That is the thing the Import wizard does not give you:
its response carries `skipped_records`, but frontend/src/api/import.js keeps only
the count, so a skipped row leaves no trace anywhere.

Dry run unless --commit is passed. The load runs inside one transaction that is
rolled back at the end, so a dry run exercises every constraint, every owner
lookup and the full report while writing nothing.

    python manage.py import_events_xlsx
    python manage.py import_events_xlsx --commit
    python manage.py import_events_xlsx --file "C:\\path\\to\\book.xlsx" --commit
"""
import csv
import re
from collections import Counter, defaultdict
from difflib import get_close_matches
from datetime import date, datetime

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import role_from_team_name
from accounts.user_resolution import AMBIGUOUS, OwnerResolver
from events.models import Event
from teams.models import Team

User = get_user_model()

# The workbook travels WITH the repo, next to the two book_event importers use,
# so this command runs on a fresh clone without anybody being told where the file
# lives. Relative to the backend directory, matching DEFAULT_WORKBOOK in
# book_event/management/commands/update_delegate_number_paid_free.py.
DEFAULT_FILE = "data_imports/events_23_24_25.xlsx"

# ── The mapping. This is the only part meant to be edited. ────────────────────
# Sheet header -> Event field. Headers match on their FIRST LINE, trimmed, so
# the two-line explanatory headers in the sheet need only their first line here.
# Delete a line to ignore that column; change the right-hand side to retarget
# it. Any concrete field on Event is a valid target.
COLUMNS = {
    "Base Code":                              "base_code",
    "Event Code":                             "event_code",
    "Start Date":                             "event_date",
    "End Date":                               "end_date",
    "Region":                                 "location",
    "Website":                                "website",
    "Nearest Related Event":                  "nearest_related_event",
    "Sales Team":                             "sales_team",
    "Telemarketing Team (UPDATED)":           "telemarketing_team",
    "SpEx Team":                              "spex_team",
    "Market Research (Senior)":               "market_research_senior",
    "Official Name 1 (Website Content).":     "official_event_name",
    "Official Name 2 (Text-based Mailshots)": "email_marketing_name",
    "Branding (Signatures and Logo)":         "branding_name",
    "Annualisation":                          "annualisation",
    "Date Format":                            "date_format",
    "Status":                                 "verdict",
    "Year":                                   "year",
}

# How `event_code` is built. See the module docstring for why the sheet's own
# code cannot simply be repeated.
#
#   "base"    the MRE initials are dropped and the two-digit year takes their
#             place, "DDU - PT" 2025 -> "DDU 25". The initials name the market
#             research person, not the event, which is noise on a closed edition;
#             and "DDU 24" is exactly the spelling the sheet's own Nearest
#             Related Event column already uses, so those references now name a
#             real row. base_code stays the bare "DDU". Every one of the 154 live
#             2026 codes carries initials, so nothing here can collide with them.
#   "year"    the sheet code kept whole, plus the year, "BISG - PM" -> "BISG - PM 25".
#   "minimal" the sheet code kept EXACTLY as written wherever it is free; only a
#             genuine repeat is marked. Mixes two spellings in one list.
CODE_STYLE = "base"

# The sheet carries no Event.Status column. Every row is a past edition, so the
# verdict is the only thing in the file that says how it ended.
STATUS_FROM_VERDICT = {
    "Going Ahead": Event.Status.COMPLETED,
    "Postponed":   Event.Status.POSTPONED,
}

# Placeholders that mean "nothing", not a value. Stored verbatim, "N/A" and "?"
# print as owner names in the Events table and suppress the team fallback in
# events/serializers.py, which is worse than an empty column. A cell holding one
# of these is treated exactly like an empty cell, so it creates no account.
BLANKS = {"", "-", "--", "–", "—", "n/a", "na", "?", "tbc", "tbd", "none", "null"}

# Columns that name a person, and the role that column implies for an account
# this command has to create. Each name is resolved to a User, which sets the FK
# and the m2m deciding who can SEE the row, and is rewritten to that user's
# display name so the sheet's spelling cannot drift from the CRM's.
OWNER_ROLES = {
    "sales_team":             User.Role.SALES,
    "team_leader":            User.Role.SALES,
    "telemarketing_team":     User.Role.TELEMARKETING,
    "spex_team":              User.Role.SPEX,
    "market_research_senior": User.Role.MARKET_RESEARCH,
    "market_research_junior": User.Role.MARKET_RESEARCH,
    "event_management_team":  User.Role.OPERATIONS,
}

# RFC 2606 reserved TLD, so a stray email can never reach a real person.
EMAIL_DOMAIN = "events.invalid"

DATE_FIELDS = ("event_date", "end_date", "website_live_date")
_DATE_FMTS = ("%Y-%m-%d", "%d-%b-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d %b %Y", "%d %B %Y")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _text(value):
    """Trimmed cell text, with the sheet's placeholders collapsed to blank."""
    s = "" if value is None else str(value).strip()
    s = re.sub(r"\s+", " ", s)
    return "" if s.lower() in BLANKS else s


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = _text(value)
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def edition_code(sheet_code, base_code, year, start, seen):
    """
    A unique value for `event_code`, derived from the sheet alone.

    Under the default CODE_STYLE "base" the MRE initials come off and the year
    goes on, so "DDU - PT" in 2025 is "DDU 25". A family that ran twice in one
    year — the postponed date and the run that replaced it — takes the start
    month as well ("DAU 25 JUN"), and a third run in the same month gets a
    counter. On this sheet that is 319 codes settled by the year alone, 35 more
    by the month, and 4 by a counter.

    `seen` carries the codes already taken, so the answer never depends on the
    order the database happens to return rows in, and a re-run over the same
    sheet reproduces it exactly.
    """
    yy = f"{year % 100:02d}"
    stem = (base_code or sheet_code) if CODE_STYLE == "base" else sheet_code
    candidates = [sheet_code] if CODE_STYLE == "minimal" else []
    candidates.append(f"{stem} {yy}")
    if start:
        candidates.append(f"{stem} {yy} {start.strftime('%b').upper()}")
    candidates += [f"{stem} {yy} #{n}" for n in range(2, 12)]
    for code in candidates:
        if code.upper() not in seen:
            seen.add(code.upper())
            return code
    raise CommandError(f"cannot mint a unique code for {sheet_code} {year}")


class OwnerDesk:
    """
    Turns an owner cell into a User, creating the account when the CRM has none.

    An account is created ONLY for a name nothing matched. An AMBIGUOUS name —
    two people the CRM already holds answer to it — is reported and left as plain
    text, because adding a third account for a name that already has two makes
    the ambiguity permanent rather than resolving it.

    What gets created is an account that EXISTS but cannot sign in:
    login_access=False, an unusable password and a placeholder address on the
    reserved .invalid TLD. It is `status=active` because that is what makes it
    resolvable and pickable; it is placed in the team its column implies, because
    almost everything on a user record is scoped by team. Same shape as
    ticket_central/management/commands/seed_dmd_assignees.py, deliberately.
    """

    def __init__(self, create=True):
        self.create = create
        self.resolver = OwnerResolver()
        # Lowest pk wins where two teams imply one role, matching the tie-break
        # in events/serializers.team_owner_defaults.
        self.teams = {}
        for team in Team.objects.filter(is_archived=False).order_by("pk"):
            role = role_from_team_name(team.name)
            if role and role not in self.teams:
                self.teams[role] = team
        self.taken = set(User.objects.values_list("username", flat=True))
        self.taken |= {e.lower() for e in User.objects.values_list("email", flat=True) if e}
        self.known_names = [u.get_full_name() or u.username for u in self.resolver.users]
        self.made = {}          # lower-cased name -> user created this run
        self.created = []       # (name, email, role) for the report
        self.unresolved = Counter()

    def owner(self, name, field):
        key = name.lower()
        if key in self.made:
            return self.made[key]

        user, reason = self.resolver.resolve(name)
        if user:
            return user
        if reason == AMBIGUOUS or not self.create:
            self.unresolved[f"{name} ({reason})"] += 1
            return None

        role = OWNER_ROLES.get(field, User.Role.SALES)
        base = _NON_ALNUM.sub(".", name.lower()).strip(".") or "event.owner"
        local, n = base, 2
        while local in self.taken or f"{local}@{EMAIL_DOMAIN}" in self.taken:
            local, n = f"{base}{n}", n + 1
        email = f"{local}@{EMAIL_DOMAIN}"
        self.taken |= {local, email}

        first, _, last = name.partition(" ")
        user = User(
            username=local, email=email, first_name=first, last_name=last,
            role=role, status=User.Status.ACTIVE,
            # Exists and is pickable; cannot sign in until somebody gives it a
            # real address and ticks login access.
            login_access=False,
            team=self.teams.get(role),
        )
        # save() derives role from the team name on a new row unless the caller
        # says the role was chosen. The column is the better evidence here.
        user.role_is_explicit = True
        user.set_unusable_password()
        user.save()

        self.made[key] = user
        # A name one letter off an existing user is a typo in the sheet, not a
        # new colleague — "Vick Verela" against "Vick Varela". Reported only,
        # never acted on: accounts/user_resolution.py refuses fuzzy matching for
        # the good reason that a near miss hands one person another's events.
        near = get_close_matches(name, self.known_names, n=1, cutoff=0.85)
        self.created.append((name, email, role, near[0] if near else ""))
        return user


class Command(BaseCommand):
    help = "Load the historical events workbook into the events catalogue."

    def add_arguments(self, parser):
        parser.add_argument("--file", default=DEFAULT_FILE)
        parser.add_argument("--sheet", default=None, help="Worksheet name; first sheet by default.")
        parser.add_argument("--commit", action="store_true",
                            help="Write. Without it the whole load is rolled back.")
        parser.add_argument("--no-create-users", dest="create_users", action="store_false",
                            help="Leave an unknown owner name as plain text instead of "
                                 "creating a sign-in-less account for it.")
        parser.add_argument("--report", default=None,
                            help="Per-row CSV report path. Defaults to <workbook>.import-report.csv")

    def handle(self, *args, **opts):
        import openpyxl

        try:
            book = openpyxl.load_workbook(opts["file"], data_only=True, read_only=True)
        except FileNotFoundError:
            raise CommandError(f"file not found: {opts['file']}")
        sheet = book[opts["sheet"]] if opts["sheet"] else book.worksheets[0]

        rows = [(n, r) for n, r in enumerate(sheet.iter_rows(values_only=True), start=1)
                if any(v is not None and str(v).strip() for v in r)]
        if not rows:
            raise CommandError("sheet is empty")
        header_row, body = rows[0][1], rows[1:]

        # Headers are matched on their first line so the sheet's two-line
        # explanatory headers do not have to be pasted into COLUMNS verbatim.
        header = {str(h).split("\n")[0].strip(): i
                  for i, h in enumerate(header_row) if h is not None}
        missing = [h for h in COLUMNS if h not in header]
        if missing:
            raise CommandError(
                "COLUMNS names headers the sheet does not have: "
                + ", ".join(repr(m) for m in missing)
                + "\nsheet headers: " + ", ".join(repr(h) for h in header)
            )
        unmapped = [h for h in header if h not in COLUMNS]

        desk = OwnerDesk(create=opts["create_users"])
        seen_codes = {c.upper() for c in Event.objects.values_list("event_code", flat=True)}
        db_codes = set(seen_codes)
        verdicts, by_action = Counter(), defaultdict(int)
        report = []

        with transaction.atomic():
            for excel_row, raw in body:
                cell = {field: raw[header[h]] for h, field in COLUMNS.items()}
                sheet_code = _text(cell.get("event_code")).upper()
                start = _as_date(cell.get("event_date"))
                year_txt = _text(cell.get("year"))
                year = int(year_txt) if year_txt.isdigit() else (start.year if start else None)

                line = {"excel_row": excel_row, "sheet_code": sheet_code,
                        "event_code": "", "year": year or "",
                        "start_date": start or "", "verdict": "", "status": "",
                        "action": "", "note": ""}

                if not sheet_code or not start or not year:
                    line.update(action="error", note="needs event code, start date and year")
                    report.append(line)
                    continue

                base = _text(cell.get("base_code")).upper()
                code = edition_code(sheet_code, base, year, start, seen_codes)
                event = Event.objects.filter(event_code=code).first()
                line["event_code"] = code
                line["action"] = "update" if event else "insert"
                if code.upper() != f"{base} {year % 100:02d}":
                    line["note"] = f"{base} ran more than once in {year}; marked by month"
                event = event or Event(event_code=code)

                owners, notes = [], []
                for field, value in cell.items():
                    if field in ("event_code", "year"):
                        continue
                    if field in DATE_FIELDS:
                        setattr(event, field, _as_date(value))
                        continue
                    text = _text(value)
                    if field == "base_code":
                        text = text.upper()
                    # A blank cell stays blank and resolves nothing.
                    if field in OWNER_ROLES and text:
                        user = desk.owner(text, field)
                        if user:
                            owners.append(user)
                            text = user.get_full_name() or user.username
                        else:
                            notes.append(f"{field}={text} unresolved")
                    setattr(event, field, text)

                event.year = year
                verdicts[event.verdict] += 1
                # Only ever a DEFAULT. An unmapped verdict leaves status alone
                # and is reported, so a new sheet value cannot silently relabel a
                # row as Completed.
                if event.verdict in STATUS_FROM_VERDICT:
                    event.status = STATUS_FROM_VERDICT[event.verdict]
                elif event.verdict:
                    notes.append(f"verdict {event.verdict} has no status mapping")
                line["verdict"], line["status"] = event.verdict, event.status

                try:
                    event.save()
                    event.assigned_users.set(owners)
                except Exception as exc:            # noqa: BLE001 - reported, not raised
                    line["action"] = "error"
                    notes.append(str(exc))
                by_action[line["action"]] += 1
                line["note"] = "; ".join(filter(None, [line["note"]] + notes))
                report.append(line)

            path = self._write_report(opts, report)
            self._summarise(report, unmapped, by_action, seen_codes, db_codes,
                            verdicts, desk, path, opts["commit"])
            if not opts["commit"]:
                transaction.set_rollback(True)

    def _write_report(self, opts, report):
        path = opts["report"] or re.sub(r"\.xlsx?$", "", opts["file"]) + ".import-report.csv"
        # utf-8-sig so Excel opens it without mangling accents.
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(report[0]) if report else ["excel_row"])
            writer.writeheader()
            writer.writerows(report)
        return path

    def _summarise(self, report, unmapped, by_action, seen_codes, db_codes,
                   verdicts, desk, path, commit):
        out, ok, warn = self.stdout, self.style.SUCCESS, self.style.WARNING
        out.write("")
        out.write(f"rows read        {len(report)}")
        out.write(f"inserted         {by_action['insert']}")
        out.write(f"updated in place {by_action['update']}")
        out.write(f"errors           {by_action['error']}")
        marked = sum(1 for r in report if "ran more than once" in r["note"])
        out.write(f"code style       {CODE_STYLE!r}, {marked} row(s) needed a month or counter")
        if seen_codes & db_codes != db_codes or by_action["update"]:
            out.write(warn("some rows matched an existing event_code and were UPDATED, "
                           "not inserted; see the action column in the report"))
        if unmapped:
            out.write(warn("sheet columns not in COLUMNS: " + ", ".join(repr(h) for h in unmapped)))
        out.write("verdicts         " + ", ".join(f"{k or '(blank)'}={v}" for k, v in verdicts.most_common()))

        if desk.created:
            out.write(ok(f"\naccounts created ({len(desk.created)}) — they exist and own "
                         f"their events, but cannot sign in until given a real address:"))
            for name, email, role, near in sorted(desk.created):
                hint = warn(f"  <- typo for {near!r}?") if near else ""
                out.write(f"    {name:<22} {email:<34} {role}{hint}")
        if desk.unresolved:
            out.write(warn(f"\nowner names left as plain text ({sum(desk.unresolved.values())} cells):"))
            for name, count in desk.unresolved.most_common():
                out.write(f"    {count:>3}  {name}")

        out.write(f"\nper-row report   {path}")
        out.write(ok("committed") if commit else warn("DRY RUN — rolled back. Re-run with --commit to write."))
