"""
sync_mailable_counts
────────────────────
How many HubSpot contacts sit under each event code, and how many of those are
mailable. Read by the Mining Matrix's Mailable column.

TWO REQUESTS PER CODE, NOT ONE PER CONTACT. The contacts search returns a
`total` beside the page, so asking for a single row answers a count of ninety
thousand contacts in one call. `mailable` is tested with HAS_PROPERTY rather
than against a value, because the property is an enumeration with a single
option, so the real question is whether marketing has classified the contact at
all. That is what "mailable is known" means.

WHICH CODES. By default the canonical purpose codes the matrix actually shows,
which is the set Ticket Central files work under, so the run scales with the
live catalogue rather than with the 500 options the HubSpot property carries.
Pass codes explicitly to refresh a few by hand.

Only the missing and the stale are fetched, so a nightly run settles down to
the handful that moved. Safe to run repeatedly.
"""
from django.core.management.base import BaseCommand

from hubspot import client, services


class Command(BaseCommand):
    help = "Refresh HubSpot mailable contact counts per event code."

    def add_arguments(self, parser):
        parser.add_argument(
            "codes", nargs="*",
            help="Purpose codes to refresh. Defaults to what the matrix shows.",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Refetch even where the cached count is still fresh.",
        )

    def handle(self, *args, **options):
        if not client.enabled():
            self.stdout.write(self.style.WARNING(
                "HUBSPOT_TOKEN is not set. Nothing to do."
            ))
            return

        codes = options["codes"] or self._matrix_codes()
        if not codes:
            self.stdout.write("No purpose codes to count.")
            return

        result = services.sync_mailable_counts(codes, force=options["force"])
        self.stdout.write(self.style.SUCCESS(
            f"Mailable counts: {result['fetched']} fetched, "
            f"{result['skipped_fresh']} already fresh, of {result['asked']} codes"
        ))
        if result["error"]:
            self.stdout.write(self.style.WARNING(f"  {result['error']}"))

    def _matrix_codes(self):
        """
        The canonical codes the Mining Matrix groups by.

        Resolved from the Events catalogue through the same `canonical_code`
        the matrix keys its rows on, so the counts line up with the column
        rather than nearly lining up.

        DELIBERATELY NOT `unmined_by_purpose`, which is the obvious-looking
        source and the wrong one twice over. It is RBAC-scoped to a caller, and
        a cron job has none, so it answered with an empty set and this command
        counted nothing at all. It is also scoped to UNMINED tickets, so a code
        whose mining is finished would drop out of the cache and its column
        would go blank on a row that is otherwise complete.
        """
        from events.models import Event
        from mining_matrix.codes import known_purpose_codes, resolve_codes

        known = known_purpose_codes()
        resolved = resolve_codes(
            [c for c in Event.objects.values_list("event_code", flat=True) if c],
            known,
        )
        return sorted({code for code in resolved.values() if code})
