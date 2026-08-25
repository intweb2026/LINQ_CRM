"""
book_event/management/commands/sync_booking_event_fields.py

Re-derive, on every booking, the two values that are OWNED BY THE EVENT and
only copied onto the invoice:

    event_name       the catalogue name of the event the booking's event_code
                     names, with the booking's own edition year appended, which
                     is exactly the rule BookEvent.save() applies today
                     (book_event/models.py).
    sales_executive  the SCA. The Bookings modal shows SCA read-only because the
                     text belongs to events.sales_team; the column that actually
                     decides who OWNS and who can SEE a booking is
                     BookEvent.sales_executive, and that is what this writes.

WHY A SCRIPT RATHER THAN A RE-SAVE
Both values are derived on write, so a booking carries whatever the catalogue
said on the day it was last saved. An event renamed afterwards, or an SCA
assigned afterwards, never reaches the invoices already in the table. This walks
them all and brings them back in step.

WHY queryset.update() AND NOT save()
BookEvent.save() does more than derive these two values: it re-parses event_code,
strips its trailing year into `edition`, canonicalises the code against the
closed booking-code list, and rewrites `booked_on` on every delegate of the
invoice. None of that is asked for here, and on a production table a backfill
that quietly rewrites identifiers is not a backfill. This writes the two named
columns and nothing else, in batches, one transaction per batch.

SAFETY
Dry run is the DEFAULT. Nothing is written without --commit, and a dry run
prints the same report the committing run would, so the diff can be read first.

Usage:
    python manage.py sync_booking_event_fields                       # dry run
    python manage.py sync_booking_event_fields --commit
    python manage.py sync_booking_event_fields --event-code ACU --commit
    python manage.py sync_booking_event_fields --fields event_name --commit
    python manage.py sync_booking_event_fields --only-missing --commit
    python manage.py sync_booking_event_fields --report changes.csv
"""
import csv
import re
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from accounts.models import User
from accounts.user_resolution import OwnerResolver, is_blank_name
from book_event.models import BookEvent
from events.models import Event


def base_code(code):
    """
    A catalogue or booking event_code with its trailing owner-initials segment
    removed, upper-cased: "REU - RS" -> "REU", "BIU/GS - PM" -> "BIU/GS".

    Only a segment of ONE TO THREE LETTERS after a hyphen is removed, which is
    the shape those initials take. Nothing else about the code is touched, so a
    code that carries no such suffix survives unchanged and still compares as
    itself.
    """
    return re.sub(r"\s*-\s*[A-Za-z]{1,3}$", "", (code or "").upper().strip()).strip()


def clean_event_name(name, edition):
    """
    The catalogue name with any trailing 4-digit year removed and the booking's
    own edition appended; the same rule BookEvent.save() applies, kept in one
    function so the two cannot drift apart silently.
    """
    base = re.sub(r"\s*\d{4}$", "", name or "").strip()
    if not base:
        return ""
    return "%s %s" % (base, edition) if edition else base


