"""
paper_review/notifications.py
──────────────────────────────
PART B — the Django port of the Zoho workflow `Email_to_Production_Team` (v2,
record event = on add): tell the production team a paper review has landed.

RECIPIENTS — WHY THE MAPPING CHANGED
Zoho walked `Event_Code.Speaker_Sales_Team.Email` and
`Event_Code.Market_Research_Senior.Email`. Speaker_Sales_Team no longer has a
counterpart at all — the Speaker Sales team was merged into SCA, so the column
is now Event.sales_team — and Market_Research_Senior is
Event.market_research_senior, but both are CharField(255) FREE TEXT with no relation to User — there is no `.Email` to
traverse. Name-matching them against User is exactly the failure this codebase
has already been burned by (see events/models.py:112, where sales_team is matched
by icontains against first/last name): a typo or a shared surname routes a
speaker's paper review to the wrong person, or to nobody, and nothing tells you.

So the traversal is replaced by the two relations that genuinely carry addresses:

    to  → Event.sales_executive           (FK to User)
    cc  → Event.assigned_users            (the User→Event M2M)
            filtered to role speaker_sales or market_research

The Event schema is untouched.

THE ZOHO PRECEDENCE BUG, NOT REPLICATED
The v2 script reads:

    if(sales_email == "" || sales_email == null && input.Event_Code != null)

which parses as `(== "") || (== null && Event_Code != null)`. An EMPTY STRING
therefore enters the block without Event_Code ever having been checked, and the
body then traverses `Event_Code.Speaker_Sales_Team` on a possibly-null lookup.
Zoho text fields return "" rather than null, so that path is live — the
surrounding try/catch has been swallowing it. Here the empty-string and null
cases are guarded explicitly and a missing/unresolvable event is its own outcome
(`no_event_code` / `event_not_found`) rather than a null dereference.

FAILURE IS NEVER THE CREATE'S PROBLEM
send_paper_review_notification is handed to transaction.on_commit by the viewset,
so it runs only after the PaperReview is durably committed, and it catches
everything: dead SMTP, a bad address, a template error. The review stays saved and
the request stays 201. Zoho's try/catch "SCRIPT ERROR" alert is replicated, and if
that alert cannot be sent either, it is logged and dropped.
"""
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone
from django.utils.html import escape

from events.models import Event

from .access import may_see_mr_fields
from .models import RUBRIC_TOTAL, NotificationLog, PaperReview

logger = logging.getLogger(__name__)

# CC roles, as User.Role values. Named here rather than inline so widening the
# notification is a one-line change with a test attached.
CC_ROLES = ("speaker_sales", "market_research")

# Resolution steps, in the order they are attempted. The one that failed is
# recorded on the NotificationLog and repeated in the fallback alert, so
# "production never heard" comes with a reason.
STEP_NO_EVENT_CODE      = "no_event_code"
STEP_EVENT_NOT_FOUND    = "event_not_found"
STEP_NO_SALES_EXECUTIVE = "no_sales_executive"
STEP_SALES_EXEC_NO_MAIL = "sales_executive_has_no_email"
STEP_NO_RECIPIENTS      = "no_recipients"

# B8. The 17 fields the Deluge lists, IN THE DELUGE'S ACTUAL ORDER (confirmed —
# the field SET was reconstructed arithmetically in the previous pass: the form
# carries 23 business fields, the Deluge body lists 17, and the six omitted are
# the six INDIVIDUAL rubric criteria, replaced by the computed Proposal score and
# Grade; 23 − 6 = 17 exactly). Labels are kept as the form-derived CRM labels
# (from PaperReviewFormModal.jsx) rather than the shorthand names in the Deluge
# ordering list, so the email and the form agree on wording.
EMAIL_FIELDS = (
    ("paper_submission_date",      "Paper submission date"),
    ("event_code",                 "Event code"),
    ("speaker_name",               "Speaker name"),
    ("company_name",               "Company name"),
    ("email",                      "Email address of the speaker"),
    ("linkedin_speaker",           "LinkedIn profile of speaker"),
    ("linkedin_followers",         "LinkedIn followers count"),
    ("linkedin_company",           "LinkedIn company profile"),
    ("nos",                        "NOS?"),
    ("proposal_received",          "Proposal received"),
    ("agenda_addition",            "Agenda addition"),
    ("theme",                      "Theme"),
    ("proposal_score",             "Proposal score"),
    ("grade",                      "Grade"),
    ("session_location_on_agenda", "Session or location on agenda"),
    ("internal_footnotes",         "Internal footnotes"),
    ("feedback_to_speaker",        "Feedback to speaker or request information"),
)

