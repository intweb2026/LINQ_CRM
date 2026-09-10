"""
attendance/access.py
─────────────────────
WHO MAY WORK WHICH DOOR. One definition, read by the queryset scope and by
every action that names an event.

Admin sees every event. A sales executive sees theirs. An on-site door user is
the SAME mechanism with no extra machinery: their narrow view comes from holding
only the `attendance` module, not from a role check here.

WHY User.visible_event_codes() AND NOT paper_review/access.py
paper_review resolves an assignment through the `assigned_events` M2M alone,
which is right for that app and wrong here. On the live catalogue that M2M is
empty on every one of the 142 events while Event.sales_executive is set on most
of them, so copying it would answer "no events" for every sales executive while
passing perfectly against hand-made fixtures. visible_event_codes() asks both
questions, and asks them over a lead's reports as well as themselves.

None MEANS UNRESTRICTED, [] MEANS RESTRICTED AND EMPTY. Every caller tests
`is None`, never truthiness, and an empty list scopes to .none() rather than to
everything. A scope that degenerates into "see it all" is the failure worth
guarding against, so it is spelled out at both call sites.

THE SPELLING MISMATCH THIS HAS TO CROSS
visible_event_codes() returns CATALOGUE codes ("ACU25", "FEB2027_BIZ-PM"). An
attendance row and a delegate row carry the DELEGATE-side pair, ("ACU", 2025),
because BookDelegate.save() strips the trailing year out of the code. Comparing
the two directly returns nothing at all, silently — the trap named at the top of
pre_event_docs/services.py. So a permitted catalogue code is turned into the
delegate-side pairs it covers, through the same mapping that module uses.
"""
from django.db.models import Q

from book_delegate.models import BookDelegate

from .roster import event_keys


def unrestricted(user):
    """Does this caller see every event? Admin, HP, or an is_all_access team."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return getattr(user, "is_admin", False) or getattr(user, "has_all_access", False)


def permitted_pairs(user):
    """
    The delegate-side (event_code, edition) pairs this caller may work, or None
    for unrestricted.
    """
    if unrestricted(user):
        return None
    wanted = {
        code.strip().upper()
        for code in (user.visible_event_codes() or [])
        if code and code.strip()
    }
    if not wanted:
        return []
    pairs = (
        BookDelegate.objects
        .exclude(event_code="")
        # .order_by() BEFORE .values_list().distinct(). BookDelegate.Meta.ordering
        # names two columns, and default ordering leaks into the SELECT, so
        # DISTINCT would be taken over the ordering columns too and return one
        # row per invoice instead of one per event.
        .order_by()
        .values_list("event_code", "edition")
        .distinct()
    )
    return [
        (code, edition) for code, edition in pairs
        if any(key in wanted for key in event_keys(code, edition))
    ]


def may_work_event(user, event_code, edition=None) -> bool:
    """Whether this caller may scan, roster or summarise one event."""
    pairs = permitted_pairs(user)
    if pairs is None:
        return True
    code = (event_code or "").strip().casefold()
    if not code:
        return False
    # BOTH halves must match. An `edition is None` escape was written here and
    # removed: it let a caller permitted on ACU 2025 alone reach ACU 2026 by
    # simply omitting the edition, and the frontend always sends the pair it got
    # from events/ anyway.
    return any(
        (permitted or "").strip().casefold() == code and permitted_edition == edition
        for permitted, permitted_edition in pairs
    )


def permitted_events(user, events):
    """`events` narrowed to the ones this caller may work."""
    pairs = permitted_pairs(user)
    if pairs is None:
        return events
    allowed = {((c or "").strip().casefold(), e) for c, e in pairs}
    return [
        event for event in events
        if ((event["event_code"] or "").strip().casefold(), event["edition"]) in allowed
    ]


def scope_records(qs, user):
    """
    Restrict the arrival log to the caller's events.

    Applied to the BASE queryset in the viewset, before any filter backend, so a
    hand-written ?event_code= naming somebody else's event intersects with this
    to nothing rather than replacing it.
    """
    pairs = permitted_pairs(user)
    if pairs is None:
        return qs
    if not pairs:
        return qs.none()
    scope = Q()
    for code, edition in pairs:
        scope |= Q(event_code__iexact=code, edition=edition)
    return qs.filter(scope)