class Command(BaseCommand):
    help = (
        "Re-derive event_name and the SCA (sales_executive) on bookings from the "
        "Events catalogue, keyed on event_code. Dry run unless --commit."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit", action="store_true",
            help="Write the changes. Without it the command reports and exits.",
        )
        parser.add_argument(
            "--fields", default="event_name,sales_executive",
            help="Comma-separated subset: event_name, sales_executive. Default both.",
        )
        parser.add_argument(
            "--event-code", action="append", default=[],
            help="Limit to this booking event_code. Repeatable. Case-insensitive.",
        )
        parser.add_argument(
            "--exact-only", action="store_true",
            help=(
                "Match ONLY on the full event_code, skipping the owner-initials "
                "base tier. Leaves every booking whose code lacks the catalogue's "
                "' - XX' suffix unmatched; useful to see that split on its own."
            ),
        )
        parser.add_argument(
            "--only-missing", action="store_true",
            help=(
                "Only FILL blanks; never change a value that is already set. The "
                "conservative first pass on a production table."
            ),
        )
        parser.add_argument(
            "--batch-size", type=int, default=500,
            help="Rows per transaction. Default 500.",
        )
        parser.add_argument(
            "--report", default="",
            help="Write every proposed change to this CSV path.",
        )
        parser.add_argument(
            "--limit-print", type=int, default=25,
            help="How many sample changes to print per column. Default 25.",
        )

    # -- event lookup ---------------------------------------------------------

    def build_event_map(self):
        """
        Three maps over the whole catalogue, built in ONE query.

        exact      event_code as stored.
        by_upper   event_code upper-cased. Upper-casing both sides is what the
                   rest of the codebase means by matching a stored booking code
                   to a catalogue code (events/name_lookup.py).
        by_base    event_code with its trailing OWNER-INITIALS segment removed:
                   "REU - RS" -> "REU", "CCC - VV" -> "CCC", "BISG - PM" ->
                   "BISG". This is the shape difference between the two tables.
                   The catalogue stores the initials of the person who owns the
                   event on the end of the code; a booking stores the bare base,
                   because BookEvent.save() writes it that way. Matching on the
                   base is therefore not a guess — it is the same identifier with
                   one table's suffix removed.

        `ambiguous_bases` is what makes the base tier safe to run by default. A
        base is only usable if EXACTLY ONE catalogue row carries it; two rows
        sharing a base means the suffix is the only thing telling them apart, and
        the booking, which has no suffix, cannot say which one it meant. Those
        bases are excluded from by_base and reported instead of guessed.
        """
        events = list(
            Event.objects
            .select_related("sales_executive")
            .only("id", "event_code", "name", "sales_team", "sales_executive")
        )
        by_upper = {}
        for event in events:
            by_upper.setdefault((event.event_code or "").upper(), event)
        exact = {e.event_code: e for e in events if e.event_code}

        grouped = {}
        for event in events:
            grouped.setdefault(base_code(event.event_code), []).append(event)
        by_base = {b: rows[0] for b, rows in grouped.items() if b and len(rows) == 1}
        ambiguous_bases = {
            b: [e.event_code for e in rows]
            for b, rows in grouped.items() if b and len(rows) > 1
        }
        return exact, by_upper, by_base, ambiguous_bases

    def assigned_user(self, event_code, cache):
        if event_code not in cache:
            cache[event_code] = (
                User.objects
                .filter(role=User.Role.SALES, assigned_events__event_code=event_code)
                .first()
            )
        return cache[event_code]

    def resolve_sca(self, event, resolver, assigned_cache):
        """
        The user the SCA names, in the precedence BookEvent.auto_assign_sales
        already uses, with the event's SCA TEXT added as the middle step:

          1. Event.sales_executive - the FK the Events tab maintains.
          2. Event.sales_team - the SCA text, resolved through OwnerResolver. An
             ambiguous or unknown name resolves to nobody and the booking is LEFT
             ALONE; guessing here hands one person's revenue to another.
          3. User.assigned_events - the older m2m, kept as the last fallback.

        Returns (user_or_None, reason_or_None).
        """
        if event.sales_executive_id:
            return event.sales_executive, None

        if not is_blank_name(event.sales_team):
            user, reason = resolver.resolve(event.sales_team)
            if user is not None:
                return user, None
            fallback = self.assigned_user(event.event_code, assigned_cache)
            if fallback is not None:
                return fallback, None
            return None, reason or "sca-name-unmatched"

        fallback = self.assigned_user(event.event_code, assigned_cache)
        if fallback is not None:
            return fallback, None
        return None, "event-has-no-sca"

    # -- main -----------------------------------------------------------------

    def handle(self, *args, **options):
        wanted = {f.strip() for f in options["fields"].split(",") if f.strip()}
        unknown = wanted - {"event_name", "sales_executive"}
        if unknown:
            raise CommandError("unknown field(s): " + ", ".join(sorted(unknown)))
        if not wanted:
            raise CommandError("--fields resolved to nothing")

        commit = options["commit"]
        only_missing = options["only_missing"]
        batch_size = max(1, options["batch_size"])
        cap = options["limit_print"]

        (exact_events, events_by_upper,
         events_by_base, ambiguous_bases) = self.build_event_map()
        self.stdout.write(
            "Loaded %d events from the catalogue; %d have a base code unique to them."
            % (len(events_by_upper), len(events_by_base))
        )

        resolver = OwnerResolver()
        assigned_cache = {}

        qs = BookEvent.objects.select_related("sales_executive").only(
            "id", "invoice_number", "event_code", "edition",
            "event_name", "sales_executive",
        )
        codes = [c.strip() for c in options["event_code"] if c.strip()]
        if codes:
            match = Q()
            for code in codes:
                match |= Q(event_code__iexact=code)
            qs = qs.filter(match)
        qs = qs.order_by("id")

        total = qs.count()
        self.stdout.write("Scanning %d bookings.\n" % total)

        changes = []          # (pk, invoice, code, column, old, new)
        pending = []          # (pk, {column: value})
        skipped = Counter()
        no_event = Counter()        # booking event_code -> count
        base_hits = Counter()       # "booking code -> catalogue code" -> count
        ambiguous_rows = Counter()  # base that names more than one event -> count
        touched = Counter()   # column -> rows changed
        written = [0]

        def flush():
            """
            Write one batch. Rows are grouped by the identical set of column
            values so each distinct update shape costs ONE statement rather than
            one per row.
            """
            if not commit or not pending:
                pending.clear()
                return
            groups = {}
            for pk, values in pending:
                key = tuple(sorted(values.items(), key=lambda kv: kv[0]))
                groups.setdefault(key, []).append(pk)
            with transaction.atomic():
                for key, pks in groups.items():
                    BookEvent.objects.filter(pk__in=pks).update(**dict(key))
                    written[0] += len(pks)
            pending.clear()

        exact_only = options["exact_only"]

        for booking in qs.iterator(chunk_size=1000):
            code = booking.event_code or ""
            event = exact_events.get(code) or events_by_upper.get(code.upper())
            if event is None and not exact_only and code:
                bcode = base_code(code)
                event = events_by_base.get(bcode)
                if event is not None:
                    base_hits["%s -> %s" % (code, event.event_code)] += 1
                elif bcode in ambiguous_bases:
                    ambiguous_rows["%s -> %s" % (code, "/".join(ambiguous_bases[bcode]))] += 1
            if event is None:
                no_event[code or "(blank)"] += 1
                continue

            values = {}

            if "event_name" in wanted:
                new_name = clean_event_name(event.name, booking.edition)
                current = booking.event_name or ""
                if new_name and new_name != current:
                    if only_missing and current:
                        skipped["event_name-already-set"] += 1
                    else:
                        values["event_name"] = new_name
                        changes.append((booking.pk, booking.invoice_number, code,
                                        "event_name", current, new_name))

            if "sales_executive" in wanted:
                user, reason = self.resolve_sca(event, resolver, assigned_cache)
                if user is None:
                    skipped[reason or "sca-unresolved"] += 1
                elif user.pk != booking.sales_executive_id:
                    if only_missing and booking.sales_executive_id:
                        skipped["sales_executive-already-set"] += 1
                    else:
                        if booking.sales_executive_id:
                            old = (booking.sales_executive.get_full_name()
                                   or booking.sales_executive.username)
                        else:
                            old = ""
                        new = user.get_full_name() or user.username
                        values["sales_executive"] = user
                        changes.append((booking.pk, booking.invoice_number, code,
                                        "sales_executive", old, new))

            if not values:
                continue
            for column in values:
                touched[column] += 1
            pending.append((booking.pk, values))
            if len(pending) >= batch_size:
                flush()

        flush()

        # -- report -----------------------------------------------------------
        verb = "changed" if commit else "would change"
        self.stdout.write("")
        for column in sorted(wanted):
            self.stdout.write(self.style.SUCCESS(
                "%s %d bookings on %s" % (verb, touched[column], column)
            ))

        for column in sorted(wanted):
            rows = [c for c in changes if c[3] == column]
            if not rows:
                continue
            self.stdout.write("\n  %s:" % column)
            for _pk, invoice, code, _col, old, new in rows[:cap]:
                self.stdout.write("    %-20s %-18s %r -> %r"
                                  % (invoice, code, old or "", new))
            if len(rows) > cap:
                self.stdout.write("    ... %d more" % (len(rows) - cap))

        if base_hits:
            self.stdout.write(
                "\n%d bookings matched on the base code, the catalogue's owner "
                "initials removed, across %d code(s):"
                % (sum(base_hits.values()), len(base_hits))
            )
            for pair, count in sorted(base_hits.items(), key=lambda kv: -kv[1])[:cap]:
                self.stdout.write("    %s  on %d booking(s)" % (pair, count))
            if len(base_hits) > cap:
                self.stdout.write("    ... %d more pairings" % (len(base_hits) - cap))

        if ambiguous_rows:
            self.stdout.write(self.style.WARNING(
                "\n%d bookings name a base that MORE THAN ONE event carries, so "
                "the booking cannot say which, and were left alone:"
                % sum(ambiguous_rows.values())
            ))
            for pair, count in sorted(ambiguous_rows.items(), key=lambda kv: -kv[1])[:cap]:
                self.stdout.write("    %s  on %d booking(s)" % (pair, count))

        if no_event:
            self.stdout.write(self.style.WARNING(
                "\n%d bookings across %d event_code(s) match NO event and were "
                "left alone:" % (sum(no_event.values()), len(no_event))
            ))
            for code, count in sorted(no_event.items(), key=lambda kv: -kv[1])[:cap]:
                near = sorted(
                    other for other in events_by_base
                    if other.startswith(base_code(code)[:3])
                    or base_code(code).startswith(other[:3])
                )
                hint = ("  nearest catalogue code(s): " + ", ".join(near[:4])) if near else ""
                self.stdout.write("    %r on %d booking(s)%s" % (code, count, hint))
            if len(no_event) > cap:
                self.stdout.write("    ... %d more codes" % (len(no_event) - cap))
            self.stdout.write(
                "  These codes are ABSENT from the Events catalogue. Add the event, "
                "or correct the booking's code, then re-run.\n"
                "  `python manage.py audit_event_code_mapping` writes the full list."
            )

        if skipped:
            self.stdout.write("\nLeft alone:")
            for reason, count in sorted(skipped.items(), key=lambda kv: -kv[1]):
                self.stdout.write("    %s: %d" % (reason, count))

        if options["report"]:
            path = options["report"]
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["booking_id", "invoice_number", "event_code",
                                 "column", "old_value", "new_value"])
                writer.writerows(changes)
            self.stdout.write(self.style.SUCCESS("\nReport written -> %s" % path))

        if commit:
            self.stdout.write(self.style.SUCCESS(
                "\nWrote %d booking rows." % written[0]
            ))
        else:
            self.stdout.write(self.style.NOTICE(
                "\nDry run. Nothing was written. Re-run with --commit to apply."
            ))
