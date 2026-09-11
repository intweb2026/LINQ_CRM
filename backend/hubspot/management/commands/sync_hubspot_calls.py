"""
sync_hubspot_calls
──────────────────
Pull call engagements, and refresh the per-contact call totals for whoever is
on a queue right now.

SEPARATE FROM refresh_credit_control ON PURPOSE, and it is a cadence argument
rather than a tidiness one. Routing only needs to run around the edges of a
shift, because a lead's bucket and owner change when the source changes, not
while somebody is dialling. Call data is the opposite: it moves continuously
through the shift, and a manager watching the dashboard mid-evening wants it
current. Same code, two clocks.

Incremental, so an hourly run costs a page of new calls rather than a window.
Safe to run repeatedly and safe to run alongside a routing pass; every write is
an upsert on HubSpot's own call id.
"""
from django.core.management.base import BaseCommand

from credit_control.models import CreditControlLead
from hubspot import client, services


class Command(BaseCommand):
    help = "Pull recent HubSpot calls and refresh per-contact call totals."

    def add_arguments(self, parser):
        parser.add_argument(
            "--calls-only", action="store_true",
            help="Skip the per-contact totals, which are the expensive half.",
        )

    def handle(self, *args, **options):
        if not client.enabled():
            self.stdout.write(self.style.WARNING(
                "HUBSPOT_TOKEN is not set. Nothing to do."
            ))
            return

        calls = services.sync_calls()
        self.stdout.write(self.style.SUCCESS(
            f"Calls: {calls['written']} written of {calls['fetched']} "
            f"fetched since {calls['since']}"
        ))
        if calls["error"]:
            self.stdout.write(self.style.WARNING(f"  {calls['error']}"))

        if options["calls_only"]:
            return

        # One request per contact, so it is bounded and refreshes the stalest
        # first. The rest wait for the next run, which converges because the
        # ones skipped now are the stalest then.
        counts = services.sync_call_counts(self._active_emails())
        self.stdout.write(
            f"Call totals: {counts['updated']} updated of {counts['considered']} stale"
            + (f" [{counts['error']}]" if counts["error"] else "")
        )

    def _active_emails(self):
        """The addresses on somebody's queue, which is who these figures are for."""
        rows = (
            CreditControlLead.objects
            .filter(bucket=CreditControlLead.Bucket.ACTIVE)
            .values_list("invoice__accounts_contact_email", "invoice__contact_email")
        )
        found = set()
        for accounts_email, contact_email in rows:
            for candidate in (accounts_email, contact_email):
                if candidate and candidate.strip():
                    found.add(candidate.strip().lower())
                    break
        return sorted(found)
