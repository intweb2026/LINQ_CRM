"""
paper_review/management/commands/preview_paper_review_email.py
───────────────────────────────────────────────────────────────
Render the paper review handoff email, and optionally send one copy somewhere.

    python manage.py preview_paper_review_email                     # write HTML
    python manage.py preview_paper_review_email --review 41         # a real row
    python manage.py preview_paper_review_email --to ops@iq-hub.com # send it

WHY A COMMAND AND NOT A TEST
The suite proves the template's LOGIC — which block renders, what is escaped.
Neither it nor a locmem outbox can tell you whether the thing looks right in
Outlook, and an editorial layout is exactly the kind of email where a rendering
bug is invisible to an assertion. So: --out writes a file to open in a browser,
--to puts one real message in a real client.

WRITES NOTHING
With no --review the sample is an UNSAVED PaperReview, built in memory and never
persisted — the same trick report_paper_review_recipients uses. proposal_score
and grade are derived in save(), which is not being called, so they are computed
here explicitly; that is the only reason this file knows about them.

--to IGNORES PAPER_REVIEW_NOTIFICATIONS_ENABLED, deliberately. That kill switch
exists to stop a paper review CREATE mailing production by surprise. Typing this
command with an explicit address is not a surprise, and a preview that silently
sent nothing while the switch was off would be read as a broken template.
"""
from datetime import date

from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from paper_review.models import PaperReview
from paper_review.notifications import render_body, subject_for

# Every field the template touches, filled — including both optional blocks and
# the NOS badge, so one render exercises the whole layout. Scores total 38 of 45,
# which is an A and therefore the "cleared" paragraph; --band swaps that.
SAMPLE = {
    "event_code":                  "AFS - JS",
    "paper_submission_date":       date(2026, 8, 10),
    "speaker_name":                "Eli Jasso",
    "company_name":                "Cicada Logistics",
    "email":                       "eli.jasso@example.com",
    "linkedin_speaker":            "https://www.linkedin.com/in/eli-jasso/",
    "linkedin_company":            "https://www.linkedin.com/company/cicada/",
    "linkedin_followers":          417,
    "nos":                         True,
    "closeness_to_topic":          9,
    "closeness_to_region":         4,
    "clear_solution_to_challenges": 9,
    "case_study_results_examples": 4,
    "not_obvious_sales_pitch":     4,
    "company_profile_score":       8,
    "session_location_on_agenda":  "Day 1, Afternoon Session",
    "theme":                       "Terminal and rail decarbonisation",
    "proposal_received":           "Terminal and rail decarbonisation",
    "agenda_addition":             "CHALLENGES IN OILFIELD CULTURE\n"
                                   "Retrofitting shore power at a working berth",
    "feedback_to_speaker":         "Please add a case study with figures.\n"
                                   "Confirm the co-presenter before 1 October.",
}

# Criteria in models.CRITERIA order, one combination per grade band, so --band
# shows the "not cleared" half of the template without hand-editing SAMPLE.
BANDS = {
    "A":  (10, 5, 10, 5, 5, 10),
    "B+": (10, 5, 10, 5, 0,  5),
    "B":  (10, 5, 10, 0, 0,  2),
    "C":  (10, 5,  5, 0, 0,  2),
    "D":  (10, 5,  0, 0, 0,  0),
    "E":  ( 5, 0,  0, 0, 0,  0),
}
CRITERIA_ORDER = (
    "closeness_to_topic", "closeness_to_region", "clear_solution_to_challenges",
    "case_study_results_examples", "not_obvious_sales_pitch",
    "company_profile_score",
)


