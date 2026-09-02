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

So the traversal is replaced by the one relation that genuinely carries an
address, the review's own author, and a standing copy list:

    from → settings.DEFAULT_FROM_EMAIL     (the SMTP account; James Trevino in
                                            production, set in the env, no code)
    to   → Event.sales_executive           (FK to User)
    cc   → PaperReview.created_by          — the MRE who filled the form
         + settings.PAPER_REVIEW_CC_EMAILS (Harry Jonas)

The Event schema is untouched.

WHY THE Cc IS NOT A PER-EVENT ROLE WALK ANY MORE
It used to walk Event.assigned_users filtered to speaker_sales / market_research,
so who was copied depended on how that event happened to be staffed — and an
event with nobody assigned copied nobody, silently. The agreed rule is instead
"the submitter, and Harry, every time", which splits cleanly in two: the submitter
is already stamped on the row as created_by (the form link's reviewer for a public
submission, the logged-in author for an in-CRM one — see
views.create_review_with_workflows), and Harry is a settings constant.

The knock-on is deliberate: internal_footnotes can no longer go out (see
Recipients.include_internal_footnotes), because a standing address has no User row
and nothing can vouch for what its reader may see.

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
import re
from html import unescape

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import escape

from events.models import Event

from .access import may_see_mr_fields
from .models import RUBRIC_TOTAL, NotificationLog, PaperReview

logger = logging.getLogger(__name__)

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
# _MR_ONLY_FIELDS. Included in the PLAIN-TEXT part only, under the all-or-nothing
# rule below. The HTML template has no footnotes field at all, so the rule is what
# stands between the field and the wire on the text side.
MR_FIELDS = ("internal_footnotes",)

# The letters that count as clearing the bar, deciding which of the template's two
# opening paragraphs renders (cleared / not_cleared). Absolute bands, so this is a
# letter set rather than a score floor — see models.GRADE_BANDS.
#
# ponytail: A/B+/B assumed, i.e. the 26/45 floor. NOT a confirmed business rule —
# no rule for it exists anywhere in the CRM or the Deluge. Correct the tuple if
# the bar sits elsewhere; nothing else has to change.
CLEARED_GRADES = ("A", "B+", "B")

# review field → template token, for the six rubric criteria. The template hard-
# codes each maximum next to its score, and those maxima are models.CRITERIA's,
# so a criterion whose maximum moves has to move in both places; the test in
# tests_notification.py checks the pair still agrees.
SCORE_TOKENS = (
    ("closeness_to_topic",           "score_topic"),
    ("closeness_to_region",          "score_region"),
    ("clear_solution_to_challenges", "score_solution"),
    ("case_study_results_examples",  "score_case_study"),
    ("not_obvious_sales_pitch",      "score_not_pitch"),
    ("company_profile_score",        "score_company"),
)


class Recipients:
    """
    The outcome of resolution. Plain class rather than a dataclass to match the
    style of webhooks/event_resolver.py's Resolution.

    to / cc            — addresses the send is attempted with
    users              — the User objects behind them, for the MR rule
    unvetted           — resolved addresses with NO User behind them
    is_fallback        — nothing resolved; the watchdog gets it instead
    failure_step       — which step ran out of information
    note              — human-readable degradation, stored on the log
    """

    def __init__(self, to=None, cc=None, users=None, unvetted=None,
                 is_fallback=False, failure_step="", note=""):
        self.to = to or []
        self.cc = cc or []
        self.users = users or []
        self.unvetted = unvetted or []
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
        # The standing Cc (settings.PAPER_REVIEW_CC_EMAILS) is a bare address with
        # no User row behind it, so nothing here can vouch for what its reader may
        # see. One such address closes the field, exactly as the fallback watchdog
        # does and for the same reason. Kept separate from `cc` rather than
        # testing `cc` outright: the MRE is also a Cc, and they DO have a User row.
        if self.unvetted:
            return False
        return all(may_see_mr_fields(u) for u in self.users)


def _clean(value):
    """Whitespace-trimmed string. Turns Zoho's "" and Django's None into one case."""
    return (value or "").strip()


# Tags that END A LINE rather than disappearing. Converted to newlines BEFORE the
# rest are stripped, so a pasted bulleted list survives as separate lines instead
# of collapsing into one paragraph. </li> and </tr> are here for the same reason
# even though the fields that carry them are rarer.
_LINE_BREAKING_TAGS = re.compile(r"(?is)<\s*(?:br\s*/?|/\s*(?:p|div|li|tr|h[1-6]))\s*>")

# Everything else that looks like a tag. Deliberately NOT django.utils.html
# .strip_tags: that runs an HTMLParser whose handle_entityref re-emits a bare
# ampersand as an entity, so a real stored value of "Q&A SESSION" came back out
# as "Q&A; SESSION" with a semicolon nobody typed. A plain removal leaves
# ampersands exactly as they were found, which is what the reader wants; the
# template escapes the result afterwards, so nothing is trusted either way.
_ANY_TAG = re.compile(r"(?s)<[^>]*>")


