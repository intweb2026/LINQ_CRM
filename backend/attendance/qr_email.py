"""
attendance/qr_email.py
────────────────────────
Emailing the QR badge to everybody on the confirmed roster, one Gmail send per
person, from the caller's own connected Gmail account.

eligible_recipients() IS THE ONE PLACE "who gets an email" IS DECIDED. Both the
preview endpoint and the actual send call it, so the count shown before sending
is exactly the set that gets sent to — see qr_email_preview / send_qr_emails in
views.py.
"""
import base64
import json
import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator, validate_email
from django.utils.html import escape

from events.models import Event
from gmail_integration import service as gmail_service
from gmail_integration.service import GmailAuthExpired, GmailNotConnected
from pre_event_docs import qr_badges
from pre_event_docs import services as pre_event_services

from . import qr, roster
from .models import AttendanceQrEmailLog


# The Content-ID the body's <img> points at; see gmail_integration.service.
QR_CID = "checkin-qr"

logger = logging.getLogger(__name__)


def _valid_email(address):
    address = (address or "").strip()
    if not address:
        return False
    try:
        validate_email(address)
    except ValidationError:
        return False
    return True


def _already_sent_ids(event_code, edition):
    return set(
        AttendanceQrEmailLog.objects
        .filter(event_code=event_code, edition=edition, status=AttendanceQrEmailLog.Status.SENT)
        .values_list("delegate_id", flat=True)
    )


def eligible_recipients(event_code, edition):
    """
    (eligible_delegates, skipped_counts) for one event, where skipped_counts is
    {"no_email": n, "invalid_email": n, "already_sent": n}.
    """
    delegates = roster.confirmed(event_code, edition)
    sent_ids = _already_sent_ids(event_code, edition)

    eligible = []
    skipped = {"no_email": 0, "invalid_email": 0, "already_sent": 0}
    for delegate in delegates:
        if delegate.id in sent_ids:
            skipped["already_sent"] += 1
            continue
        email = (delegate.email or "").strip()
        if not email:
            skipped["no_email"] += 1
            continue
        if not _valid_email(email):
            skipped["invalid_email"] += 1
            continue
        eligible.append(delegate)
    return eligible, skipped


def _clean_field(value):
    """
    Free text off an event row, or "" if it is really a placeholder.

    Imported rows carry a single dash or a stray character in venue, city and
    country. Two characters or fewer is not a venue name, and letting one
    through pre-fills the form with junk the SCA then has to notice and clear.
    """
    value = (value or "").strip()
    return value if len(value) > 2 else ""


def _long_date(value):
    """Thursday 1 July 2027. No leading zero; %-d is not portable to Windows."""
    return f"{value.strftime('%A')} {value.day} {value.strftime('%B %Y')}"


def format_event_dates(start, end):
    """
    Both days, month spelled out, so 07/01 cannot be read as either order.

    Four shapes, because a range that repeats the month or the year reads as
    though it named two separate events:
        one day          1 July 2027
        same month       1-3 July 2027
        same year        30 June - 2 July 2027
        across a year    31 December 2027 - 2 January 2028
    """
    if not start:
        return ""
    if not end or end == start:
        return f"{start.day} {start.strftime('%B %Y')}"
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day}\u2013{end.day} {start.strftime('%B %Y')}"
    if start.year == end.year:
        return (f"{start.day} {start.strftime('%B')} \u2013 "
                f"{end.day} {end.strftime('%B %Y')}")
    return (f"{start.day} {start.strftime('%B %Y')} \u2013 "
            f"{end.day} {end.strftime('%B %Y')}")