class Command(BaseCommand):
    help = ("Render the paper review handoff email to a file, and optionally "
            "send one copy to an address for a real-client check.")

    def add_arguments(self, parser):
        parser.add_argument("--review", type=int,
                            help="Render a stored PaperReview by id instead of "
                                 "the built-in sample.")
        parser.add_argument("--band", choices=sorted(BANDS),
                            help="Score the sample into this grade band. Only "
                                 "meaningful without --review.")
        parser.add_argument("--to",
                            help="Send one copy here. Uses the configured "
                                 "EMAIL_BACKEND, so it needs real SMTP "
                                 "credentials in the environment.")
        parser.add_argument("--out", default="paper_review_email_preview.html",
                            help="Where to write the rendered HTML "
                                 "(default: %(default)s). Pass an empty string "
                                 "to skip writing.")
        parser.add_argument("--diagnose", action="store_true",
                            help="Walk the SMTP conversation one command at a "
                                 "time and print the server's literal reply to "
                                 "each, including the queue id. Sends a one-line "
                                 "probe, not the template. Use with --to.")
        parser.add_argument("--footnotes", action="store_true",
                            help="Include internal_footnotes in the PLAIN-TEXT "
                                 "part. The HTML has no such field.")

    def build_review(self, options):
        if options["review"]:
            review = PaperReview.objects.filter(pk=options["review"]).first()
            if review is None:
                raise CommandError(f"No paper review #{options['review']}.")
            return review

        review = PaperReview(**SAMPLE)
        if options["band"]:
            for field, value in zip(CRITERIA_ORDER, BANDS[options["band"]]):
                setattr(review, field, value)
        # Derived in save(), which is never called here.
        review.proposal_score = review.computed_score()
        review.grade = review.computed_grade() or ""
        return review

    def diagnose(self, to):
        """
        The SMTP dialogue, verbatim.

        EmailMessage.send() reduces the whole exchange to "it worked" or an
        exception, which is useless when a relay ACCEPTS a message and then fails
        to deliver it — the case this exists for. Every reply is printed, and the
        250 that answers DATA carries the queue id the provider's own log is
        searchable by, which is the only thing that turns "no email arrived" into
        a question their support can answer.

        Sends a one-line probe rather than the template on purpose; this is
        testing the transport, and a plain body removes the content of the
        message as a variable.
        """
        import smtplib
        from email.mime.text import MIMEText

        sender = settings.DEFAULT_FROM_EMAIL
        probe = MIMEText("SMTP transport probe from Linq CRM.", "plain")
        probe["Subject"] = "Linq CRM SMTP probe"
        probe["From"] = sender
        probe["To"] = to

        self.stdout.write(f"host     {settings.EMAIL_HOST}:{settings.EMAIL_PORT} "
                          f"tls={settings.EMAIL_USE_TLS} "
                          f"ssl={getattr(settings, 'EMAIL_USE_SSL', False)}")
        login_as = settings.EMAIL_HOST_USER or (
            "(none, so the relay must be allow-listing this IP)")
        self.stdout.write(f"login as {login_as}")
        self.stdout.write(f"sends as {sender}")
        self.stdout.write("")

        opener = (smtplib.SMTP_SSL if getattr(settings, "EMAIL_USE_SSL", False)
                  else smtplib.SMTP)
        server = opener(settings.EMAIL_HOST, settings.EMAIL_PORT,
                        timeout=getattr(settings, "EMAIL_TIMEOUT", 20) or 20)
        try:
            server.ehlo()
            if settings.EMAIL_USE_TLS:
                server.starttls()
                server.ehlo()
            # Skipped when no username is configured, which is exactly the
            # IP-allow-listed relay case — authenticating there is an error.
            if settings.EMAIL_HOST_USER:
                self.stdout.write(f"AUTH   {server.login(settings.EMAIL_HOST_USER, settings.EMAIL_HOST_PASSWORD)}")
            else:
                self.stdout.write("AUTH   skipped, no EMAIL_HOST_USER set")
            self.stdout.write(f"MAIL   {server.mail(sender)}")
            self.stdout.write(f"RCPT   {server.rcpt(to)}")
            self.stdout.write(f"DATA   {server.data(probe.as_string().encode())}")
        finally:
            try:
                server.quit()
            except Exception:                                 # noqa: BLE001
                pass

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            "Accepted. If nothing arrives, the message is sitting at the "
            "provider; search the queue id above in their delivery log."))

    def handle(self, *args, **options):
        if options["diagnose"]:
            if not options["to"]:
                raise CommandError("--diagnose needs --to.")
            return self.diagnose(options["to"])

        review = self.build_review(options)
        subject = subject_for(review)
        text, html = render_body(review, options["footnotes"])

        if options["out"]:
            with open(options["out"], "w", encoding="utf-8") as handle:
                handle.write(html)
            self.stdout.write(self.style.SUCCESS(
                f"Wrote {options['out']} — open it in a browser."))

        # Marked in the subject on every send. Everything this command puts on the
        # wire is a rehearsal, and an unmarked one sitting in a real inbox looks
        # exactly like the notification a submitted review produces. The real send
        # path marks itself the same way when PAPER_REVIEW_REDIRECT_ALL_EMAIL is
        # set, so a marked subject consistently means "nobody acted on this".
        #
        # Applied BEFORE the subject is reported, so the line printed here is the
        # line that arrives; reporting the unmarked one would be a small lie that
        # costs someone a confused search of their inbox.
        if options["to"]:
            subject = f"[TEST] {subject}"

        self.stdout.write(f"Subject: {subject}")
        self.stdout.write(f"From:    {settings.DEFAULT_FROM_EMAIL or '(unset)'}")

        if not options["to"]:
            self.stdout.write("No --to given, so nothing was sent.")
            return

        message = EmailMultiAlternatives(
            subject=subject, body=text,
            from_email=settings.DEFAULT_FROM_EMAIL, to=[options["to"]],
        )
        message.attach_alternative(html, "text/html")
        # fail_silently stays off: a failed preview must say why, loudly. The
        # usual why is an empty EMAIL_HOST_PASSWORD.
        message.send()
        self.stdout.write(self.style.SUCCESS(f"Sent to {options['to']}."))
