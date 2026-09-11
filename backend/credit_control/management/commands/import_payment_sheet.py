"""
credit_control/management/commands/import_payment_sheet.py
──────────────────────────────────────────────────────────
The one-shot lift of the Payment Collection sheet's four caller tabs, plus its
_Activity log, into the leads this module has already created.

AN OVERLAY, NOT A LOAD. It creates no lead, no invoice and no delegate. The
engine owns which invoices exist and which bucket each one sits in, and it
decides that by reading book_events live; this command writes only the columns
that exist NOWHERE BUT THE SHEET, namely the owner, the disposition, the
remark, the callback date, the last-touched stamp and the two invoice-type
columns. Everything else the sheet carries, the phones, the company, the tier,
the days pending, the times called, is already live in the CRM and copying it
would be building the snapshot models.py exists to avoid.

AN INVOICE NUMBER THAT HAS NO LEAD IS RE-RESOLVED BEFORE IT IS GIVEN UP ON.
Numbers move. Five of the sheet's invoices are absent from book_events because
the booking was re-invoiced, so DLG26BUE-2853 and -2854 are both now -2855 and
HFE27GER-2852 and -2853 are both now -2842. The delegate's EMAIL did not move,
so `book_delegates` still knows where the booking went, and remapping through
it is what turns five dead rows into three live leads. Only within the same
event edition, per `_remap`, so a delegate's unrelated booking elsewhere can
never be mistaken for the same debt.

THE ONE CASE A LEAD IS CREATED is an invoice that exists in book_events and has
already settled. The engine only routes PENDING invoices, so a booking paid or
cancelled before this module existed has no lead and never will get one from a
refresh, and three of the five re-invoiced numbers above are exactly that. The
work is real and the sheet is the only record of it, so a lead is written
straight into Resolved with the reason read off the live invoice by the
engine's own `_done_reason`. Nothing is decided here that the engine would have
decided differently; it simply never had the chance.

An invoice still without a lead after that, meaning no such booking exists at
all, is REPORTED and skipped, never invented. The lead's primary key is a
foreign key onto `book_events.invoice_number`, so inventing one would mean
writing a row that points at a booking nobody has loaded.

RUN ORDER, AND IT MATTERS
  1. refresh_credit_control                 creates and buckets the leads
  2. import_payment_sheet --file X --dry-run  read the report, load the gaps
  3. import_payment_sheet --file X            overlay the caller work

Re-runnable. Step 3 twice over writes the same values the second time, so the
usual path after loading a missing event is simply to run it again.

ONE INVOICE, ONE LEAD, AND THE SHEET DISAGREES
The sheet is keyed `invoice||email`, one row per delegate. A lead is keyed on
the invoice number alone, because one invoice is one phone call however many
delegates sit under it. 229 rows collapse to 215 invoices, and ten of those
invoices were being worked by two or three different callers at once.

NEWEST WINS. The row with the latest Last Updated takes the lead, and every
losing row's remark is folded into the winner's, tagged with the delegate name
and the disposition that row carried. Nothing a caller typed is discarded, and
the losing caller's own verdict survives as words even though the lead can only
have one owner.

WHAT IS DELIBERATELY NOT WRITTEN
  * `handed_off_at`. Freezing the sheet's allocation was the first plan, and it
    was wrong: serializers.get_handoff_reason derives a "passed on at day four"
    banner from that stamp, so setting it would print a handoff that never
    happened onto every imported lead. It is also unnecessary. Of the team
    lead's non-sticky rows, nearly all have a blank disposition, meaning nobody
    ever worked them, and moving exactly those is what the day-4 rule is FOR.
    The engine redistributing them is the agreed rule running, and the remarks
    travel with the lead either way.
  * `first_seen`. The engine set it when it created the lead, and it means when
    this lead entered the CRM's flow, which is true as it stands. It is not the
    invoice date and not the sheet's stamp, and the ageing columns read
    invoice_date anyway.
  * `next_action`. The column still exists, holding what was typed in the CRM
    before the field was withdrawn, but it is no longer displayed. Writing the
    sheet's 80 next actions there would file them somewhere nobody reads, so
    they are folded into the remark instead, which is one place and visible.
  * `resolved`. Three rows are dispositioned To Pay - Already paid, which
    records that somebody SAID they had paid. Only the source confirms payment,
    and constants.py is explicit that this disposition stays a live sticky lead
    until it does.
"""
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models.functions import Lower
from django.utils import timezone

