"""
attendance/roster.py
─────────────────────
WHO MAY CHECK IN, and it is not a new rule.

The confirmed roster is the PRE-EVENT DOCS CHECK-IN SHEET, exactly as
pre_event_docs.services.at_desk() decides it: payment status in Paid, Paid
(Transferred) or Pending, not one of our own staff, and not a booking code that
is a table, a press pass, an add-on or an upgrade line. That function is the
analogue of the "payment status = Paid" gate the Bookings version of this module
used, and it is CALLED rather than re-implemented — two spellings of who is
attending is how the workbook this all replaces went wrong in the first place.

SPEAKERS AND DELEGATES ARE ONE TABLE HERE. A speaker is a book_delegates row
whose booking_code carries a speaker marker. The marker list comes from
book_event.booking_code, so a new spelling of "speaker" is understood in one
place; role_of() in services is not reused for this because it answers a
different question — it prints the whole booking code for a sponsor, so
"Speaker / GLD SpEx" reads as the code and not as a speaker.

THE EVENT-CODE JOIN TRAP, worth reading before touching anything here.
BookDelegate.save() strips the trailing year out of event_code into `edition`,
so a delegate carries ("ACU", 2025) where the Events catalogue carries "ACU25".
Every lookup in this module is therefore (code, edition) against the DELEGATES
table, never a catalogue code against it. __iexact, never __icontains: codes
differ in case between the two sides, and "SFU - AD" is a substring of
"BSFU - AD".
"""
from book_delegate.models import BookDelegate
from book_event.booking_code import speaker_markers
from pre_event_docs import services
from webhooks.event_resolver import boundary_regex

from . import qr


def collapse(value):
    """A display name with its stray internal whitespace removed."""
    return " ".join((value or "").split())


def attendee_type(delegate):
    code = delegate.booking_code or ""
    if any(boundary_regex(marker).search(code) for marker in speaker_markers()):
        return "speaker"
    return "delegate"


def events():
    """
    The events this module can work, newest edition first, with a head count.

    services.pickable_events() verbatim, and `delegates` is deliberately its
    count and its key: the frontend event picker is the one Pre-Event Docs
    already uses, and re-keying the payload here would mean a second copy of
    that component.
    """
    return services.pickable_events()


# The delegate-side (code, edition) pair spelled the way the CATALOGUE spells it.
# Imported rather than copied: pre_event_docs already carries this mapping and
# the reasoning behind it, and two versions of it would drift.
event_keys = services._event_keys


def exists(event_code, edition=None):
    """Is this (code, edition) an event the delegates table actually holds?"""
    code = (event_code or "").strip()
    if not code:
        return False
    qs = BookDelegate.objects.filter(event_code__iexact=code)
    if edition:
        qs = qs.filter(edition=edition)
    return qs.exists()


def confirmed(event_code, edition=None):
    """Everybody on the check-in sheet for one event, as BookDelegate rows."""
    rows = services.event_queryset(event_code, edition)
    return [d for d in rows if services.at_desk(d)]


def confirmed_delegate(delegate_id):
    """
    One delegate, but only if they are on their own event's check-in sheet.

    The gate is applied to the row that was found rather than expressed as a
    filter, so it is the SAME function the roster listing uses and cannot answer
    differently for one person than for the list they appear in.
    """
    delegate = (
        BookDelegate.objects
        .select_related("invoice", "company")
        .filter(pk=delegate_id)
        .first()
    )
    if delegate is None or not services.at_desk(delegate):
        return None
    return delegate


def row(delegate, record=None):
    """
    One roster line, plus the badge token that checks this person in.

    The token is minted HERE rather than at a separate endpoint because the
    roster is already the answer to "who may I check in on this event", and the
    caller has already been checked against that event. A manual check-in is
    then the same POST to scan/ as a camera makes, and the server re-verifies
    everything either way.
    """
    return {
        "delegate_id": delegate.id,
        "attendee_type": attendee_type(delegate),
        "attendee_name": collapse(f"{delegate.first_name} {delegate.last_name}"),
        "attendee_email": delegate.email or "",
        "company_name": collapse(services.company_of(delegate)),
        "position": collapse(delegate.position),
        "booking_code": delegate.booking_code or "",
        "payment_status": services.payment_status_of(delegate),
        "event_code": delegate.event_code,
        "edition": delegate.edition,
        "token": qr.mint(delegate.id, delegate.event_code,
                         attendee_type(delegate)),
        "checked_in_at": record.checked_in_at if record else None,
    }


def matches(row_dict, term):
    """
    Does this roster line match a typed search term?

    TOKENISED, and the shape of it is the whole point. The name spans two
    columns in the database, so testing the WHOLE term against each column and
    ORing the results can never match a full name — "Sam Burleigh" is in neither
    first_name nor last_name. So: split on whitespace, AND across the tokens, OR
    across the columns.

    Not a concatenate-and-contains either, tempting as that is. The data holds
    stray internal whitespace (" Burleigh"), so the joined string carries a
    double space and "Sam Burleigh" fails against it; every value here is
    collapsed by row() before it is compared. ANDing the tokens is what makes
    two words NARROWER than one, which is what a person typing a second word
    means.
    """
    tokens = (term or "").split()
    if not tokens:
        return True
    columns = [
        (row_dict["attendee_name"] or "").casefold(),
        (row_dict["company_name"] or "").casefold(),
        (row_dict["attendee_email"] or "").casefold(),
    ]
    return all(
        any(token in column for column in columns)
        for token in (t.casefold() for t in tokens)
    )