def compose_email(delegate, values):
    """
    (subject, html_body, text_body) for one delegate.

    `values` is the batch: the ten template variables as the SCA approved them,
    resolved ONCE per send by event_values and identical for every recipient.
    Only the greeting and the QR differ from one email to the next, which is
    exactly the split the requirement asks for.

    NO CONDITIONAL CLAUSES LEFT. Every variable is mandatory and the send is
    refused while one is blank, so the sentences are plain merges. The older
    version rewrote itself around missing venues and unknown desk hours; that
    branching is dead now that nothing reaches here unfilled, and a merge
    nobody has to trace is worth more than one that degrades invisibly.
    """
    first_name = (delegate.first_name or "").strip() or "there"
    event_name = values["event_name"]
    city = values["city"]

    subject = f"Important: Your check-in QR code for {event_name}"
    welcome = (f"We look forward to welcoming you to {event_name} at "
               f"{values['venue']}, {city} on {values['event_dates']}.")
    instructions = (
        "To keep the process smooth on the day, please have it ready when you "
        "collect your badge at the registration desk, either on your phone or "
        "printed. Our team will scan the code and issue your badge in a few "
        "seconds."
    )
    registration = (
        f"The registration desk is open from {values['registration_opens']} to "
        f"{values['registration_closes']} on {values['day_one']}, and the "
        f"conference opens at {values['start_time']}. Please allow a few "
        "minutes to collect your badge before the first session begins."
    )
    amend = ("If anything needs amending on your badge before you arrive, reply "
             "to this email and the registration team will take care of it.")
    closing = f"We look forward to seeing you in {city}."
    # The event line of the sign-off block: the name in caps, then place and
    # dates on one line, as the approved sign-off sets it out.
    event_line = f"{city} | {values['event_dates']}"

    text_body = "\n\n".join([
        f"Dear {first_name},",
        welcome,
        # The text part cannot show an inline image, so it points at the PDF
        # where the HTML points at the QR above it.
        f"Your personal check-in QR code is attached as a PDF. {instructions}",
        registration,
        amend,
        closing,
        "Best,",
        f"{values['sender_name']}\n{values['sender_title']}",
        f"{event_name.upper()}\n{event_line}",
        # Spelled out rather than linked: a text part has no anchors, and
        # "Event Website" on its own line would name a link nobody can follow.
        f"Event Website: {values['event_url']}\nLinkedIn: {values['linkedin_url']}",
    ])

    # Escaped because all of these are free text, off an event row or typed
    # into the review form: an ampersand in a venue name breaks the markup, and
    # a stray angle bracket in a delegate name would land in the body as a tag.
    e = escape
    html_body = (
        f"<p>Dear {e(first_name)},</p>"
        f"<p>We look forward to welcoming you to <b>{e(event_name)}</b> at "
        f"{e(values['venue'])}, {e(city)} on {e(values['event_dates'])}.</p>"
        f"<p>Your personal check-in QR code is below. {e(instructions)}</p>"
        f'<p><img src="cid:{QR_CID}" alt="Your check-in QR code"'
        ' width="240" height="240"'
        ' style="display:block;border:1px solid #E1E5EA;border-radius:8px"></p>'
        "<p>The same code is attached as a PDF in case the image above does not "
        "display.</p>"
        f"<p>{e(registration)}</p>"
        f"<p>{e(amend)}</p>"
        f"<p>{e(closing)}</p>"
        # The sign-off block. Inline styles rather than classes, because an
        # email has no stylesheet and Gmail strips <style> blocks.
        "<p style=\"margin-bottom:4px\">Best,</p>"
        f"<p style=\"margin:0 0 14px\"><b>{e(values['sender_name'])}</b><br>"
        f"{e(values['sender_title'])}</p>"
        f"<p style=\"margin:0 0 14px\"><b>{e(event_name.upper())}</b><br>"
        f"{e(event_line)}</p>"
        f"<p style=\"margin:0\">"
        f'<a href="{e(values["event_url"])}">Event Website</a>'
        " | "
        f'<a href="{e(values["linkedin_url"])}">LinkedIn</a></p>'
    )
    return subject, html_body, text_body


# Google errors that will fail identically for EVERY recipient, so the run stops
# on the first one instead of grinding through the roster. Mapped to a sentence
# somebody can act on, because str(HttpError) is a 700-character dump carrying
# the request URL and the same JSON body twice; two recipients filled a modal
# with it, and a real event roster is hundreds.
FATAL_REASONS = {
    "accessNotConfigured": (
        "The Gmail API is not enabled on the Google Cloud project, so Google "
        "refused every send. An administrator must enable it, then this can be "
        "retried."
    ),
    "insufficientPermissions": (
        "The Gmail connection does not carry permission to send mail. "
        "Disconnect Gmail and connect it again."
    ),
    "ACCESS_TOKEN_SCOPE_INSUFFICIENT": (
        "The Gmail connection does not carry permission to send mail. "
        "Disconnect Gmail and connect it again."
    ),
    "authError": (
        "Gmail rejected the stored credentials. Disconnect Gmail and connect "
        "it again."
    ),
    "rateLimitExceeded": (
        "Gmail's sending limit for this account has been reached. The "
        "recipients not yet emailed can be sent to later; nobody is emailed "
        "twice."
    ),
    "dailyLimitExceeded": (
        "Gmail's daily sending limit for this account has been reached. The "
        "rest can be sent tomorrow; nobody is emailed twice."
    ),
}