from accounts.models import User
from book_delegate.models import BookDelegate
from book_event.models import BookEvent
from teams.models import Team

from ... import engine
from ...models import CreditControlLead, CreditControlTouch

TABS = ("Bruce", "Ben", "Derek", "Devin")
ACTIVITY_TAB = "_Activity"

# Bookings made against these domains are our own test data, not customers.
# Nineteen of the sheet's twenty-four invoices that are absent from book_events
# are test rows, and they are absent precisely BECAUSE they were never real.
TEST_DOMAINS = ("mailinator.com", "iq-hub.com")

# Labels the sheet used as dispositions that are not dispositions here. "Not
# Invoiced Yet" is a BUCKET in the CRM, derived from a blank invoice date, and
# every row carrying it as a disposition has an invoice date. The label goes,
# the remark stays, and the lead reads as untouched so a caller works it fresh.
DROPPED_DISPOSITIONS = frozenset({"Not Invoiced Yet"})

# The rubric these verdicts came from. Never equal to
# constants.CLASSIFIER_PROMPT_VERSION, which is what makes every imported
# verdict eligible for reclassification under the current rubric rather than
# passing as one it never saw.
SHEET_PROMPT_VERSION = "sheet"

# Where the workbook lives when it is not passed in. Committed alongside the
# code so production runs the same one command as a laptop does, with no path
# to type and no file to copy into a container.
#
# `.gitignore` blocks *.xlsx repo-wide, on purpose, because spreadsheets are the
# largest body of personal data that can reach a commit; this one is in anyway
# via `git add -f`, which is the escape hatch that rule documents.
DEFAULT_SHEET = Path(__file__).resolve().parents[2] / "data" / "payment_collection.xlsx"

# What the winner's Last Updated is compared against when it has none at all.
# Aware, because it is sorted against parsed stamps.
_NO_STAMP = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)


def is_test_row(email: str) -> bool:
    """Our own test bookings, matched on the delegate address."""
    address = (email or "").strip().lower()
    return any(address.endswith(domain) for domain in TEST_DOMAINS)


def sheet_datetime(value):
    """
    '19-Aug-2026 23:50:36' to an aware datetime, or None.

    Last Updated is TEXT in this workbook, not a date cell, so there is no
    getting at it without a format string. A value that will not parse returns
    None rather than raising: one unreadable stamp must not stop a migration,
    and the effect of None is only that the row loses a tiebreak.

    Naive values are localised to the project timezone, which is the
    America/Los_Angeles the predecessor Apps Script build forced on itself. See
    the shift note in constants.py.
    """
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.strptime(text, "%d-%b-%Y %H:%M:%S")
        except ValueError:
            return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed)
    return parsed


def sheet_date(value):
    """'27/8/2026' to a date, or None. Day first; no value in the column is ambiguous."""
    if isinstance(value, datetime):
        return value.date()
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%d/%m/%Y").date()
    except ValueError:
        return None


def clean_disposition(value: str) -> str:
    """
    The sheet's label as a live one.

    Two steps, and both are the module's existing rules rather than new ones.
    normalize_disposition applies constants.RETIRED_DISPOSITIONS, which is what
    turns the sheet's eight Not Interested rows into Disputing Invoice, and
    DROPPED_DISPOSITIONS removes the labels that were never dispositions.
    """
    label = engine.normalize_disposition(value or "")
    return "" if label in DROPPED_DISPOSITIONS else label


