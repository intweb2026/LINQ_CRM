"""
classify_invoice_types
──────────────────────
Blind Invoice or Requested, for speaker leads, and the handover briefs.

Separate from refresh_credit_control on purpose. Routing must run in a second
with no credentials; this one talks to HubSpot and Anthropic and is allowed to
take its time. If it fails, or is never run, every queue still works and the
speaker rows simply read Unclassified, which is the honest state.

--check is the mode to run FIRST, before this is ever pointed at live data. It
scores the classifier against rows that already carry a human verdict and
reports the agreement rate without writing anything, which is what turns an
existing hand-labelled set into a measurement rather than a hope. Run it, read
every disagreement, and only then let the writing mode loose.
"""
from django.core.management.base import BaseCommand

from credit_control import classifier
from credit_control.constants import CLASSIFIER_MAX_PER_RUN
from credit_control.models import CreditControlLead


class Command(BaseCommand):
    help = "Classify speaker invoices as Blind or Requested, and write handoff briefs."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=CLASSIFIER_MAX_PER_RUN)
        parser.add_argument(
            "--check", action="store_true",
            help="Score against rows that already carry a human verdict. Writes nothing.",
        )
        parser.add_argument(
            "--briefs", action="store_true",
            help="Only write handoff briefs for leads that changed hands.",
        )

    def handle(self, *args, **options):
        if not classifier.enabled():
            self.stdout.write(self.style.WARNING(
                "ANTHROPIC_API_KEY is not set. Nothing to do."
            ))
            return

        if options["check"]:
            self._check(options["limit"])
            return

        if options["briefs"]:
            tally = classifier.write_pending_briefs(limit=options["limit"])
            self.stdout.write(self.style.SUCCESS(
                f"Briefs: {tally['written']} written, {tally['empty']} left blank, "
                f"of {tally['considered']} handed-over leads"
            ))
            return

        tally = classifier.classify_pending(limit=options["limit"])
        self.stdout.write(self.style.SUCCESS(
            f"Considered {tally['considered']}: "
            f"{tally.get('rule', 0)} by rule, {tally.get('classified', 0)} classified, "
            f"{tally.get('unsure', 0)} low confidence, "
            f"{tally.get('ungrounded', 0)} quote not found, {tally.get('error', 0)} errored"
        ))
        if tally.get("unsure") or tally.get("ungrounded"):
            self.stdout.write(
                "  Those rows are left Unclassified for a human, with the reason "
                "stored on the lead. That is the intended outcome, not a failure."
            )

    def _check(self, limit):
        """
        Measure the classifier against verdicts a human already gave.

        Reports agreement and lists every disagreement, because the
        disagreements are the useful output: each one is either a rubric that
        needs another sentence or a label that needs a second look.
        """
        rows = list(
            CreditControlLead.objects
            .select_related("invoice")
            .exclude(invoice_type_override="")[:limit]
        )
        if not rows:
            self.stdout.write(
                "No rows carry a human verdict yet. Import the existing "
                "hand-classified set, or set some Invoice Type overrides, then re-run."
            )
            return

        agree = 0
        disagreements = []
        for lead in rows:
            packet = classifier.gather_evidence(lead)
            ruled = classifier.rule_verdict(lead, packet)
            if ruled:
                verdict, basis = ruled
            else:
                verdict, basis = self._dry_verdict(lead, packet)
            if verdict == lead.invoice_type_override:
                agree += 1
            else:
                disagreements.append(
                    (lead.invoice_id, lead.invoice_type_override, verdict, basis)
                )

        total = len(rows)
        self.stdout.write(self.style.SUCCESS(
            f"Agreement {agree} of {total}, {agree / total:.0%}"
        ))
        for invoice, human, machine, basis in disagreements:
            self.stdout.write(
                f"  {invoice}: human {human}, machine {machine or 'none'} ({basis})"
            )

    def _dry_verdict(self, lead, packet):
        """
        One classification that is scored and thrown away.

        dry_run is what makes this safe: classify() computes the verdict and
        writes nothing, so scoring cannot touch the very column it is being
        scored against.
        """
        try:
            result = classifier.classify(lead, packet, dry_run=True)
        except Exception as exc:  # noqa: BLE001
            return "", f"error: {exc}"
        return result.get("verdict", ""), result.get("detail", "")
