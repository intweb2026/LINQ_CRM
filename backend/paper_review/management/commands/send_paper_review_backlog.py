"""
paper_review/management/commands/send_paper_review_backlog.py
──────────────────────────────────────────────────────────────
Send the production-team notification for paper reviews that were created before
the notification existed, or while it was switched off.

    python manage.py send_paper_review_backlog                  # dry run, default
    python manage.py send_paper_review_backlog --send           # actually send
    python manage.py send_paper_review_backlog --ids 12,15,19
    python manage.py send_paper_review_backlog --since 2026-01-01 --limit 10

DRY RUN IS THE DEFAULT, and --send is the only way past it. This command mails
real sales executives about real speakers; the cost of running it by accident is
dozens of confusing emails to people who cannot un-see them, and there is no
recall. The dry run resolves every recipient and prints exactly who would be
written to, so the list can be checked BEFORE anything leaves.

IDEMPOTENT BY CONSTRUCTION. A review that already has a NotificationLog row
recording a real send is skipped, so a second run mails nobody twice — including
a run that died halfway through the first time. `resolved` and `fallback` both
count as sent, because both put a message on the wire. `suppressed` and `failed`
do not: nothing was delivered in either case, which is precisely the backlog this
command exists to clear.

IT REUSES THE LIVE PATH. Each review goes through
notifications.send_paper_review_notification, the same callable the create
endpoint hands to transaction.on_commit. Not a copy of it — a backfill that
resolved recipients slightly differently from the real thing would be worse than
no backfill, and this way the NotificationLog rows it writes are indistinguishable
from the ones a form submission writes.

IT REFUSES TO RUN WITH THE KILL SWITCH OFF. With
PAPER_REVIEW_NOTIFICATIONS_ENABLED false, every review would be walked, logged as
`suppressed`, and mail nobody, while the command reported success — and those
suppressed rows would then look like history to the next run. Better to stop and
say so.
"""
import time
from collections import Counter
from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from paper_review.models import NotificationLog, PaperReview
from paper_review.notifications import (
    resolve_recipients, send_paper_review_notification,
)

# NotificationLog statuses that mean a message actually reached the mail server.
# Anything else is backlog.
SENT_STATUSES = (NotificationLog.Status.RESOLVED, NotificationLog.Status.FALLBACK)