def fold_remark(winner: dict, losers: list[dict]) -> str:
    """
    One remark from several rows.

    The winner's own text first, then its next action, then a tagged line per
    losing row. The tag carries the delegate name AND the disposition that row
    held, because the lead can only report one disposition and the loser's would
    otherwise be the one thing the collapse destroyed.
    """
    lines = []
    if winner["remark"]:
        lines.append(winner["remark"])
    if winner["next_action"]:
        lines.append(f"Next: {winner['next_action']}")
    for other in losers:
        text = " ".join(p for p in (other["remark"], other["next_action"]) if p)
        if not (text or other["disposition"]):
            continue
        tag = ", ".join(p for p in (other["delegate"], other["disposition"]) if p)
        lines.append(f"[{tag}] {text}".rstrip())
    return "\n".join(lines)


def collapse(rows: list[dict]) -> dict:
    """
    Several rows for one invoice down to one, newest first.

    Sorted on Last Updated descending, with an unstamped row pushed to the back;
    a row nobody has touched should never outrank one somebody has.
    """
    ordered = sorted(rows, key=lambda r: r["updated"] or _NO_STAMP, reverse=True)
    winner, losers = ordered[0], ordered[1:]
    return {**winner, "remark": fold_remark(winner, losers), "folded": len(losers)}


def edition_of(invoice: str) -> str:
    """
    The event-edition half of an invoice number, so `DLG26BUE-2853` is `DLG26BUE`.

    Re-invoicing changes the number after the dash and never the part before it,
    which is what makes this a safe fence around the email lookup.
    """
    return invoice.rsplit("-", 1)[0].strip().lower()


