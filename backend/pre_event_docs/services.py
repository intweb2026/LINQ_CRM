"""
pre_event_docs/services.py
───────────────────────────
FOUR REPORTS, and the names are fixed by the spec rather than by this module:

    Name Badges              every booking on the event, full name and company
    Check-In Sheet           the desk sheet, filtered
    Additional Name Badges   what changed since the badges were logged
    Speed Networking         see networking.py

One queryset, the projections off it, and the change detection against the badge
log. This is the whole of what the PRE EVENT DOCS workbook did with formulas.

REGISTERED DELEGATES IS DELIBERATELY ABSENT. In the workbook it and
Report_NameBadges held the same 75 rows, differing only in sort order and in
whether a TBA row was dropped or blanked, so it was a second view of one list.

THE SHAPE, AND WHY IT IS THIS SHAPE
Every report tab in that workbook read the same rows and reshaped them, so the
page fetches those rows ONCE and every projection below takes the fetched list
rather than a queryset. Six querysets returning the same rows six times is the
cost the workbook could not see and this one should not pay.

TWO POPULATIONS, WHICH IS THE WORKBOOK'S OWN DESIGN AND NOT AN OVERSIGHT HERE
The badge tabs filter on the EVENT and nothing else. Only Report_CheckIn applies
the payment status whitelist and the exclusions. For DLG - VV that is 75 badge
rows against 45 at the desk, and the difference is deliberate: a badge is printed
for everybody booked, and who actually turns up is a question for the door.

event_queryset() is the superset and the only fetch. at_desk() derives the
check-in subset from the rows already in memory, so matching the workbook costs
no extra query.

THE JOIN GOTCHA, WORTH READING BEFORE CHANGING ANYTHING HERE
BookDelegate.save strips the trailing year out of event_code into `edition`, so
a delegate carries ("ACU", 2025) while the Events catalogue carries "ACU25".
Filtering delegates by an Events row's event_code returns NOTHING, silently.
The picker therefore reads the pairs the delegates table itself holds and only
consults Events for a display name.
"""
import re
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Max
from django.utils import timezone

from book_delegate.effective import effective_value
from book_delegate.models import BookDelegate
from book_event.booking_code import (
    spex_exact, spex_markers, speaker_markers,
)
from events.models import Event
from webhooks.event_resolver import boundary_regex

from .models import BadgeIssue

# Who counts as attending, for the CHECK-IN SHEET only. See at_desk.
ATTENDING_STATUSES = ("Paid", "Paid (Transferred)", "Pending")

# A cancelled booking. ON the Name Badges report, OFF the Additional one.
#
# THE TWO LISTS DIFFER BY EXACTLY THIS, and the asymmetry is deliberate rather
# than an oversight, so it is worth writing down:
#
#   Name Badges             includes cancelled. It is the whole roster, and the
#                           desk would rather see a cancelled card and bin it
#                           than not know it was printed.
#   Additional Name Badges  excludes cancelled. It lists badges to PRINT, and
#                           nobody prints a badge for somebody not coming.
#
# WHY IT CAME UP. A booking sat as TBA, so it was off the badge list entirely.
# Somebody gave it a real name while its payment status was Cancelled, and it
# arrived on Additional Name Badges as a New Badge to print for a person who had
# cancelled. Nothing in the workbook catches that; Report_NameBadges has no
# payment filter at all.
#
# A CANCELLED BOOKING IS ALSO NOT A CANCELLATION, which reads oddly until you
# see why. Cancellations exist to say "a badge on the table should not be
# there". Since a cancelled booking is deliberately kept ON the badge list,
# flagging it for removal at the same time would be the print-it-and-pull-it
# contradiction that the earlier version of this report had. So a cancelled
# booking is simply invisible to the changes report, in both halves.
CANCELLED_STATUSES = ("Cancelled",)

# The one status that owes money at the door, and the words the check in sheet
# prints against it.
COLLECT_ON_SITE_STATUS = "Pending"
COLLECT_ON_SITE_NOTE = "Payment to collect on-site"