class Command(BaseCommand):
    help = ("Send the paper review notification for reviews that never got one. "
            "Dry run unless --send is given.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--send", action="store_true",
            help="Actually send. Without it this only reports what would go.")
        parser.add_argument(
            "--ids",
            help="Comma-separated review ids, instead of the whole backlog.")
        parser.add_argument(
            "--event", help="Restrict to one event code, exactly as stored.")
        parser.add_argument(
            "--since",
            help="Only reviews created on or after this date, YYYY-MM-DD.")
        parser.add_argument(
            "--limit", type=int,
            help="Stop after this many. Useful for sending the first one or two "
                 "and checking the result before releasing the rest.")
        parser.add_argument(
            "--include-sent", action="store_true",
            help="Include reviews that already have a delivered notification. "
                 "Off by default, and the reason a repeat run is harmless.")
        parser.add_argument(
            "--show", type=int, default=20,
            help="How many rows to list (default: %(default)s). The COUNTS "
                 "always cover everything; this caps only the listing, because "
                 "a backlog of thousands printed one per line is unreadable. "
                 "Pass 0 to list them all.")
        parser.add_argument(
            "--delay", type=float, default=1.0,
            help="Seconds between sends (default: %(default)s). Paced rather "
                 "than bursted, so a backlog does not look like a spam run to "
                 "the provider.")

    def build_queryset(self, options):
        qs = PaperReview.objects.all().order_by("id")

        if options["ids"]:
            try:
                ids = [int(part) for part in options["ids"].split(",") if part.strip()]
            except ValueError:
                raise CommandError("--ids takes numbers separated by commas.")
            qs = qs.filter(id__in=ids)

        if options["event"]:
            qs = qs.filter(event_code=options["event"])

        if options["since"]:
            try:
                since = datetime.strptime(options["since"], "%Y-%m-%d").date()
            except ValueError:
                raise CommandError("--since takes a date as YYYY-MM-DD.")
            qs = qs.filter(created_at__date__gte=since)

        if not options["include_sent"]:
            # Exclude anything already delivered. Expressed as a subquery on the
            # log rather than a flag on the review, because the log is the record
            # of what actually happened and a flag would be a second one to keep
            # in step.
            already = (NotificationLog.objects
                       .filter(status__in=SENT_STATUSES)
                       .values("paper_review_id"))
            qs = qs.exclude(id__in=already)

        return qs

    def handle(self, *args, **options):
        sending = options["send"]

        if sending and not settings.PAPER_REVIEW_NOTIFICATIONS_ENABLED:
            raise CommandError(
                "PAPER_REVIEW_NOTIFICATIONS_ENABLED is False, so every review "
                "would be logged as suppressed and mail nobody, while this "
                "command reported success. Set it True and run again.")

        redirect = settings.PAPER_REVIEW_REDIRECT_ALL_EMAIL
        if redirect:
            self.stdout.write(self.style.WARNING(
                f"PAPER_REVIEW_REDIRECT_ALL_EMAIL is set to {redirect}. Every "
                f"message below will go THERE, not to the people named. Empty "
                f"that setting to reach the real recipients."))

        queryset = self.build_queryset(options)
        total = queryset.count()
        if options["limit"]:
            queryset = queryset[:options["limit"]]

        reviews = list(queryset)
        if not reviews:
            self.stdout.write("Nothing to send; the backlog is empty.")
            return

        shown = len(reviews)
        self.stdout.write(
            f"{shown} review(s) to send"
            + (f", of {total} in the backlog" if shown != total else "")
            + (".\n" if sending else ", DRY RUN.\n"))

        # Resolution runs for every row before ANY of them is sent, so a bad
        # recipient list is visible while it is still cheap to stop.
        # The listing is capped but the COUNTING is not: at four figures the
        # per-row lines are noise, and the summary underneath is the thing worth
        # reading before releasing anything.
        outcomes = Counter()
        failures = 0
        cap = options["show"]
        for index, review in enumerate(reviews):
            got = resolve_recipients(review)
            outcome = got.failure_step or "resolved"
            outcomes[outcome] += 1
            if got.is_fallback:
                failures += 1
            if cap and index >= cap:
                continue
            tag = {"resolved": self.style.SUCCESS("OK      ")}.get(
                outcome, self.style.WARNING(f"{outcome[:8].upper():8}"))
            self.stdout.write(
                f"{tag} #{review.id:<6} {review.event_code:<16} "
                f"{review.speaker_name[:24]:<24} "
                f"to=[{', '.join(got.to)}] cc=[{', '.join(got.cc)}]")

        if cap and len(reviews) > cap:
            self.stdout.write(
                f"... and {len(reviews) - cap} more, not listed. "
                f"Use --show 0 for all of them.")

        self.stdout.write("")
        self.stdout.write("Resolution:")
        for outcome, count in outcomes.most_common():
            self.stdout.write(f"  {outcome:<28} {count}")

        if failures:
            self.stdout.write(self.style.WARNING(
                f"\n{failures} of these resolve to nobody and would reach the "
                f"watchdog at {settings.PAPER_REVIEW_ALERT_EMAIL} instead. Fix "
                f"the event's sales executive, or accept it."))

        if not sending:
            self.stdout.write(self.style.SUCCESS(
                "\nDry run, nothing was sent. Re-run with --send to release "
                "these. Consider --limit 1 first."))
            return

        self.stdout.write("")
        self.stdout.write("Sending.")
        results = Counter()
        for index, review in enumerate(reviews):
            # Never raises; it catches everything and records the outcome on the
            # NotificationLog, so one dead recipient cannot halt the backlog.
            send_paper_review_notification(review)

            # Read the outcome back off the log rather than reporting what was
            # INTENDED. The send happens inside that callable and can fail there;
            # printing the resolved recipients as though they were delivered
            # would tell you a message went to a sales executive who never got
            # one, which is exactly the thing this output exists to rule out.
            log = (NotificationLog.objects.filter(paper_review=review)
                   .order_by("-id").first())
            status = log.status if log else "no log written"
            results[status] += 1

            style = (self.style.SUCCESS
                     if status == NotificationLog.Status.RESOLVED
                     else self.style.WARNING)
            self.stdout.write(style(
                f"  #{review.id:<6} {status:<11} "
                f"to=[{', '.join(log.to_addresses) if log else ''}] "
                f"cc=[{', '.join(log.cc_addresses) if log else ''}]"))
            self.stdout.write(f"          {review.event_code} — "
                              f"{review.speaker_name}")
            if log and log.error:
                self.stdout.write(self.style.WARNING(f"          {log.error}"))

            if options["delay"] and index < len(reviews) - 1:
                time.sleep(options["delay"])

        self.stdout.write("")
        self.stdout.write("Delivered:")
        for status, count in results.most_common():
            self.stdout.write(f"  {status:<12} {count}")
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"{results.get(NotificationLog.Status.RESOLVED, 0)} of "
            f"{len(reviews)} reached the mail server. `resolved` means sent, "
            f"`failed` carries the reason, `fallback` means nobody resolved from "
            f"the event and the watchdog got it instead."))