def _plain(value):
    """
    Free text with any markup reduced to plain text and its line structure kept.

    WHY THIS EXISTS. agenda_addition and feedback_to_speaker are pasted from Word
    and from the Zoho rich-text editor, so real rows hold things like
    `<p style="margin: 0cm"><b><span style="font-family: Arial">TITLE</span></b>`.
    The template autoescapes, which is correct and must not change, so that markup
    was reaching the reader as visible tags.

    STRIPPED, NOT TRUSTED. The alternative was marking the field safe and letting
    the stored HTML render, which would put arbitrary user-entered markup into an
    email; Word's styling would also fight the template's own. Stripping keeps the
    escaping intact and the email consistent, and the words are what the reader
    needs.

    Cheap when there is nothing to do — a value with no "<" is returned untouched,
    which is almost every field on almost every row.
    """
    text = str(value)
    if "<" not in text and "&" not in text:
        return text

    text = _LINE_BREAKING_TAGS.sub("\n", text)
    text = _ANY_TAG.sub("", text)
    # After the tags, the entities: &nbsp; and &amp; are what Word leaves behind.
    # The template escapes again on the way out, so this does not un-escape
    # anything into the HTML.
    # Real entities only. A bare "&" that is not part of one is left alone, so
    # "Q&A" stays "Q&A".
    text = unescape(text).replace(" ", " ")

    # Word emits runs of empty paragraphs. Collapse them, so the email does not
    # open with a hole where the pasted content used to have spacing.
    lines, out = [l.strip() for l in text.splitlines()], []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


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

    # The Cc: the submitter, then the standing list. Deduped against the To and
    # against each other, case-insensitively, so a sales executive who also filed
    # the review — or who is on the standing list — is addressed exactly once.
    cc, cc_users, unvetted, seen = [], [], [], {a.lower() for a in to}

    # The MRE who filled the form. created_by is null on rows the importer and the
    # webhook create, which neither notify nor have a submitter to copy; getattr
    # keeps the unsaved PaperReview that report_paper_review_recipients drives
    # this with working too.
    author = getattr(review, "created_by", None)
    author_address = _clean(getattr(author, "email", ""))
    if author_address and author_address.lower() not in seen:
        seen.add(author_address.lower())
        cc.append(author_address)
        cc_users.append(author)

    for address in settings.PAPER_REVIEW_CC_EMAILS:
        address = _clean(address)
        if not address or address.lower() in seen:
            continue
        seen.add(address.lower())
        cc.append(address)
        unvetted.append(address)

    if to:
        return Recipients(to=to, cc=cc, users=to_users + cc_users,
                          unvetted=unvetted)

    # No sales executive address. The Cc is still real people, so the email goes
    # to them rather than to the watchdog — but the missing assignment is
    # recorded, not swallowed. DECISION: the fallback fires only when NOTHING
    # resolves, which is what "if no recipient resolves" means; a degraded send is
    # still a delivered send.
    if cc:
        return Recipients(
            to=cc, cc=[], users=cc_users, unvetted=unvetted, failure_step=step,
            note=(f"Degraded: {step}. The Cc list (the submitter and the standing "
                  f"recipients) was used as the To list instead."),
        )

    return Recipients(
        to=[settings.PAPER_REVIEW_ALERT_EMAIL], is_fallback=True,
        failure_step=step or STEP_NO_RECIPIENTS,
        note=(f"{step or STEP_NO_RECIPIENTS}: event '{code}' has no sales "
              f"executive address, the review names no submitter with an email "
              f"address, and PAPER_REVIEW_CC_EMAILS is empty."),
    )