# Booking codes that are not a person needing a badge.
#
# Every one is a canonical spelling from booking_code_canonical's closed list,
# and BookDelegate.save canonicalises the column on every write, so a WHOLE
# STRING iexact match is exact rather than approximate. Deliberately not
# icontains: "Media" as a substring would also exclude a delegate from
# Mediacorp, which is the over-match booking_code.py exists to prevent.
EXCLUDED_BOOKING_CODES = (
    "Speaker Table",          # a table, not a person
    "Media",                  # press, not a delegate
    "Add-Ons",                # a purchase, not a person
    "Upgraded to GLD SpEx",   # an adjustment line against another booking
    "Upgraded to PLT SpEx",
    "Upgraded to SLV SpEx",
)

# A BUG IN THE WORKBOOK, FIXED HERE RATHER THAN COPIED.
#
# Report_CheckIn tries to drop the upgrade lines with the clause
#     SMZoho!N:N <> "Upgraded*"
# and a Google Sheets FILTER does not expand wildcards in <>, so that compares
# the column against the literal seven characters plus a star and matches
# nothing. Every "Upgraded to ... SpEx" line has therefore been appearing on the
# real check-in sheet as a delegate needing a badge. The three canonical
# spellings are listed above instead, which is what the clause was reaching for.
WORKBOOK_WILDCARD_BUG = "Upgraded*"

# A name or company nobody has supplied yet.
#
# THE WORKBOOK SPELLS THIS RULE THREE DIFFERENT WAYS, which is worth knowing
# before changing it. Report_NameBadges tests a case-SENSITIVE substring,
# REGEXMATCH(E:E, "TBA"). Report_CheckIn tests a case-insensitive substring,
# "(?i)TBA". Report_Additional tests a three character prefix on the name AND
# the company, UPPER(LEFT(TRIM(x), 3)) = "TBA". So one delegate can be a
# placeholder on one tab and a real person on another.
#
# One rule is used for all of them here, and it is tighter than any of the
# three; see _PLACEHOLDER below for why. TBC rides along because it is the same
# kind of placeholder and the same fix covers it.
TBA_MARKERS = ("TBA", "TBC")


def _setting(name, default):
    return tuple(getattr(settings, name, None) or default)


def internal_email_domains():
    return _setting("PRE_EVENT_DOCS_INTERNAL_EMAIL_DOMAINS", ("iq-hub.com",))


def internal_company_names():
    """
    The company a booking carries when it is one of ours.

    Read out of the real filter, which is `SMZoho!H:H <> "iQ-Hub"`, an EXACT
    comparison against that one spelling. An earlier guess here used an email
    domain and a loose company match; the domain is kept below because it costs
    nothing and catches a booking typed under a different company spelling, but
    this exact list is the rule the workbook actually applies.
    """
    return _setting("PRE_EVENT_DOCS_INTERNAL_COMPANY_NAMES", ("iQ-Hub",))


# ── the one queryset, and the subset that filters ────────────────────────────
#
# There were Q-object versions of the internal and excluded-code rules here,
# built for a single pre-filtered queryset. at_desk() answers the same question
# in Python over rows that are already in memory, so the Q versions had no
# caller left and are gone rather than kept for symmetry.

def event_queryset(event_code, edition=None):
    """
    Everybody booked on one event. THE ONE FETCH, and the badge population.

    Event and edition only, which is exactly what the badge tabs filter on. No
    payment status, no exclusions, no attendance test, because the workbook
    applies none of those here and doing so printed 45 badges where it prints 75.

    TBA rows are included; the two badge projections treat them differently and
    both need to see them.
    """
    qs = (
        BookDelegate.objects
        .select_related("invoice", "company")
        # BookDelegate.Meta.ordering is ["invoice__invoice_number", "first_name"],
        # which makes the database sort the whole joined set on a varchar
        # invoice number for nothing: every projection below sorts in Python, by
        # name or by company, and none of them wants this order. Cleared rather
        # than replaced, because there is no one order that suits five tabs.
        .order_by()
        .filter(event_code__iexact=(event_code or "").strip())
    )
    if edition:
        qs = qs.filter(edition=edition)
    return qs


def is_cancelled(delegate):
    status = (payment_status_of(delegate) or "").strip().casefold()
    return any(status == c.casefold() for c in CANCELLED_STATUSES)


