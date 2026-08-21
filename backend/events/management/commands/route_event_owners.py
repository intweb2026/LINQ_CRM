"""
Resolve the SCA name stored on every event into the sales_executive FK that
grants visibility, and report every name that cannot be resolved.

Run this after adding accounts, and any time somebody reports an empty Events or
Bookings page. Start with --dry-run; it changes nothing and prints exactly what
the committing run would do.

    python manage.py route_event_owners --dry-run
    python manage.py route_event_owners
    python manage.py route_event_owners --user harrison.peck
    python manage.py route_event_owners --reassign        # also moves owned rows
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from events.owner_routing import route_events


class Command(BaseCommand):
    help = "Route events to their sales executive from the stored SCA name."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report only; write nothing.",
        )
        parser.add_argument(
            "--user", action="append", default=[],
            help="Limit changes to matches on this username or email. Repeatable.",
        )
        parser.add_argument(
            "--reassign", action="store_true",
            help=(
                "Also consider events that already have a sales executive, so a "
                "changed SCA name MOVES the event. Off by default; without it no "
                "event is ever taken away from its current owner."
            ),
        )
        parser.add_argument(
            "--limit-report", type=int, default=40,
            help="How many unresolved names to print per category. Default 40.",
        )

    def handle(self, *args, **options):
        User = get_user_model()

        users = None
        if options["user"]:
            users = []
            for ident in options["user"]:
                user = (
                    User.objects.filter(username__iexact=ident).first()
                    or User.objects.filter(email__iexact=ident).first()
                )
                if not user:
                    raise CommandError("no user matches " + ident)
                users.append(user)

        report = route_events(
            users=users,
            commit=not options["dry_run"],
            reassign=options["reassign"],
        )

        verb = "would route" if options["dry_run"] else "routed"
        self.stdout.write(
            "%s %d of %d events carrying an SCA name."
            % (verb, len(report["routed"]), report["considered"])
        )

        cap = options["limit_report"]
        for row in report["routed"][:cap]:
            self.stdout.write("  %s  %s" % (row["event_code"], row["user"]))
        if len(report["routed"]) > cap:
            self.stdout.write("  ... %d more" % (len(report["routed"]) - cap))

        # Printed loudly, because these are the rows that will STILL be invisible
        # after this run. Every one of them is a spelling a human has to fix, in
        # the event or in the user record.
        if report["ambiguous"]:
            self.stdout.write(self.style.WARNING(
                "\n%d events name somebody ambiguously and were left alone."
                % len(report["ambiguous"])
            ))
            for row in report["ambiguous"][:cap]:
                self.stdout.write("  %s  %r matches more than one account"
                                  % (row["event_code"], row["name"]))

        if report["unmatched"]:
            self.stdout.write(self.style.WARNING(
                "\n%d events name somebody with no account and were left alone."
                % len(report["unmatched"])
            ))
            names = sorted({row["name"] for row in report["unmatched"]})
            for name in names[:cap]:
                count = sum(1 for r in report["unmatched"] if r["name"] == name)
                self.stdout.write("  %r on %d event(s)" % (name, count))
            if len(names) > cap:
                self.stdout.write("  ... %d more distinct names" % (len(names) - cap))

        if options["dry_run"]:
            self.stdout.write(self.style.NOTICE("\nDry run. Nothing was written."))