def resolved_refs(recipients):
    """
    B5. speaker_email_ref / research_email_ref are OUTPUTS.

    In Zoho they are cached lookups filled by a separate on-user-input workflow
    which — per the v2 script's own comment — does not fire for API submissions;
    that gap is precisely why v2 added a server-side fallback. Resolving
    server-side removes the need for the cache, so these are written with what was
    ACTUALLY resolved at send time rather than read as inputs.

    ASSUMPTION: both columns are EmailField, i.e. single-valued. The first
    resolved address per role is stored; the complete list lives on the
    NotificationLog.

    NARROWED with the Cc rewrite: recipients.users is now the sales executive and
    the submitting MRE (the standing Cc addresses have no User rows), so these
    fill only when one of those two holds the role in question, and are otherwise
    blank. The NotificationLog remains the complete record of who was written to.
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
    # Markup-stripped here rather than at each call site, so the plain-text part
    # of the email and every cell of the HTML table get it from one place.
    return _plain(value) or "—"


def event_mre_name(review):
    """
    The MR executive named on the EVENT, for the email's "reviewed by" line.

    NOT the submitter. Those are the same person most of the time and diverge
    exactly when it matters — a stand-in files the review while the event still
    belongs to someone else — and the sales executive reading this wants the name
    of whoever owns the research on their event. The submitter is Cc'd either way,
    so nobody is dropped from the thread.

    SAME SOURCE AS THE ACCESS RULE. paper_review/access.py decides which events an
    MRE may see at all from Event.market_research_senior / _junior, the columns the
    event modal's Team ownership block writes; see
    accounts.user_resolution.event_codes_naming. Reading a different column here
    would let the email credit somebody who cannot open the record it links to.

    Senior first, then Junior, matching the order access.py grants on. These are
    CharField(255) free text, and they are used here as a DISPLAY STRING ONLY,
    never resolved back to a User — matching a name against User to find an
    address is the misrouting this codebase has already been burned by (see the
    module docstring and events/models.py). Displaying a name routes nothing.

    Falls back to the submitter, then to the system name, because an importer or
    webhook row has neither a named reviewer nor an author, and a blank name
    mid-sentence reads as a bug.
    """
    code = _clean(review.event_code)
    event = Event.objects.filter(event_code=code).first() if code else None

    if event is not None:
        named = (_clean(event.market_research_senior)
                 or _clean(event.market_research_junior))
        if named:
            return named

    author = getattr(review, "created_by", None)
    if author is not None:
        return author.get_full_name() or author.username
    return "Linq CRM"


def template_context(review):
    """
    The token set templates/paper_review/handoff_email.html expects.

    NOT ESCAPED HERE. The template autoescapes, and escaping twice turns a speaker
    called O'Brien into O&amp;#x27;Brien in the delivered mail.

    An absent value becomes an em dash rather than the empty string, so a blank
    table cell always means "we hold nothing" and never "the token is misspelt".
    The exceptions are the conditional tokens (nos, cleared, li_company,
    agenda_addition, feedback), which stay falsy so their blocks strip cleanly.
    """
    context = {
        "event_code":    _display(review, "event_code"),
        "paper_date":    (review.paper_submission_date.strftime("%d %b %Y")
                          if review.paper_submission_date else "\u2014"),
        "speaker_name":  _display(review, "speaker_name"),
        "company_name":  _display(review, "company_name"),
        "speaker_email": _display(review, "email"),
        "li_followers":  _display(review, "linkedin_followers"),
        "li_profile":    _clean(review.linkedin_speaker),
        "li_company":    _clean(review.linkedin_company),
        "session":       _display(review, "session_location_on_agenda"),
        "theme":         _display(review, "theme"),
        "grade":         _clean(review.grade) or "\u2014",
        "score_total":   ("\u2014" if review.proposal_score is None
                          else review.proposal_score),
        "nos":           bool(review.nos),
        "cleared":       _clean(review.grade) in CLEARED_GRADES,
        # linebreaksbr runs in the template, so the newlines _plain leaves behind
        # have to survive to it. These two are the fields that actually carry
        # pasted Word markup.
        "agenda_addition": _plain(_clean(review.agenda_addition)),
        "feedback":        _plain(_clean(review.feedback_to_speaker)),
        "record_url":    f"{settings.CRM_BASE_URL}/paper-review",
        # Named in the HEADER line, "reviewed by ...". The event's assigned MRE,
        # not the submitter — see event_mre_name. NOT the signature either; the
        # message is from the CRM rather than from a person, so the sign-off is
        # constant in the template.
        "mre_name":      event_mre_name(review),
    }
    for field, token in SCORE_TOKENS:
        value = getattr(review, field, None)
        context[token] = "\u2014" if value is None else value
    return context


def render_body(review, include_internal_footnotes):
    """
    (text, html). The HTML is the supplied handoff template; the text part exists
    because a send with no plain-text alternative is what makes an email look like
    spam, and it stays the flat field list \u2014 a plain-text transcription of an
    editorial layout is a worse fallback than the fields themselves.

    THE MR RULE APPLIES TO THE TEXT PART ONLY, because internal_footnotes is the
    only field it governs and the HTML template never had a place for it. A field
    the recipients may not read is OMITTED, not blanked \u2014 the same choice the
    serializer makes, so the absence itself carries no information about whether a
    value exists.
    """
    rows_text = [
        f"{label}: {_display(review, field)}"
        for field, label in EMAIL_FIELDS
        if not (field in MR_FIELDS and not include_internal_footnotes)
    ]
    text = (
        "A new paper review has been submitted.\n\n"
        + "\n".join(rows_text)
        + "\n\n\u2014 Linq CRM"
    )
    html = render_to_string("paper_review/handoff_email.html",
                            template_context(review))
    return text, html


def _send(subject, text, html, to, cc=None):
    # The testing redirect, applied at the ONE point every message in this module
    # passes through — the notification and both watchdog alerts alike. Doing it
    # here rather than in resolve_recipients is what keeps NotificationLog and
    # report_paper_review_recipients honest about who the mail was FOR.
    #
    # The real To: rides in the subject, so a redirected inbox can still answer
    # "who would have got this?" without opening the log.
    redirect = _clean(getattr(settings, "PAPER_REVIEW_REDIRECT_ALL_EMAIL", ""))
    if redirect:
        subject = f"[TEST, for {', '.join(to) or '(nobody)'}] {subject}"
        to, cc = [redirect], []

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