def on_badge_list(delegate):
    """
    Is this booking on the NAME BADGES report, and therefore freezable.

    Everything except a placeholder. Cancelled bookings are included; see
    CANCELLED_STATUSES for why.

    THIS IS ALSO WHAT CANCELLATIONS ARE MEASURED AGAINST, deliberately. It is
    the population a freeze captures, so it is the only population a diff
    against that freeze can mean anything about. When name_badges() and the diff
    disagreed about membership, the gap between them was reported as change and a
    real event showed 31 corrections where one booking had been edited.
    """
    return not is_tba(delegate)


def needs_a_badge_printed(delegate):
    """
    Is this booking on the ADDITIONAL NAME BADGES report.

    On the badge list AND not cancelled. The narrower of the two memberships,
    and the only place the cancelled rule applies.
    """
    return on_badge_list(delegate) and not is_cancelled(delegate)


def at_desk(delegate):
    """
    Is this person on the CHECK-IN sheet, as Report_CheckIn decides it.

    The four clauses of that FILTER, in the same order, applied in Python to a
    row already fetched rather than as a second query:

        payment status in Paid, Paid (Transferred), Pending
        company is not iQ-Hub
        booking code is not Speaker Table, Media, Add-Ons or an upgrade line

    NO ATTENDANCE TEST, deliberately. The workbook does not have one; a
    Cancelled payment status is what removes a cancellation, and the IN? column
    is the tick, not a filter. An earlier version excluded
    attendance=Cancelled here and that is not the workbook's rule.
    """
    if payment_status_of(delegate) not in ATTENDING_STATUSES:
        return False
    # Whole-cell comparison, not a substring: the workbook tests
    # `SMZoho!H:H <> "iQ-Hub"`, and a substring test would also drop a real
    # delegate from a company whose name happens to contain ours.
    company = _norm(company_of(delegate))
    if any(company == _norm(name) for name in internal_company_names()):
        return False
    email = (delegate.email or "").strip().lower()
    if any(email.endswith(f"@{d.lower()}") for d in internal_email_domains()):
        return False
    code = _norm(delegate.booking_code)
    return not any(code == _norm(c) for c in EXCLUDED_BOOKING_CODES)


def _event_keys(code, edition):
    """
    The catalogue codes an (event_code, edition) pair might be spelled as.

    "ACU" plus 2025 is "ACU25" in the catalogue, so that is tried first and the
    bare code second, for an event carrying no year. See the join note at the
    top of this module for why the two disagree in the first place.
    """
    code = (code or "").strip()
    keys = []
    if edition:
        keys.append(f"{code}{str(edition)[-2:]}".upper())
    keys.append(code.upper())
    return keys


# The catalogue spellings of one (code, edition); attendance/roster.py and
# attendance/qr_email.py both need it by a public name.
event_keys = _event_keys


def event_meta(event_code, edition=None):
    """
    Name, start date and the three upcoming events, from the catalogue.

    The DATE is what the change deadline is computed from, so this is not
    decoration. It comes from Event.event_date rather than from the workbook EA
    tab, whose Event Dates column holds text like "February 2-3" and cannot be
    subtracted from.

    The three upcoming events are the header the real check-in sheet carries,
    `=XLOOKUP(Control!B1, EA!A:A, EA!D:F)`, and the CRM already stores them on
    the Event row.
    """
    rows = {
        (row["event_code"] or "").strip().upper(): row
        for row in Event.objects.values(*_EVENT_FIELDS)
    }
    for key in _event_keys(event_code, edition):
        row = rows.get(key)
        if row:
            return _event_detail(row)
    # EXACT MATCH ONLY, deliberately.
    #
    # A family fallback was built here and removed. It matched a bare delegate
    # code like "CCC" to a catalogue code like "APR2027_CCC-VV" and then had to
    # CHOOSE an edition, because a family has several and the delegate row names
    # none. An event name and date printed on a report header have to be the
    # right edition or they mislead, so no name is the correct answer when the
    # code does not resolve exactly. 84 of 241 pickable events are in that
    # position; the picker shows their code, which is a fact.
    return _event_detail(None)


