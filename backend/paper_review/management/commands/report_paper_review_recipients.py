"""
paper_review/management/commands/report_paper_review_recipients.py
──────────────────────────────────────────────────────────────────
B2 — read-only recipient-resolution audit.

For every event code actually present in paper_reviews and proposal_submissions,
reports what paper_review/notifications.py:resolve_recipients() would resolve —
To, Cc, and the outcome — WITHOUT sending anything and without depending on
PAPER_REVIEW_NOTIFICATIONS_ENABLED (this bypasses _notify entirely; it calls
resolve_recipients() directly, so it tells the truth regardless of the kill
switch's position).

WHY THIS EXISTS
B1's kill switch makes a UAT create safe, but it does not answer the actual
question standing between here and turning the switch on: is Event.sales_executive
/ assigned_users populated well enough, across the REAL catalogue, that most
events resolve cleanly? This command is how that gets confirmed before B1 is
flipped to True anywhere it matters.

READ-ONLY, GUARANTEED
No .save(), no .create(), no email send — resolve_recipients() takes an event
code and reads Event/User, nothing else. An UNSAVED PaperReview(event_code=code)
is used to drive it: resolve_recipients() only ever touches `review.event_code`,
so building one in memory (never persisted) reuses the exact production logic
without a throwaway DB row.

Usage:
    python manage.py report_paper_review_recipients
    python manage.py report_paper_review_recipients --format csv
"""
import csv
from collections import Counter

from django.core.management.base import BaseCommand
from django.db.models import Q

from events.models import Event
from paper_review.models import PaperReview
from paper_review.notifications import (
    CC_ROLES, STEP_EVENT_NOT_FOUND, STEP_NO_EVENT_CODE, resolve_recipients,
)
from proposal_submission.models import ProposalSubmission

# The command's own outcome vocabulary — one step finer than Recipients itself,
# because "the event doesn't exist" is a data problem (fix the stored code) while
# "the event exists but nobody is assigned" is a staffing problem (assign a sales
# executive) — conflating them under one "fallback" bucket would point whoever
# reads this report at the wrong fix.
RESOLVED         = "resolved"
DEGRADED         = "degraded"
FALLBACK         = "fallback"
EVENT_NOT_FOUND  = "event_not_found"


def classify(recipients):
    if recipients.failure_step in (STEP_NO_EVENT_CODE, STEP_EVENT_NOT_FOUND):
        return EVENT_NOT_FOUND
    if recipients.is_fallback:
        return FALLBACK
    if recipients.failure_step:
        return DEGRADED
    return RESOLVED


def distinct_event_codes(scope="all"):
    """
    Event codes to report on, sorted.

    scope="pipeline" — only codes actually used by paper_reviews /
        proposal_submissions. Answers "are the reviews we already hold
        deliverable?"
    scope="all" (default, D1) — every code in the Event catalogue, UNION the
        pipeline codes. Answers the question that actually gates flipping
        PAPER_REVIEW_NOTIFICATIONS_ENABLED: is sales_executive populated widely
        enough across the catalogue that the agreed To: recipient exists at all?
        Restricting to pipeline codes would have answered that from a sample of
        one, which is how the previous pass reached a conclusion it could not
        support.

    The pipeline codes are unioned in rather than replaced so a stored code with
    NO catalogue match still appears as EVENT_NOT_FOUND instead of vanishing from
    the report.
    """
    codes = set(PaperReview.objects.values_list("event_code", flat=True))
    codes |= set(ProposalSubmission.objects.values_list("event_code", flat=True))
    if scope == "all":
        codes |= set(Event.objects.values_list("event_code", flat=True))
    codes.discard("")
    codes.discard(None)
    return sorted(codes)


def data_readiness():
    """
    The two raw counts D1 asks for, straight off the Event catalogue — reported
    separately from the resolution outcomes because they are the underlying cause:
    an event with neither a sales_executive nor an assigned CC-role user cannot
    resolve, and no amount of resolution logic will change that.
    """
    total = Event.objects.count()
    with_exec = Event.objects.filter(sales_executive__isnull=False).count()
    with_cc = (
        Event.objects.filter(assigned_users__role__in=CC_ROLES)
        .distinct().count()
    )
    with_either = (
        Event.objects.filter(
            Q(sales_executive__isnull=False)
            | Q(assigned_users__role__in=CC_ROLES)
        ).distinct().count()
    )
    return {
        "total": total,
        "with_sales_executive": with_exec,
        "with_cc_role_assignee": with_cc,
        "with_either": with_either,
        "with_neither": total - with_either,
    }