def _google_reason(exc):
    """The machine-readable `reason` out of a googleapiclient error, or ""."""
    for detail in (getattr(exc, "error_details", None) or []):
        if isinstance(detail, dict) and detail.get("reason"):
            return detail["reason"]
    # error_details is empty on some responses, so fall back to the raw body.
    content = getattr(exc, "content", None)
    if content:
        try:
            body = json.loads(content.decode() if isinstance(content, bytes) else content)
            errors = body.get("error", {}).get("errors") or []
            if errors and errors[0].get("reason"):
                return errors[0]["reason"]
            status = body.get("error", {}).get("status")
            if status:
                return status
        except (ValueError, AttributeError):
            pass
    return ""


def classify_error(exc):
    """
    (message, is_fatal) for one failed send.

    is_fatal means "no other recipient will fare any better", which is the
    difference between one bad address and a project-wide misconfiguration.
    """
    reason = _google_reason(exc)
    if reason in FATAL_REASONS:
        return FATAL_REASONS[reason], True

    # Anything else is treated as this recipient's problem, and kept short: the
    # first sentence of Google's message, or the exception if it has none.
    text = ""
    for detail in (getattr(exc, "error_details", None) or []):
        if isinstance(detail, dict) and detail.get("message"):
            text = detail["message"]
            break
    text = text or str(exc)
    text = " ".join(text.split())
    if len(text) > 200:
        text = text[:197] + "..."
    return text, False


def _badge_for(delegate, event_code, attendee_type, event_name=""):
    return {
        "event_code": delegate.event_code,
        "edition": delegate.edition,
        # Printed under the QR in place of the code; see qr_badges.badge_pdf.
        "event_name": event_name,
        "first_name": delegate.first_name,
        "last_name": delegate.last_name,
        "name": roster.collapse(delegate.first_name + " " + delegate.last_name),
        "company": roster.collapse(delegate.company.name if delegate.company_id else (delegate.company_name_raw or "")),
        "attendee_type": attendee_type,
        "token": qr.mint(delegate.id, delegate.event_code, attendee_type),
    }


# Every event variable the template merges, in the order the form asks for
# them. The SCA sees all of them before every send, pre-filled from the event.
#
# EVENT NAME AND THE DATES ARE NOT HERE. `name` is populated on all 768 events
# and event_date is NOT NULL, so neither can be blank, and both are identity
# rather than logistics: retyping them per send would let two sends of the same
# event disagree about what the event is called.
# EVERY variable the template merges, in the order the review screen shows
# them, with the group each belongs to.
#
# [FIRST NAME] IS NOT HERE, and that is the one deliberate omission. It is the
# only merge field that differs per recipient, so it cannot be a batch value; it
# comes off each delegate record, exactly as the merge table says. The review
# screen names it as such rather than pretending it is editable.
EMAIL_FIELDS = (
    ("event_name", "Event name", "Event",
     "Full official event name, not the event code"),
    ("venue", "Venue name", "Event",
     "Hotel or venue exactly as printed on the agenda"),
    ("city", "City", "Event",
     "Used twice, in the welcome line and the close"),
    ("event_dates", "Event dates", "Event",
     "Both days, month spelled out so there is no ambiguity across regions"),
    ("day_one", "Day one date", "Schedule", "Opening day only"),
    ("registration_opens", "Registration opens", "Schedule",
     "Desk opening time, local to the venue"),
    ("registration_closes", "Registration closes", "Schedule",
     "End of the desk window on day one"),
    ("start_time", "Event start time", "Schedule",
     "Opening session start, taken from the agenda"),
    ("sender_name", "Sender name", "Sign-off",
     "A named sender gets replies answered, a generic one does not"),
    ("sender_title", "Sender title", "Sign-off",
     "Shown under the name, in the sign-off block"),
    ("event_url", "Event website URL", "Sign-off",
     "Linked as \u201cEvent Website\u201d. Typed in: the event record has no URL"),
    ("linkedin_url", "LinkedIn URL", "Sign-off",
     "Linked as \u201cLinkedIn\u201d. Typed in: the CRM holds no LinkedIn page"),
)
FIELD_KEYS = frozenset(key for key, _, _, _ in EMAIL_FIELDS)