# Restricted to MR/Admin everywhere else in this app — see serializers.py
# _MR_ONLY_FIELDS. Included in the body only under the all-or-nothing rule below.
MR_FIELDS = ("internal_footnotes",)


class Recipients:
    """
    The outcome of resolution. Plain class rather than a dataclass to match the
    style of webhooks/event_resolver.py's Resolution.

    to / cc            — addresses the send is attempted with
    users              — the User objects behind them, for the MR rule
    is_fallback        — nothing resolved; the watchdog gets it instead
    failure_step       — which step ran out of information
    note              — human-readable degradation, stored on the log
    """

    def __init__(self, to=None, cc=None, users=None, is_fallback=False,
                 failure_step="", note=""):
        self.to = to or []
        self.cc = cc or []
        self.users = users or []
        self.is_fallback = is_fallback
        self.failure_step = failure_step
        self.note = note

    @property
    def include_internal_footnotes(self):
        """
        B8. internal_footnotes goes out ONLY when every resolved recipient may
        read it. All-or-nothing on purpose: a mixed To/Cc list is one email with
        one body, so "include it for the MR reader" is the same thing as "leak it
        to the speaker-sales reader", which reopens the hole the serializer's MR
        stripping closed.

        The fallback watchdog is a bare address with no User behind it and so can
        never satisfy this, which is the intended answer — the fallback email is
        the one going somewhere nobody vetted.
        """
        if self.is_fallback or not self.users:
            return False
        return all(may_see_mr_fields(u) for u in self.users)


def _clean(value):
    """Whitespace-trimmed string. Turns Zoho's "" and Django's None into one case."""
    return (value or "").strip()


def resolve_recipients(review):
    """
    Resolve To and Cc from the review's event. Never raises; an unresolvable step
    comes back as a fallback with the step named.
    """
    code = _clean(review.event_code)

    # Guarded explicitly, both cases, before anything is dereferenced — this is
    # the Zoho precedence bug's blast radius.
    if not code:
        return Recipients(
            to=[settings.PAPER_REVIEW_ALERT_EMAIL], is_fallback=True,
            failure_step=STEP_NO_EVENT_CODE,
            note="The review carries no event code, so no event could be looked up.",
        )

    # Exact match, matching the RBAC scope rule in access.py: the serializer
    # stores the catalogue's canonical spelling, so anything that does not match
    # exactly genuinely is not this catalogue's event.
    event = Event.objects.filter(event_code=code).first()
    if event is None:
        return Recipients(
            to=[settings.PAPER_REVIEW_ALERT_EMAIL], is_fallback=True,
            failure_step=STEP_EVENT_NOT_FOUND,
            note=f"No event in the catalogue matches '{code}'.",
        )

    sales_exec = event.sales_executive
    to, to_users, step = [], [], ""
    if sales_exec is None:
        step = STEP_NO_SALES_EXECUTIVE
    else:
        address = _clean(sales_exec.email)
        if not address:
            step = STEP_SALES_EXEC_NO_MAIL
        else:
            to = [address]
            to_users = [sales_exec]

    cc, cc_users, seen = [], [], {a.lower() for a in to}
    for user in event.assigned_users.filter(role__in=CC_ROLES).order_by("id"):
        address = _clean(user.email)
        if not address or address.lower() in seen:
            continue
        seen.add(address.lower())
        cc.append(address)
        cc_users.append(user)

    if to:
        return Recipients(to=to, cc=cc, users=to_users + cc_users)

    # No sales executive address. The Cc list still contains real people, so the
    # email goes to them rather than to the watchdog — but the missing assignment
    # is recorded, not swallowed. DECISION: the fallback fires only when NOTHING
    # resolves, which is what "if no recipient resolves" means; a degraded send is
    # still a delivered send.
    if cc:
        return Recipients(
            to=cc, cc=[], users=cc_users, failure_step=step,
            note=(f"Degraded: {step}. The event's assigned speaker-sales / "
                  f"market-research users were used as the To list instead."),
        )

    return Recipients(
        to=[settings.PAPER_REVIEW_ALERT_EMAIL], is_fallback=True,
        failure_step=step or STEP_NO_RECIPIENTS,
        note=(f"{step or STEP_NO_RECIPIENTS}: event '{code}' has no sales "
              f"executive address and no assigned speaker-sales or "
              f"market-research user with an email address."),
    )