_EVENT_FIELDS = (
    "event_code", "name", "official_name", "event_date", "end_date",
    "city", "country", "venue", "location", "status",
    "upcoming_event_1", "upcoming_event_2", "upcoming_event_3",
    # Badge-email details; see attendance/qr_email.py.
    "registration_opens", "registration_closes", "start_time",
)


def _event_detail(row):
    """
    One event as the picker and the report headers want it.

    TWO TRAPS IN Event.save() THAT THIS HAS TO WORK AROUND, both measured
    against the live catalogue rather than guessed at.

    THE PLACE IS STORED THREE TIMES. `if self.location: self.city = country =
    venue = self.location`, so all three hold one string. Every one of the 28
    events carrying a city has city EXACTLY EQUAL to country, and that string is
    already a full place, "Calgary, Alberta, Canada". Joining city and country
    printed it twice. So the parts are de-duplicated, in order, and a part
    already contained in what has been kept is dropped.

    `name` IS DERIVED, NOT AUTHORED. save() overwrites it with
    official_event_name, or with the event_code when that is blank, so assigning
    `name` directly is silently discarded. Reading it is fine, because it mirrors
    official_event_name; writing it is not, which is worth knowing before
    anybody tries to fix a display here by editing an Event.
    """
    if not row:
        return {"event_name": "", "official_name": "", "event_date": None,
                "end_date": None, "location": "", "venue": "", "status": "",
                "upcoming_events": [], "registration_opens": "",
                "registration_closes": "", "start_time": ""}
    parts = []
    for candidate in (row.get("city"), row.get("country"), row.get("location")):
        candidate = (candidate or "").strip()
        if candidate and not any(candidate in kept for kept in parts):
            parts.append(candidate)
    place = ", ".join(parts)
    return {
        "event_name": row.get("name") or "",
        "official_name": row.get("official_name") or "",
        "event_date": row.get("event_date"),
        "end_date": row.get("end_date"),
        "location": place,
        "venue": row.get("venue") or "",
        "status": row.get("status") or "",
        "upcoming_events": [
            e for e in (row.get("upcoming_event_1"), row.get("upcoming_event_2"),
                        row.get("upcoming_event_3")) if e
        ],
        "registration_opens": row.get("registration_opens") or "",
        "registration_closes": row.get("registration_closes") or "",
        "start_time": row.get("start_time") or "",
        # The raw venue, before the place de-duplication above folds it away.
        # missing_fields() has to know whether a venue was ever recorded, which
        # `venue` cannot answer once save() has fanned `location` across it.
        "raw_city": row.get("city") or "",
    }


def pickable_events():
    """
    The (event_code, edition) pairs the delegates table actually holds.

    Read from delegates rather than from Events, for the join reason in the
    module docstring. Events is consulted afterwards for a display name, and an
    event with no name simply shows its code.
    """
    rows = (
        BookDelegate.objects
        .exclude(event_code="")
        .values("event_code", "edition")
        .annotate(delegates=Count("id"))
        .order_by("-edition", "event_code")
    )
    rows = list(rows)

    # One query for the whole catalogue, indexed by code, then matched per pair.
    catalogue = {
        (row["event_code"] or "").strip().upper(): row
        for row in Event.objects.values(*_EVENT_FIELDS)
    }
    out = []
    for row in rows:
        code = (row["event_code"] or "").strip()
        edition = row["edition"]
        # Exact match only; see the note in event_meta.
        found = next((catalogue[k] for k in _event_keys(code, edition)
                      if k in catalogue), None)
        out.append({
            "event_code": code,
            "edition": edition,
            "delegates": row["delegates"],
            **_event_detail(found),
        })
    return out


# ── row helpers ───────────────────────────────────────────────────────────────

# A placeholder is the marker as a WHOLE WORD at the start, so "TBA" and
# "TBA - Sales Director" match while "Tbarak" does not.
#
# WHY THIS IS STRICTER THAN THE WORKBOOK, deliberately. Report_Additional tests
# LEFT(TRIM(x), 3) = "TBA", a bare three character prefix, which also matches a
# real person called Tbarak and a company called Tbatec. That is the same class
# of over-match booking_code.py exists to prevent, and it costs nothing to close:
# every genuine placeholder in the log is either the marker alone or the marker
# followed by punctuation.
_PLACEHOLDER = re.compile(
    r"^(?:%s)(?![A-Za-z0-9])" % "|".join(TBA_MARKERS), re.IGNORECASE,
)