class Command(BaseCommand):
    help = "Overlay the Payment Collection sheet's caller work onto existing leads."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file", default=str(DEFAULT_SHEET),
            help=f"Path to the workbook. Defaults to {DEFAULT_SHEET.name} beside the app.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would change and write nothing.",
        )
        parser.add_argument(
            "--skip-activity", action="store_true",
            help="Leave the _Activity tab out of CreditControlTouch.",
        )

    def handle(self, *args, **options):
        # Imported here and not at module scope. openpyxl is in requirements
        # for the export path, and a one-shot migration command has no business
        # adding an import to every management command's startup.
        from openpyxl import load_workbook

        self.dry_run = options["dry_run"]
        try:
            workbook = load_workbook(options["file"], data_only=True)
        except FileNotFoundError:
            raise CommandError(
                f"No such workbook: {options['file']}. Pass --file, or put it at "
                f"{DEFAULT_SHEET}."
            )

        callers = self._callers()
        rows, tests = self._read_tabs(workbook, callers)
        self.stdout.write(
            f"Read {len(rows) + tests} rows from {', '.join(TABS)}; "
            f"{tests} were test bookings and are ignored."
        )

        remapped = self._remap(rows)
        if remapped:
            self.stdout.write(
                f"{len(remapped)} invoice numbers were re-invoiced and have been "
                "followed to their current number via book_delegates."
            )
            for was, now_, email in sorted(remapped):
                self.stdout.write(f"  {was:16} -> {now_:16} {email}")

        by_invoice: dict[str, list[dict]] = {}
        for row in rows:
            by_invoice.setdefault(row["invoice"], []).append(row)
        collapsed = [collapse(group) for group in by_invoice.values()]
        folded = sum(row["folded"] for row in collapsed)
        self.stdout.write(
            f"{len(collapsed)} invoices, of which "
            f"{sum(1 for r in collapsed if r['folded'])} had more than one caller; "
            f"{folded} losing rows folded into the winner's remark."
        )

        with transaction.atomic():
            written, missing = self._overlay(collapsed)
            touches = 0
            if not options["skip_activity"]:
                touches = self._activity(
                    workbook, {was: now_ for was, now_, _ in remapped},
                )
            if self.dry_run:
                transaction.set_rollback(True)

        verb = "Would write" if self.dry_run else "Wrote"
        self.stdout.write(self.style.SUCCESS(f"{verb} {written} leads, {touches} touches."))
        if missing:
            self.stdout.write(self.style.WARNING(
                f"{len(missing)} invoices have no lead, so their work could not "
                "be placed. Load the event into book_events, run "
                "refresh_credit_control, then run this again."
            ))
            for invoice, who, email in sorted(missing):
                self.stdout.write(f"  {invoice:16} {who:6} {email}")
        if self.dry_run:
            self.stdout.write(self.style.WARNING("Dry run. Nothing was committed."))

    # ── Reading ────────────────────────────────────────────────────────────

    def _callers(self) -> dict[str, User]:
        """
        Sheet first name to CRM user, scoped to the Credit Control team.

        SCOPED, because "Ben" alone is ambiguous in this database; there is a
        Benny Scott in Admin as well as the Ben Patino who does this work. The
        team is what disambiguates, and it is the same team the roster reads, so
        a name this cannot resolve is a name that could not be given a queue
        either. An unresolvable rep is fatal rather than skipped: assigning
        somebody else's leads quietly is worse than stopping.
        """
        team = (
            Team.objects
            .filter(name__iexact=engine.TEAM_NAME, is_archived=False)
            .first()
        )
        if not team:
            raise CommandError(
                f"No team named {engine.TEAM_NAME!r}. Create it and add the four "
                "callers before importing their work."
            )
        found = {
            (person.first_name or "").strip().lower(): person
            for person in User.objects.filter(team=team)
        }
        unknown = [name for name in TABS if name.lower() not in found]
        if unknown:
            raise CommandError(
                f"No {engine.TEAM_NAME} member matches {', '.join(unknown)}. "
                f"The team holds: {', '.join(sorted(found)) or 'nobody'}."
            )
        return found

    def _read_tabs(self, workbook, callers) -> tuple[list[dict], int]:
        tabs = [name for name in TABS if name in workbook.sheetnames]
        if not tabs:
            raise CommandError(f"The workbook has none of the tabs {TABS}.")

        rows, tests = [], 0
        for tab in tabs:
            sheet = workbook[tab]
            header = [
                str(cell).strip() if cell is not None else ""
                for cell in next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))
            ]
            index = {name: position for position, name in enumerate(header)}

            def cell(record, column):
                position = index.get(column)
                if position is None or position >= len(record):
                    return None
                return record[position]

            for record in sheet.iter_rows(min_row=2, values_only=True):
                invoice = str(cell(record, "Invoice Number") or "").strip()
                if not invoice:
                    continue
                email = str(cell(record, "Email") or "").strip()
                if is_test_row(email):
                    tests += 1
                    continue
                rows.append({
                    "invoice": invoice,
                    "tab": tab,
                    "owner": callers[tab.lower()],
                    "delegate": str(cell(record, "Delegate Name") or "").strip(),
                    "email": email,
                    "disposition": clean_disposition(cell(record, "Calling Disposition")),
                    "remark": str(cell(record, "Remark") or "").strip(),
                    "next_action": str(cell(record, "Next Action") or "").strip(),
                    "callback": sheet_date(cell(record, "Callback Date")),
                    "updated": sheet_datetime(cell(record, "Last Updated")),
                    "invoice_type": str(cell(record, "Invoice Type") or "").strip(),
                    "type_notes": str(cell(record, "Invoice Type Notes") or "").strip(),
                })
        return rows, tests

    def _remap(self, rows: list[dict]) -> list[tuple[str, str, str]]:
        """
        Follow a re-invoiced booking to its current invoice number, in place.

        Only rows whose own number has no lead are looked up, so a sheet that
        matches the CRM costs one extra query and no remapping at all.

        THE FENCE IS THE EVENT EDITION. A delegate can hold bookings at several
        events, and matching on email alone would happily move a Dubai debt onto
        a Houston invoice. A candidate is accepted only when it sits in the same
        edition as the number the sheet recorded, and only when the lookup
        returns exactly ONE such invoice; two candidates is a genuine ambiguity
        and is left for a person, because guessing which of two debts a remark
        belongs to is worse than reporting that it needs deciding.
        """
        have = set(
            CreditControlLead.objects
            .filter(invoice_id__in={row["invoice"] for row in rows})
            .values_list("invoice_id", flat=True)
        )
        orphans = [row for row in rows if row["invoice"] not in have and row["email"]]
        if not orphans:
            return []

        # MATCHED ON Lower(email) AND NOT ON email__in. Postgres compares
        # strings case-sensitively, and 22 of the 15,508 delegate emails are
        # stored with capitals, one of them the `Ben.Yu@nrc-cnrc.gc.ca` this
        # very lookup exists to follow. A lowercased `__in` silently found
        # nothing and the row reported as an invoice that does not exist.
        candidates: dict[str, set[str]] = {}
        for email, invoice in (
            BookDelegate.objects
            .annotate(email_lower=Lower("email"))
            .filter(email_lower__in={row["email"].lower() for row in orphans})
            .values_list("email_lower", "invoice_id")
        ):
            candidates.setdefault(email or "", set()).add(invoice)

        moved = []
        for row in orphans:
            edition = edition_of(row["invoice"])
            found = {
                invoice
                for invoice in candidates.get(row["email"].lower(), ())
                if edition_of(invoice) == edition and invoice != row["invoice"]
            }
            if len(found) != 1:
                if found:
                    self.stdout.write(self.style.WARNING(
                        f"  {row['invoice']} matches {len(found)} invoices for "
                        f"{row['email']}; left alone for a person to decide."
                    ))
                continue
            current = found.pop()
            moved.append((row["invoice"], current, row["email"]))
            row["invoice"] = current
        return moved

    # ── Writing ────────────────────────────────────────────────────────────

    def _overlay(self, collapsed: list[dict]) -> tuple[int, list[tuple]]:
        """
        Write the caller columns onto leads that already exist.

        Bucket, ownership rules and resolution are the engine's, so nothing here
        touches them beyond naming the owner the sheet recorded.
        """
        now = timezone.now()
        valid_types = {
            CreditControlLead.InvoiceType.BLIND.value,
            CreditControlLead.InvoiceType.REQUESTED.value,
        }
        keys = [row["invoice"] for row in collapsed]
        leads = CreditControlLead.objects.in_bulk(keys)
        # The settled bookings, for the create-into-Resolved case. Fetched in
        # one query for the whole batch rather than per orphan.
        settled = BookEvent.objects.in_bulk(
            [key for key in keys if key not in leads], field_name="invoice_number",
        )

        written, missing, revived = 0, [], 0
        for row in collapsed:
            lead = leads.get(row["invoice"])
            if lead is None:
                invoice = settled.get(row["invoice"])
                if invoice is None:
                    missing.append((row["invoice"], row["tab"], row["email"]))
                    continue
                lead = self._resolved_lead(invoice)
                revived += 1

            lead.assigned_to = row["owner"]
            lead.disposition = row["disposition"]
            lead.remark = row["remark"]
            lead.callback_date = row["callback"]
            fields = [
                "assigned_to", "disposition", "remark", "callback_date", "updated_at",
            ]
            # Only where the sheet has a stamp. A lead nobody ever worked has no
            # last-touched moment, and inventing one would put an untouched lead
            # into the dashboard's recent-activity reads.
            if row["updated"]:
                lead.last_touched_at = row["updated"]
                lead.last_touched_by = row["owner"]
                fields += ["last_touched_at", "last_touched_by"]

            if row["invoice_type"] in valid_types:
                lead.invoice_type = row["invoice_type"]
                # `model` and not `manual`. These verdicts were reasoned out by
                # the previous build's classifier, not decided by a person, and
                # `manual` is the one source the classifier will never overwrite.
                # Calling them manual would freeze a machine's guess forever.
                lead.invoice_type_source = CreditControlLead.TypeSource.MODEL
                lead.invoice_type_basis = row["type_notes"]
                lead.invoice_type_prompt_version = SHEET_PROMPT_VERSION
                lead.invoice_type_at = row["updated"] or now
                fields += [
                    "invoice_type", "invoice_type_source", "invoice_type_basis",
                    "invoice_type_prompt_version", "invoice_type_at",
                ]

            lead.save(update_fields=fields)
            written += 1
        if revived:
            self.stdout.write(
                f"{revived} invoices had already settled before this module "
                "existed, so their leads were created directly in Resolved."
            )
        return written, missing

    def _resolved_lead(self, invoice: BookEvent) -> CreditControlLead:
        """
        A lead for an invoice that settled before the module existed.

        force_insert for the same reason engine._create_lead needs it: the
        primary key is the invoice number, so a plain save() on a populated pk
        emits an UPDATE that matches nothing.

        The reason comes from the engine so the wording matches every other row
        in Resolved, and `resolved_after_effort` is True because the sheet is
        the evidence that somebody worked it; that flag is what keeps the
        attribution report from crediting a payment nobody chased.
        """
        lead = CreditControlLead(
            invoice_id=invoice.invoice_number,
            bucket=CreditControlLead.Bucket.DONE,
            resolved=bool(invoice.payment_date),
            done_reason=engine._done_reason(invoice),
            resolved_after_effort=True,
        )
        # Written even on a dry run. The whole command runs in one atomic block
        # that a dry run rolls back, so exercising the real path costs nothing
        # and a second, unsaved path would be the one thing the dry run never
        # tests.
        lead.save(force_insert=True)
        return lead

    def _activity(self, workbook, aliases: dict[str, str]) -> int:
        """
        The _Activity tab into CreditControlTouch.

        NOT the call count, which is a common misreading of this table. Times
        called comes from HubSpotContact and is already live; a touch is the
        record that somebody CHANGED a disposition, and this tab is 800 of them
        with the rep and the moment. Without it the table starts empty and the
        dashboard's seven-day matrix has nothing to fall back on when HubSpot
        call data is unavailable.

        Matched case-insensitively on the invoice half of the sheet's key, since
        that key is lowercased and invoice numbers in book_events are not.
        `aliases` carries the re-invoiced numbers `_remap` already resolved, so
        a call logged against the old number still lands on the lead that debt
        became. Rows whose lead does not exist are counted out, not guessed at.

        Idempotent by (lead, user, created_at): re-running does not double the
        log. There is no unique constraint carrying that, so it is enforced by
        reading what is already there, which is cheap against a table this size.
        """
        if ACTIVITY_TAB not in workbook.sheetnames:
            self.stdout.write(self.style.WARNING(f"No {ACTIVITY_TAB} tab; skipping touches."))
            return 0

        callers = self._callers()
        by_lower = {
            invoice.lower(): invoice
            for invoice in CreditControlLead.objects.values_list("invoice_id", flat=True)
        }
        by_lower.update({was.lower(): now_ for was, now_ in aliases.items()})
        seen = set(
            CreditControlTouch.objects.values_list("lead_id", "user_id", "created_at")
        )

        sheet = workbook[ACTIVITY_TAB]
        header = [
            str(cell).strip() if cell is not None else ""
            for cell in next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))
        ]
        index = {name: position for position, name in enumerate(header)}
        needed = ("Timestamp", "Rep", "Key", "Disposition")
        if any(name not in index for name in needed):
            raise CommandError(f"{ACTIVITY_TAB} is missing one of {needed}; found {header}.")

        batch, unmatched, unknown_reps = [], 0, set()
        for record in sheet.iter_rows(min_row=2, values_only=True):
            key = str(record[index["Key"]] or "")
            invoice = by_lower.get(key.split("||")[0].strip().lower())
            if invoice is None:
                unmatched += 1
                continue
            rep = str(record[index["Rep"]] or "").strip().lower()
            owner = callers.get(rep)
            if owner is None:
                unknown_reps.add(rep or "(blank)")
                continue
            stamp = sheet_datetime(record[index["Timestamp"]])
            if stamp is None:
                continue
            fingerprint = (invoice, owner.pk, stamp)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            batch.append(CreditControlTouch(
                lead_id=invoice,
                user=owner,
                # Raw, not cleaned. This is a log of what was logged at the
                # time, and rewriting history to the current label set would
                # make the audit trail disagree with the sheet it came from.
                disposition=str(record[index["Disposition"]] or "").strip(),
                fields_changed="disposition",
                created_at=stamp,
            ))

        if unmatched:
            self.stdout.write(
                f"  {unmatched} activity rows had no lead, most of them settled "
                "invoices that live on the sheet's Done tab."
            )
        if unknown_reps:
            self.stdout.write(self.style.WARNING(
                f"  Activity rows skipped for unknown reps: {', '.join(sorted(unknown_reps))}"
            ))
        CreditControlTouch.objects.bulk_create(batch, batch_size=500)
        return len(batch)