def resolved_refs(recipients):
    """
    B5. speaker_email_ref / research_email_ref are OUTPUTS.

    In Zoho they are cached lookups filled by a separate on-user-input workflow
    which — per the v2 script's own comment — does not fire for API submissions;
    that gap is precisely why v2 added a server-side fallback. Resolving
    server-side removes the need for the cache, so these are written with what was
    ACTUALLY resolved at send time rather than read as inputs.

    ASSUMPTION: both columns are EmailField, i.e. single-valued, and the Cc list
    can hold several people per role. The first resolved address per role is
    stored; the complete list lives on the NotificationLog.
    """
    speaker, research = "", ""
    for user in recipients.users:
        role = getattr(user, "role", "")
        address = _clean(user.email)
        if not address:
            continue
        if role == "speaker_sales" and not speaker:
            speaker = address
        elif role == "market_research" and not research:
            research = address
    return speaker, research


def subject_for(review):
    """Per Zoho, with its own "Unknown Event" fallback for a blank code."""
    code = _clean(review.event_code) or "Unknown Event"
    return f"New Paper Review: {code} - {review.speaker_name}"


def _display(review, field):
    value = getattr(review, field, None)
    if field == "nos":
        return "Yes" if value else "No"
    if field == "proposal_score":
        return "—" if value is None else f"{value} / {RUBRIC_TOTAL}"
    if value is None or value == "":
        return "—"
    return str(value)


def render_body(review, include_internal_footnotes):
    """
    (text, html). The HTML table is the real body; the text part exists because a
    send with no plain-text alternative is what makes an email look like spam.

    A field the recipients may not read is OMITTED, not blanked — the same choice
    the serializer makes, so the absence itself carries no information about
    whether a value exists.
    """
    rows_text, rows_html = [], []
    for field, label in EMAIL_FIELDS:
        if field in MR_FIELDS and not include_internal_footnotes:
            continue
        value = _display(review, field)
        rows_text.append(f"{label}: {value}")
        rows_html.append(
            "<tr>"
            f'<th align="left" style="padding:6px 12px 6px 0;vertical-align:top;'
            f'white-space:nowrap;color:#374151;font-weight:600;">{escape(label)}</th>'
            f'<td style="padding:6px 0;vertical-align:top;color:#111827;">'
            f"{escape(value).replace(chr(10), '<br>')}</td>"
            "</tr>"
        )

    text = (
        "A new paper review has been submitted.\n\n"
        + "\n".join(rows_text)
        + "\n\n— Linq CRM"
    )
    html = (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:13px;">'
        "<p>A new paper review has been submitted.</p>"
        '<table cellpadding="0" cellspacing="0" '
        'style="border-collapse:collapse;font-size:13px;">'
        + "".join(rows_html)
        + "</table><p>— Linq CRM</p></div>"
    )
    return text, html


def _send(subject, text, html, to, cc=None):
    message = EmailMultiAlternatives(
        subject=subject, body=text,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=list(to), cc=list(cc or []),
    )
    message.attach_alternative(html, "text/html")
    # fail_silently is deliberately NOT set: the caller needs the exception so it
    # can record status=failed and raise the SCRIPT ERROR alert.
    message.send()


def _log(review, subject, recipients, status, error, included_footnotes):
    NotificationLog.objects.create(
        paper_review=review, subject=subject[:255],
        to_addresses=list(recipients.to), cc_addresses=list(recipients.cc),
        status=status, error=error or "",
        included_internal_footnotes=included_footnotes,
        sent_at=timezone.now(),
    )


def _alert(kind, subject, detail, intended):
    """
    The two watchdog emails, both to PAPER_REVIEW_ALERT_EMAIL:
    "RECIPIENT FALLBACK" (nobody resolved) and "SCRIPT ERROR" (the send failed).
    """
    body = (
        f"{kind}\n\n"
        f"Original subject: {subject}\n"
        f"Intended recipients: {', '.join(intended) or '(none resolved)'}\n\n"
        f"{detail}\n\n— Linq CRM"
    )
    html = (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:13px;">'
        f"<p><strong>{escape(kind)}</strong></p>"
        f"<p>Original subject: {escape(subject)}<br>"
        f"Intended recipients: {escape(', '.join(intended) or '(none resolved)')}</p>"
        f"<pre style=\"white-space:pre-wrap;font-family:inherit;\">{escape(detail)}</pre>"
        "<p>— Linq CRM</p></div>"
    )
    _send(f"[Linq CRM] {kind} — {subject}", body, html,
          [settings.PAPER_REVIEW_ALERT_EMAIL])