# The two the SCA must type. Event.website looks like a pre-fill and is not:
# it holds event NAMES on 548 of 768 rows ("Battery Passport Europe"), so
# reading it would put a sentence where an href belongs.
URL_FIELDS = ("event_url", "linkedin_url")


def url_problem(value):
    """
    Why this URL cannot be linked, or "" if it can.

    A TRUST BOUNDARY, not decoration. These two values are typed by hand and
    land in an href in mail sent to hundreds of external recipients, so the
    scheme is checked rather than assumed: "javascript:..." is a valid-looking
    string that URLValidator alone would not stop being dangerous, and a bare
    "www.example.com" silently resolves relative to the mail client.
    """
    value = (value or "").strip()
    if not value:
        return "Required"
    if not value.lower().startswith(("http://", "https://")):
        return "Must start with http:// or https://"
    try:
        URLValidator(schemes=["http", "https"])(value)
    except ValidationError:
        return "Not a valid web address"
    return ""


def _clean_overrides(values):
    """Only the known keys, trimmed, blanks dropped so they fall back."""
    return {k: (v or "").strip() for k, v in (values or {}).items()
            if k in FIELD_KEYS and (v or "").strip()}


def event_values(event_code, edition, user=None, overrides=None):
    """
    Every batch variable for THIS send, after the SCA's edits.

    RESOLVED ONCE PER SEND, not once per recipient. event_meta reads the whole
    Event table to answer, so calling it inside the per-delegate loop meant one
    full scan for every attendee; a 200-person roster paid for 200 of them.

    THE EDITS ARE NOT WRITTEN BACK. They arrive with the request that previews
    and again with the request that sends, and they live exactly as long as
    those. The event row is read for the pre-fill and never updated, so
    correcting a venue for one mailing cannot quietly rewrite the catalogue the
    badges, reports and every other event view read from.
    """
    meta = pre_event_services.event_meta(event_code, edition)
    place = _clean_field(meta.get("raw_city"))
    venue = _clean_field(meta.get("venue"))
    # Event.save() fans `location` into city, country and venue alike, so a
    # venue equal to the place is not one anybody printed on an agenda.
    if venue and place and venue.lower() == place.lower():
        venue = ""
    start = meta.get("event_date")

    values = {
        "event_name": _event_display_name(meta, event_code),
        "venue": venue,
        "city": place,
        "event_dates": format_event_dates(start, meta.get("end_date")),
        "day_one": _long_date(start) if start else "",
        "registration_opens": _clean_field(meta.get("registration_opens")),
        "registration_closes": _clean_field(meta.get("registration_closes")),
        "start_time": _clean_field(meta.get("start_time")),
        "sender_name": _sender_name(user),
        "sender_title": (getattr(settings, "QR_EMAIL_SENDER_TITLE", "") or "").strip(),
        # No source to pre-fill from; see URL_FIELDS.
        "event_url": "",
        "linkedin_url": "",
    }
    values.update(_clean_overrides(overrides))
    return values


def _sender_name(user):
    if user is None:
        return ""
    return (user.get_full_name() or "").strip() or (user.username or "").strip()


def _event_display_name(meta, event_code):
    """
    The full official event name, never the event code, per the merge table.

    official_name first, then `name`, which Event.save() derives from
    official_event_name. The code is the last resort and is exactly what the
    template exists to avoid, so it only surfaces for an event the catalogue
    cannot resolve, where the SCA is asked to correct it before sending.
    """
    for key in ("official_name", "event_name"):
        value = (meta.get(key) or "").strip()
        if value:
            return value
    return event_code


def email_form(event_code, edition, user=None):
    """[{key, label, group, help, value}] to pre-fill the review screen."""
    values = event_values(event_code, edition, user)
    return [{"key": key, "label": label, "group": group, "help": help_text,
             "value": values[key]}
            for key, label, group, help_text in EMAIL_FIELDS]


