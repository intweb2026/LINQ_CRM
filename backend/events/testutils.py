"""
events/testutils.py
────────────────────
Two helpers, so every test that needs a reviewer's scope sets it up the way the
product does. Here rather than in either app, because both paper_review and
proposal_submission answer scope from the same shared function and both must set
it up the same way — see accounts.user_resolution.event_codes_naming.

Scope used to come from `user.assigned_events.set([...])`, and every test in both
apps wrote that line. It was never how a real assignment happened: the M2M is written by the
CSV importer and by nothing a human touches, so the tests were exercising a path
the event modal cannot reach — which is precisely why a reviewer named on four
events could file against one and no test noticed.

Both access modules now read the event's Market Research columns instead.
Calling this instead of touching the M2M means a test setup and a real assignment
are the same act.
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
    Event.objects.filter(pk__in=[e.pk for e in events]).update(
        **{column: user.get_full_name() or user.username}
    )
    for event in events:
        setattr(event, column, user.get_full_name() or user.username)


def unassign_reviewer(*events, junior=False):
    """The other direction, for the tests that check an empty scope refuses."""
    from events.models import Event

    column = "market_research_junior" if junior else "market_research_senior"
    Event.objects.filter(pk__in=[e.pk for e in events]).update(**{column: ""})
    for event in events:
        setattr(event, column, "")