def _is_placeholder(value):
    return bool(_PLACEHOLDER.match((value or "").strip()))


def is_tba(delegate):
    """
    True when this row is a placeholder rather than a person.

    The COMPANY is checked as well as the name, following Report_Additional. A
    booking held under "TBA" with a real company is still nobody in particular,
    and so is a real name at a company nobody has confirmed yet.
    """
    name = f"{delegate.first_name} {delegate.last_name}".strip()
    return _is_placeholder(name) or _is_placeholder(company_of(delegate))


def role_of(booking_code):
    """
    What the desk needs to know about this person, as the workbook prints it.

    Report_CheckIn!A5 is the authority, and it is cleverer than it looks:

        IF(REGEXMATCH(N, "(?i)SpEx"), N,
        IF(REGEXMATCH(N, "(?i)(Speaker|SPP)"), "Speaker", ""))

    SpEx is tested FIRST, and when it matches the cell shows the WHOLE booking
    code rather than a label. So a sponsor reads as "GLD SpEx", a speaker reads
    as "Speaker", and somebody who is both reads as "Speaker / GLD SpEx", which
    tells the desk both facts in one column.

    AN EARLIER VERSION OF THIS INVERTED THE PRECEDENCE, on the reasoning that
    the desk most needs to spot who is going on stage. That reasoning was sound
    and the conclusion was wrong: showing the full code answers it without
    having to choose, and choosing threw away the sponsorship tier. Reverted to
    match the sheet.

    The marker lists still come from booking_code.py rather than being retyped,
    so a new SpEx or speaker spelling is understood in one place.
    """
    code = (booking_code or "").strip()
    if not code:
        return ""
    if any(code.lower() == e.lower() for e in spex_exact()):
        return code
    if any(boundary_regex(m).search(code) for m in spex_markers()):
        return code
    if any(boundary_regex(m).search(code) for m in speaker_markers()):
        return "Speaker"
    return ""


def company_of(delegate):
    return delegate.company.name if delegate.company_id else delegate.company_name_raw


def payment_status_of(delegate):
    return effective_value(delegate, "delegate_payment_status", "payment_status")


def _norm(value):
    """How two spellings of a name or company are compared. Never for display."""
    return " ".join((value or "").split()).casefold()


def _base_row(delegate):
    return {
        "delegate_id": delegate.id,
        "name": f"{delegate.first_name} {delegate.last_name}".strip(),
        "company": company_of(delegate),
        "email": delegate.email,
        "position": delegate.position,
        "booking_code": delegate.booking_code,
        "invoice_number": delegate.invoice_id,
    }


# ── the six projections ───────────────────────────────────────────────────────

def name_badges(rows):
    """
    NAME BADGES. Every booking on the event, full name and company, by company.

    Two columns and nothing else, because that is what goes on a badge.

    TBA ROWS ARE DROPPED, not blanked. The workbook keeps them with the name
    cleared, on the theory that a blank badge is one somebody writes at the desk,
    and this followed it for a while. Dropped instead, on instruction, and it is
    the better rule: a blank badge is stock, not a badge, so printing one per
    unnamed booking wastes a badge and leaves a nameless card on the table for
    somebody to puzzle over. A placeholder becomes a real badge through
    Additional Name Badges the moment the booking gets a name.

    NO PAYMENT FILTERING AT ALL. Pending, Refunded, the credit statuses and
    CANCELLED bookings are all here, which is what "includes all the bookings"
    means and why this list is bigger than the check-in sheet. Cancelled is
    excluded from Additional Name Badges and only from there; see
    CANCELLED_STATUSES.
    """
    return sorted(
        (_base_row(d) for d in rows if on_badge_list(d)),
        key=lambda r: (_norm(r["company"]), _norm(r["name"])),
    )