def missing_fields(event_code, edition, user=None, overrides=None):
    """
    [{key, label, reason}] for every variable that is blank or unusable.

    Blank and invalid share one list because the screen treats them the same
    way, marking the field and refusing to go on; `reason` is what it prints
    beside it, so "Required" and "Must start with http://" reach the SCA in the
    same place rather than one as a field marker and the other as a surprise
    after they press Continue.
    """
    values = event_values(event_code, edition, user, overrides)
    problems = []
    for key, label, _, _ in EMAIL_FIELDS:
        reason = (url_problem(values[key]) if key in URL_FIELDS
                  else ("" if values[key] else "Required"))
        if reason:
            problems.append({"key": key, "label": label, "reason": reason})
    return problems


def preview_for(delegate, event_code, values):
    """
    {to, name, subject, html} for ONE real delegate: the actual email.

    NOT A MOCKUP. It is compose_email, the same function send_qr_emails calls,
    from the same `values` the send will use, so there is no second template to
    drift out of step. The only difference is the QR image, which travels as a
    cid: attachment in a real send and cannot resolve in a browser, so it is
    swapped for a data: URI of that delegate's OWN badge token. What the SCA
    approves is what the recipient gets, down to the code scanned at the desk.
    """
    subject, html, _ = compose_email(delegate, values)
    token = _badge_for(delegate, event_code, roster.attendee_type(delegate))["token"]
    png = base64.b64encode(qr_badges.qr_png(token)).decode()
    html = html.replace(f"cid:{QR_CID}", f"data:image/png;base64,{png}")
    return {
        "to": delegate.email,
        "name": roster.collapse(f"{delegate.first_name} {delegate.last_name}"),
        "subject": subject,
        "html": html,
    }


def send_qr_emails(event_code, edition, user, overrides=None):
    """
    {sent, failed, skipped, skipped_reasons, failures, gmail_not_connected}
    """
    eligible, skipped_reasons = eligible_recipients(event_code, edition)
    skipped_total = sum(skipped_reasons.values())

    result = {
        "sent": 0, "failed": 0, "skipped": skipped_total,
        "skipped_reasons": skipped_reasons, "failures": [],
        "gmail_not_connected": False, "fatal_error": "",
    }

    # Once for the whole run: the approved values are the same for everybody,
    # and resolving them per delegate meant a full Event table scan each time.
    values = event_values(event_code, edition, user, overrides)
    event_name = values["event_name"]

    for delegate in eligible:
        attendee_type = roster.attendee_type(delegate)
        badge = _badge_for(delegate, event_code, attendee_type, event_name)
        pdf_bytes = qr_badges.badge_pdf(badge)
        png_bytes = qr_badges.qr_png(badge["token"])
        subject, html_body, text_body = compose_email(delegate, values)
        filename = roster.collapse(
            f"{delegate.first_name}_{delegate.last_name}").replace(" ", "_") + ".pdf"

        try:
            gmail_service.send_email(
                user, delegate.email, subject, html_body, text_body,
                attachments=[{
                    "filename": filename or "badge.pdf",
                    "content": pdf_bytes,
                    "mimetype": "application/pdf",
                }],
                inline_images=[{
                    "cid": QR_CID,
                    "content": png_bytes,
                    "filename": "qr.png",
                }],
            )
        except (GmailNotConnected, GmailAuthExpired):
            result["gmail_not_connected"] = True
            break
        except Exception as exc:
            message, fatal = classify_error(exc)
            AttendanceQrEmailLog.objects.create(
                delegate=delegate, event_code=event_code, edition=edition,
                recipient_email=delegate.email,
                status=AttendanceQrEmailLog.Status.FAILED,
                # The full text, not the shortened one: the log is where the
                # detail belongs, and nobody reads it in a dialog.
                error_message=str(exc)[:2000], sent_by=user,
            )
            result["failed"] += 1
            result["failures"].append({
                "name": roster.collapse(delegate.first_name + " " + delegate.last_name),
                "email": delegate.email,
                "error": message,
            })
            if fatal:
                # Nothing after this would succeed either. Stopping here keeps a
                # project-wide misconfiguration from being reported as one
                # failure per delegate, which on a full roster is hundreds of
                # identical paragraphs. Whoever has not been emailed is simply
                # still eligible, so a retry after the fix picks them up.
                logger.error("QR emails aborted for %s %s: %s",
                             event_code, edition, str(exc)[:500])
                result["fatal_error"] = message
                break
            continue

        AttendanceQrEmailLog.objects.create(
            delegate=delegate, event_code=event_code, edition=edition,
            recipient_email=delegate.email,
            status=AttendanceQrEmailLog.Status.SENT, sent_by=user,
        )
        result["sent"] += 1

    return result