def _notify(review):
    recipients = resolve_recipients(review)
    subject = subject_for(review)
    include_footnotes = recipients.include_internal_footnotes

    # B1 — the kill switch. Resolution and body-rendering ALWAYS run, so
    # resolve_recipients() is verifiable against real Event data with zero mail
    # leaving the building (see management command
    # report_paper_review_recipients for exactly that check, without even this
    # much). Read here — a live attribute access on the lazy settings object at
    # send time — rather than as a module-level constant, so flipping the env var
    # takes effect without a restart.
    #
    # Unconditional: no send is attempted at all, not even the fallback/
    # SCRIPT-ERROR watchdog alerts — "send nothing" per B1 means nothing, not
    # "nothing except the alerts". speaker_email_ref / research_email_ref are
    # deliberately NOT populated here: those are stamped "at send time" (B5), and
    # with sending suppressed there is no send time to stamp them at.
    if not settings.PAPER_REVIEW_NOTIFICATIONS_ENABLED:
        render_body(review, include_footnotes)   # proves the body would build
        _log(review, subject, recipients, NotificationLog.Status.SUPPRESSED,
             recipients.note, include_footnotes)
        return

    # Rendering is inside the try with the send: B6 names a template error
    # alongside dead SMTP, and both have to end as a logged `failed` rather than
    # as an exception escaping into a request whose record is already committed.
    try:
        text, html = render_body(review, include_footnotes)
        _send(subject, text, html, recipients.to, recipients.cc)
    except Exception as exc:                                  # noqa: BLE001
        _log(review, subject, recipients, NotificationLog.Status.FAILED,
             f"{type(exc).__name__}: {exc}", False)
        try:
            _alert("SCRIPT ERROR", subject,
                   f"The notification could not be sent.\n"
                   f"Reason: {type(exc).__name__}: {exc}",
                   recipients.to + recipients.cc)
        except Exception:                                     # noqa: BLE001
            # The alert about the failure also failed. Logged and dropped —
            # there is nowhere left to escalate to, and raising here would take
            # down a request whose record is already safely committed.
            logger.exception(
                "paper_review: SCRIPT ERROR alert could not be sent for "
                "review #%s", review.pk,
            )
        return

    if recipients.is_fallback:
        # Replicates Zoho: the body still goes out (to the watchdog), AND a
        # separate alert names the step that ran out of information.
        try:
            _alert("RECIPIENT FALLBACK", subject,
                   f"No recipient could be resolved from the event, so the "
                   f"notification below was sent to "
                   f"{settings.PAPER_REVIEW_ALERT_EMAIL} instead.\n"
                   f"Failed step: {recipients.failure_step}\n"
                   f"{recipients.note}\n\n"
                   f"--- original body ---\n{text}",
                   recipients.to)
        except Exception as exc:                              # noqa: BLE001
            _log(review, subject, recipients, NotificationLog.Status.FALLBACK,
                 f"{recipients.note} | alert send failed: "
                 f"{type(exc).__name__}: {exc}", include_footnotes)
            return
        _log(review, subject, recipients, NotificationLog.Status.FALLBACK,
             recipients.note, include_footnotes)
        return

    speaker_ref, research_ref = resolved_refs(recipients)
    # queryset.update(), not save(): auto_now would stamp updated_at and make a
    # notification look like a user edit of the row.
    PaperReview.objects.filter(pk=review.pk).update(
        speaker_email_ref=speaker_ref, research_email_ref=research_ref,
    )
    _log(review, subject, recipients, NotificationLog.Status.RESOLVED,
         recipients.note, include_footnotes)


def send_paper_review_notification(review):
    """
    The transaction.on_commit callback. Absolute backstop: an exception escaping
    here would surface from the viewset's atomic block AFTER the review has been
    committed, turning a saved record into a 500.
    """
    try:
        _notify(review)
    except Exception:                                         # noqa: BLE001
        logger.exception(
            "paper_review: notification failed for review #%s", review.pk,
        )