def check_in(rows):
    """
    Report_CheckIn. The desk sheet, sorted by company.

    The workbook's deliberately blank tick column is `attendance` here, which is
    a real field the Bookings page already shows, so a tick at the desk is
    visible to everyone rather than being ink on one printout.

    THIS is the tab that filters. `rows` is the whole event population, and
    at_desk() applies the four clauses Report_CheckIn applies.
    """
    out = []
    for delegate in rows:
        if not at_desk(delegate):
            continue
        row = _base_row(delegate)
        status = payment_status_of(delegate)
        row.update({
            # THE WORKBOOK'S OWN TWO ODD COLUMNS, kept as it has them.
            #
            # Its "Booking Code" column does not print the booking code; it
            # prints the whole code for a SpEx line, the word Speaker for a
            # speaker, and blank for an ordinary delegate. See role_of.
            #
            # Its "Payment Status" column does not print the status either; it
            # prints one sentence, and only for Pending. Both are reproduced
            # rather than corrected, because the desk reads these on paper and
            # a column of "Paid" against every name tells it nothing.
            "booking_code_label": role_of(delegate.booking_code),
            "payment_status_label": (COLLECT_ON_SITE_NOTE
                                     if status == COLLECT_ON_SITE_STATUS else ""),
            # The raw values, for anyone who wants to filter or export on them.
            "payment_status": status,
            "attendance": delegate.attendance,
            "checked_in": delegate.attendance == BookDelegate.Attendance.CONFIRMED,
        })
        out.append(row)
    return sorted(out, key=lambda r: (_norm(r["company"]), _norm(r["name"])))


def latest_badges(event_code, edition=None):
    """
    The most recent badge issued to each delegate at this event, plus the badges
    whose delegate row is gone.

    One query, newest first, first row per delegate wins. Orphans are keyed by
    their own row id because they have no delegate to key on, and they still
    have to reach the take out list.
    """
    qs = BadgeIssue.objects.filter(event_code__iexact=(event_code or "").strip())
    if edition:
        qs = qs.filter(edition=edition)

    by_delegate, orphans = {}, []
    for badge in qs.order_by("-issued_at", "-id"):
        if badge.delegate_id is None:
            orphans.append(badge)
        elif badge.delegate_id not in by_delegate:
            by_delegate[badge.delegate_id] = badge
    return by_delegate, orphans


def change_window_days():
    from .models import DEFAULT_CHANGE_WINDOW_DAYS
    return int(getattr(settings, "PRE_EVENT_DOCS_CHANGE_WINDOW_DAYS",
                       DEFAULT_CHANGE_WINDOW_DAYS))


def change_deadline(event_date):
    """
    The last day a badge change can still be actioned, or None.

    EVENT START DATE MINUS 14 CALENDAR DAYS, weekends included. This is a
    FREEZE DATE, not a countdown from when badges were sent, which is what an
    earlier version of this had. The difference matters in both directions: a
    change arriving today is urgent or not depending on how close the event is,
    not on how long ago the badges went out, and a badge issued months early
    does not go stale on its own.

    It also removes a dependency this could not have satisfied. The workbook
    Database_Sent log has 5,318 rows and its Stamp column is empty in every one
    of them, so a badge-relative window could never have been computed for the
    imported history. An event-relative one needs no issue date at all.

    Calendar days, deliberately. Weekends count, because the printer deadline
    does not care what day of the week it falls on.
    """
    if not event_date:
        return None
    return event_date - timedelta(days=change_window_days())


def _window(deadline):
    """Where today sits relative to the freeze date. Same shape for every row."""
    if deadline is None:
        # No event date in the catalogue, so there is no deadline to be inside
        # or outside of. Reported as unknown rather than as met or missed.
        return {"deadline": None, "days_left": None, "in_window": None}
    remaining = (deadline - timezone.localdate()).days
    return {"deadline": deadline, "days_left": remaining, "in_window": remaining >= 0}


