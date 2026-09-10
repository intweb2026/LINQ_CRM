"""
refresh_credit_control
──────────────────────
The routing pass, plus the HubSpot cache refresh for the leads it produced.

Run on cron (see CRONJOBS in settings.py) and reachable from the Refresh button
on the page. Idempotent, so a second run right after the first changes nothing.

ORDER MATTERS. Routing first, then the caches, because the caches only want the
addresses that are on somebody's queue right now, and routing is what decides
that. The reverse order would refresh phone numbers for leads that had just
left the flow.

The HubSpot steps are skipped silently without a token, and a HubSpot failure
never fails the command: the routing has already been committed by then, and
that is the part a shift depends on.
"""
import logging

from django.core.management.base import BaseCommand

from credit_control import engine
from credit_control.models import CreditControlDisposition, CreditControlLead

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Re-route Credit Control leads and refresh the HubSpot cache for them."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-hubspot", action="store_true",
            help="Routing only. Leaves phones and call counts as they are.",
        )
        parser.add_argument(
            "--force-contacts", action="store_true",
            help="Refetch every contact even if the cached row is still fresh.",
        )

    def handle(self, *args, **options):
        seeded = CreditControlDisposition.seed()
        if seeded:
            self.stdout.write(f"Seeded {seeded} dispositions.")

        summary = engine.refresh()
        self.stdout.write(self.style.SUCCESS(
            "Routed: "
            f"{summary['active']} active, {summary['not_invoiced']} not invoiced, "
            f"{summary['spex']} SpEx, {summary['done']} done, "
            f"{summary['excluded']} excluded by verdict"
        ))
        self.stdout.write(
            f"  created {summary['created']}, handed off {summary['handed_off']}, "
            f"reactivated {summary['reactivated']}, unassigned {summary['unassigned']}"
        )
        roster = engine.Roster.load()
        self.stdout.write(
            f"Roster: lead {roster.lead.username if roster.lead else 'NONE'}, "
            f"pool {[u.username for u in roster.pool]}"
        )
        for person, why in roster.skipped:
            self.stdout.write(self.style.WARNING(
                f"  Skipped {person.username}: {why}"
            ))
        if roster.lead is None:
            self.stdout.write(self.style.WARNING(
                "  No team lead, so nothing can be given a first touch. Set the "
                "team's lead on the Teams screen."
            ))

        if summary["unassigned"]:
            self.stdout.write(self.style.WARNING(
                "  Some active leads have no owner. Check the Credit Control team "
                "exists, has a team lead, and that its members can log in."
            ))

        if options["skip_hubspot"]:
            return

        from hubspot import services as hs

        emails = self._active_emails()
        contacts = hs.sync_contacts(emails, force=options["force_contacts"])
        self.stdout.write(
            f"HubSpot contacts: asked {contacts['asked']}, fetched {contacts['fetched']}, "
            f"fresh {contacts['skipped_fresh']}"
            + (f" [{contacts['error']}]" if contacts["error"] else "")
        )

        counts = hs.sync_call_counts(emails)
        self.stdout.write(
            f"HubSpot call counts: {counts['updated']} updated of "
            f"{counts['considered']} stale"
            + (f" [{counts['error']}]" if counts["error"] else "")
        )

        calls = hs.sync_calls()
        self.stdout.write(
            f"HubSpot calls: {calls['written']} written of {calls['fetched']} "
            f"since {calls['since']}"
            + (f" [{calls['error']}]" if calls["error"] else "")
        )

    def _active_emails(self):
        """
        The addresses on somebody's queue: the accounts contact we actually
        call, with the delegate address as the fallback the queue also shows.
        """
        rows = (
            CreditControlLead.objects
            .filter(bucket=CreditControlLead.Bucket.ACTIVE)
            .select_related("invoice")
            .values_list("invoice__accounts_contact_email", "invoice__contact_email")
        )
        found = set()
        for accounts_email, contact_email in rows:
            for candidate in (accounts_email, contact_email):
                if candidate and candidate.strip():
                    found.add(candidate.strip().lower())
                    break
        return sorted(found)