class Command(BaseCommand):
    help = (
        "Read-only: report what paper_review's production-team notification "
        "would resolve (to/cc/outcome) for every event code currently used in "
        "paper_reviews and proposal_submissions. Sends nothing."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--format", choices=["text", "csv"], default="text",
            help="text (default, human-readable) or csv (to stdout, pipeable)",
        )
        parser.add_argument(
            "--scope", choices=["all", "pipeline"], default="all",
            help="all (default): every event in the catalogue, which is what "
                 "decides whether the notification design is viable. "
                 "pipeline: only codes present in paper_reviews / "
                 "proposal_submissions.",
        )
        parser.add_argument(
            "--only", choices=["resolved", "degraded", "fallback",
                               "event_not_found"],
            help="Show only rows with this outcome. The summary still counts all.",
        )

    def handle(self, *args, **options):
        codes = distinct_event_codes(options["scope"])
        rows = []
        for code in codes:
            review = PaperReview(event_code=code)          # UNSAVED — read-only
            recipients = resolve_recipients(review)
            rows.append({
                "event_code": code,
                "outcome": classify(recipients),
                "to": ", ".join(recipients.to),
                "cc": ", ".join(recipients.cc),
                "note": recipients.failure_step or "",
            })

        if options["format"] == "csv":
            writer = csv.DictWriter(
                self.stdout, fieldnames=["event_code", "outcome", "to", "cc", "note"])
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
            return

        counts = Counter(row["outcome"] for row in rows)
        readiness = data_readiness()
        scope_label = ("the whole Event catalogue"
                       if options["scope"] == "all"
                       else "paper_reviews + proposal_submissions only")

        self.stdout.write(f"{len(rows)} event code(s) — scope: {scope_label}.\n")

        shown = [r for r in rows
                 if not options["only"] or r["outcome"] == options["only"]]
        for row in shown:
            tag = {
                RESOLVED: self.style.SUCCESS("RESOLVED        "),
                DEGRADED: self.style.WARNING("DEGRADED        "),
                FALLBACK: self.style.WARNING("FALLBACK        "),
                EVENT_NOT_FOUND: self.style.ERROR("EVENT_NOT_FOUND "),
            }[row["outcome"]]
            self.stdout.write(f"{tag} {row['event_code']!r:30s} "
                              f"to=[{row['to']}] cc=[{row['cc']}]"
                              + (f"  ({row['note']})" if row["note"] else ""))
        if options["only"]:
            self.stdout.write(
                f"\n({len(shown)} of {len(rows)} shown — filtered to "
                f"{options['only']}.)")

        # D1 — the underlying data, reported before the outcomes, because the
        # outcomes are a consequence of it.
        self.stdout.write("")
        self.stdout.write("Event catalogue readiness:")
        self.stdout.write(f"  events in catalogue                     "
                          f"{readiness['total']}")
        self.stdout.write(f"  with a sales_executive (the To:)         "
                          f"{readiness['with_sales_executive']}")
        self.stdout.write(f"  with a speaker_sales/market_research     "
                          f"{readiness['with_cc_role_assignee']}")
        self.stdout.write(f"    assigned user (the Cc:)")
        self.stdout.write(f"  with at least one of the two            "
                          f"{readiness['with_either']}")
        # ASCII here deliberately: the Windows console this is run from renders an
        # em-dash as a replacement character, and a readiness report that looks
        # corrupted invites doubt about the numbers next to it.
        self.stdout.write(f"  with NEITHER - cannot resolve at all     "
                          f"{readiness['with_neither']}")

        self.stdout.write("")
        self.stdout.write("Resolution outcomes:")
        for outcome in (RESOLVED, DEGRADED, FALLBACK, EVENT_NOT_FOUND):
            self.stdout.write(f"  {outcome:16s} {counts.get(outcome, 0)}")

        # The verdict D1 asks for, stated rather than left to the reader.
        total = readiness["total"]
        if total:
            exec_pct = 100.0 * readiness["with_sales_executive"] / total
            self.stdout.write("")
            if readiness["with_sales_executive"] == 0:
                self.stdout.write(self.style.ERROR(
                    "VERDICT: NO event has a sales_executive. The agreed To: "
                    "recipient (the assigned sales rep) does not exist in this "
                    "data at all — every notification would resolve via the Cc "
                    "roles or fall back to the watchdog. The recipient design "
                    "needs revisiting before the kill switch is flipped."
                ))
            elif exec_pct < 50:
                self.stdout.write(self.style.WARNING(
                    f"VERDICT: only {readiness['with_sales_executive']}/{total} "
                    f"events ({exec_pct:.0f}%) have a sales_executive, so the "
                    f"agreed To: recipient is missing for the majority. Either "
                    f"populate Event.sales_executive or revisit the recipient "
                    f"design before flipping the kill switch."
                ))
            else:
                self.stdout.write(self.style.SUCCESS(
                    f"VERDICT: {readiness['with_sales_executive']}/{total} events "
                    f"({exec_pct:.0f}%) have a sales_executive."
                ))

        if counts.get(FALLBACK) or counts.get(EVENT_NOT_FOUND):
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                "FALLBACK / EVENT_NOT_FOUND rows would email "
                "PAPER_REVIEW_ALERT_EMAIL instead of production if "
                "PAPER_REVIEW_NOTIFICATIONS_ENABLED were True. Fix the "
                "underlying event/assignment data, or accept the fallback, "
                "before turning the flag on for these codes."
            ))