def badge_changes(rows, event_code, edition=None, event_date=None):
    """
    TWO LISTS, returned as (additional, cancellations).

    THE WORKBOOK KEEPS THESE APART AND SO DOES THIS. Report_Additional carries
    two headers, "Additional Name Badges" on the left and "Cancellations" on the
    right. They were merged here for a while, with a removal appearing under the
    additional list as a "Remove" remark, and that was wrong: a badge to pull off
    the table is not an additional name badge. One report is badges to PRINT, the
    other is badges to TAKE OFF, and they are two different jobs for two
    different people.

    BOTH COMPARE AGAINST THE NAME BADGE POPULATION, WHICH IS WHAT WAS FROZEN.
    This is the important line in this docstring. An earlier version compared
    against the CHECK-IN SHEET, and that produced nonsense the moment anybody
    used it: a real event froze 46 name badges, its check-in sheet held 19, and
    the report showed one genuine change plus THIRTY cancellations. None of those
    thirty were cancelled. They were unpaid bookings, media lines and speaker
    tables, which never reach the desk sheet and were never meant to.

    The rule is simply that a diff has to be taken against the list that was
    frozen. You freeze Name Badges, so Name Badges is what "changed since" means.
    Comparing one population against a different one can only ever report the
    difference between the two populations, which is not a change at all.

    ADDITIONAL NAME BADGES, full name, company, remark:

        New Badge          on the check-in sheet, never badged
        Name Change        badged, and the name has changed since
        Company Change     badged, and the company has changed since

    CANCELLATIONS, full name and company:

        badged, and no longer on the badge list at all, so the booking was
        deleted or turned back into a placeholder. NOT merely cancelled;
        see CANCELLED_STATUSES

    THE WORKBOOK USED TWO DIFFERENT POPULATIONS FOR THIS and it cost it. Its
    left column ran over everybody booked while its right column compared
    against Report_CheckIn, so an unpaid delegate was in the first and not the
    second: flagged as a new badge, printed, then flagged for removal, for ever.
    One population for both questions is what stops that.

    NOTHING FROZEN MEANS NOTHING TO REPORT, and this is the first rule applied.
    Opening an event for the first time, BOTH lists are EMPTY. They have to be:
    they answer "what has changed since the badges were sent", and until they
    have been sent there is no since. An earlier version had no such guard, so a
    fresh event listed every single person on the check-in sheet as a New Badge,
    which read as a hundred corrections to make before anybody had printed
    anything.

    Once a freeze exists the three remarks all become meaningful, New Badge
    included: somebody who booked AFTER the freeze genuinely needs an additional
    badge, and that is the whole purpose of the report.

    TBA rows are excluded entirely. A blank badge is not a change to chase.

    THE DEADLINE is the same for every row, because it belongs to the event and
    not to a badge; see change_deadline. It is carried on the rows rather than
    filtered by, so the page can lead with what still needs doing without ever
    hiding an overdue row.
    """
    by_delegate, orphans = latest_badges(event_code, edition)

    # NO FREEZE, NO REPORT. See the note above; this is not an optimisation.
    if not by_delegate and not orphans:
        return [], []

    window = _window(change_deadline(event_date))

    # TWO POPULATIONS, and the difference between them is the cancelled rule.
    #
    # `badged` is what a freeze captured, so it is what CANCELLATIONS are
    # measured against: a frozen badge whose booking is no longer even on the
    # badge list has to come off the table.
    #
    # `printable` is the narrower set ADDITIONAL is built from, because a
    # cancelled booking must never be offered as a badge to print.
    #
    # A cancelled booking is therefore in the first and not the second, which is
    # exactly what makes it invisible to both halves of this report rather than
    # being listed as a cancellation of a badge it is still meant to have.
    badged = {d.id: d for d in rows if on_badge_list(d)}
    printable = {i: d for i, d in badged.items() if needs_a_badge_printed(d)}

    additional, cancellations = [], []
    for delegate in printable.values():
        row = _base_row(delegate)
        badge = by_delegate.get(delegate.id)
        if badge is None:
            row.update(remark="New Badge", was_name="", was_company="",
                       badged_on=None, **window)
            additional.append(row)
            continue

        name_changed = _norm(badge.name) != _norm(row["name"])
        company_changed = _norm(badge.company) != _norm(row["company"])
        if not (name_changed or company_changed):
            continue
        if name_changed and company_changed:
            remark = "Name & Company Change"
        elif name_changed:
            remark = "Name Change"
        else:
            remark = "Company Change"
        row.update(remark=remark, was_name=badge.name, was_company=badge.company,
                   badged_on=badge.issued_at, **window)
        additional.append(row)

    for badge in list(by_delegate.values()) + orphans:
        # `badged`, not `printable`: a cancelled booking is still on the badge
        # list, so its badge is not one to pull off the table.
        if badge.delegate_id is not None and badge.delegate_id in badged:
            continue
        cancellations.append({
            "badge_id": badge.id,
            "delegate_id": badge.delegate_id,
            "name": badge.name,
            "company": badge.company,
            # Why it is off the sheet, which is the one thing the desk cannot
            # work out for itself from a name and a company.
            "reason": ("Booking removed" if badge.delegate_id is None
                       else "No longer on the check-in sheet"),
            "badged_on": badge.issued_at,
            **window,
        })

    # Both by company, as specified. The remark is the tiebreak on the additional
    # list so a company with two changes reads in a stable order rather than by id.
    additional.sort(key=lambda r: (_norm(r["company"]), r["remark"], _norm(r["name"])))
    cancellations.sort(key=lambda r: (_norm(r["company"]), _norm(r["name"])))
    return additional, cancellations


