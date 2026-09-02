"""
events/testutils.py
────────────────────
Naming a reviewer on an event the way the event modal does, for the tests in
paper_review and proposal_submission; both answer scope from the shared
accounts.user_resolution.event_codes_naming.

Scope used to come from `user.assigned_events.set([...])`, a path the event modal
cannot reach, which is why a reviewer named on four events could file against one
and no test noticed.
"""


def assign_reviewer(user, *events, junior=False):
    """
    Make `user` the named reviewer on each event, as the event modal would.

    Writes the FULL NAME where there is one, falling back to the username, which
    is what the modal's owner select stores. `junior=True` uses the Market
    Research Jr. column instead; access.py reads both, and a test that cares
    which one granted the event should say so.

    Uses queryset.update rather than save(): Event.save() rewrites name,
    official_name, city, country and venue from other columns, and a test that
    only wanted to name a reviewer should not have its event renamed underneath
    it.
    """
    from events.models import Event

    column = "market_research_junior" if junior else "market_research_senior"
    name = user.get_full_name() or user.username
    Event.objects.filter(pk__in=[e.pk for e in events]).update(**{column: name})
    for event in events:
        setattr(event, column, name)