def badge_run_detail(run_id):
    """
    THE FROZEN SNAPSHOT of one badge run, row by row, and whether it still holds.

    This exists to answer "how do I verify the stored record is proper". Each row
    carries the name and company AS FROZEN, plus a `drift` saying how the
    delegate reads today:

        Matches          the booking still says exactly what the badge says
        Name changed     the frozen name is no longer the delegate's name
        Company changed  likewise for the company
        Both changed     both
        Booking removed  the delegate row is gone; the badge is still recorded

    DRIFT IS NOT AN ERROR. A row reading Name changed means the log is doing its
    job, remembering the older spelling so Additional Name Badges can spot the
    correction. A run where every row reads Matches is one nothing has moved on.

    One query, and the delegate is select_related because every row reads it.
    """
    rows = (BadgeIssue.objects
            .filter(run_id=run_id)
            .select_related("delegate", "delegate__company", "issued_by")
            .order_by("company", "name"))

    out = []
    for badge in rows:
        delegate = badge.delegate
        if delegate is None:
            drift = "Booking removed"
            now_name = now_company = ""
        else:
            now_name = f"{delegate.first_name} {delegate.last_name}".strip()
            now_company = company_of(delegate)
            name_moved = _norm(now_name) != _norm(badge.name)
            company_moved = _norm(now_company) != _norm(badge.company)
            drift = ("Both changed" if name_moved and company_moved
                     else "Name changed" if name_moved
                     else "Company changed" if company_moved
                     else "Matches")
        out.append({
            "badge_id": badge.id,
            "delegate_id": badge.delegate_id,
            # As frozen. Never re-read from the delegate; that is the point.
            "name": badge.name,
            "company": badge.company,
            # As the booking reads today, for the eye to compare against.
            "name_now": now_name,
            "company_now": now_company,
            "drift": drift,
        })
    return out


def badge_runs(event_code, edition=None):
    """
    The run history, one row per badge run, newest first.

    `drifted` is what makes a run verifiable at a glance: how many of its frozen
    rows no longer match the booking. Counted here rather than in the page so the
    list and the detail view can never disagree about it.
    """
    qs = BadgeIssue.objects.filter(event_code__iexact=(event_code or "").strip())
    if edition:
        qs = qs.filter(edition=edition)
    runs = [
        {
            "run_id": row["run_id"],
            "badges": row["badges"],
            "issued_at": row["issued_at"],
            "issued_by": row["issued_by__username"],
        }
        for row in qs.values("run_id", "issued_by__username")
                     .annotate(badges=Count("id"), issued_at=Max("issued_at"))
                     .order_by("-issued_at")
    ]

    # Drift per run, from ONE pass over this event's badges rather than a query
    # per run. The rows are already needed for nothing else, so this is the only
    # read, and a 200 badge event is 200 rows.
    if runs:
        drifted = {r["run_id"]: 0 for r in runs}
        for badge in qs.select_related("delegate", "delegate__company"):
            delegate = badge.delegate
            moved = (
                delegate is None
                or _norm(f"{delegate.first_name} {delegate.last_name}".strip())
                != _norm(badge.name)
                or _norm(company_of(delegate)) != _norm(badge.company)
            )
            if moved and badge.run_id in drifted:
                drifted[badge.run_id] += 1
        for run in runs:
            run["drifted"] = drifted.get(run["run_id"], 0)
    return runs
