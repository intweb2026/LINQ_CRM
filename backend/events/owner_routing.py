"""
events/owner_routing.py
───────────────────────
Re-resolve the SCA name already stored on events into the FK that grants
visibility, for rows that were written before the person existed.

THE PRODUCTION FAILURE THIS CLOSES
`Event.sales_executive` is only ever resolved while an event row is being saved.
Add four sales accounts today and every event that names them in `sales_team` was
last saved yesterday, against a user table that did not contain them, so the FK
on all of those rows is null. events/views.py get_queryset scopes a non admin to
`Q(assigned_users=user) | Q(sales_executive=user)`, so those four see an empty
Events table, an empty Bookings table and an empty event dropdown, while their
name is sitting in plain sight in the SCA column of the rows they own.

Creating an account is therefore not enough on its own, and never was. This
module is the missing half. It runs automatically whenever a user is created or
renamed through the API (accounts/serializers.py), and on demand as

    python manage.py route_event_owners --dry-run

WHAT IT WILL NOT DO
Never takes an event away from somebody. Only rows whose `sales_executive` is
NULL are considered, unless `reassign=True` is passed deliberately, which the
command exposes as an explicit flag and nothing calls implicitly. An ambiguous or
unmatched name is reported and skipped; see accounts/user_resolution.py for why
guessing is worse than leaving it.

WHY queryset.update AND NOT save()
The answer is already computed here, so a save() would resolve it a second time,
and Event.save() also rewrites name, official_name, city, country and venue from
other columns. On legacy rows imported years ago those derivations would fire as
a side effect of a routing fix, which is a change nobody asked this command to
make. Only the two owner columns are written.
"""
from accounts.user_resolution import AMBIGUOUS, OwnerResolver, is_blank_name

from .models import Event


def _display(user):
    return user.get_full_name() or user.username


def route_events(users=None, commit=True, reassign=False):
    """
    Resolve `sales_team` to `sales_executive` across the catalogue.

    users     restrict the changes to matches that land on these users. This is
              what makes the "new starter" call cheap and safe; adding one
              account cannot re-home an event onto somebody else because a
              different name happened to become resolvable in the same pass.
    commit    False makes this a pure report, which is what --dry-run passes.
    reassign  also consider rows that ALREADY have a sales_executive. Off by
              default; on, a rename in the SCA column moves the event.

    Returns a report dict, safe to log or to return from an API call,
        {"routed": [...], "ambiguous": [...], "unmatched": [...],
         "considered": int, "committed": bool}
    Each entry in "routed" is {event_code, name, user_id, user}; the other two
    lists carry {event_code, name}, so a human can see exactly which spellings
    need fixing. `resolver.report()` on the resolver would give the same names
    weighted by row count, which is what the command prints.
    """
    wanted_ids = {u.pk for u in users} if users is not None else None

    qs = Event.objects.all()
    if not reassign:
        qs = qs.filter(sales_executive__isnull=True)

    # The user table is read ONCE for the whole run, which is the reason
    # accounts.user_resolution is built around a resolver object.
    resolver = OwnerResolver()

    report = {"routed": [], "ambiguous": [], "unmatched": [],
              "considered": 0, "committed": bool(commit)}

    for event in qs.only("id", "event_code", "sales_team", "sales_executive").iterator():
        if is_blank_name(event.sales_team):
            continue
        report["considered"] += 1

        user, reason = resolver.resolve(event.sales_team)

        if user is None:
            bucket = "ambiguous" if reason == AMBIGUOUS else "unmatched"
            report[bucket].append({
                "event_code": event.event_code,
                "name": event.sales_team,
            })
            continue
        if wanted_ids is not None and user.pk not in wanted_ids:
            continue
        if event.sales_executive_id == user.pk:
            continue

        report["routed"].append({
            "event_code": event.event_code,
            "name": event.sales_team,
            "user_id": user.pk,
            "user": _display(user),
        })
        if commit:
            # Only the two owner columns; see the module docstring.
            Event.objects.filter(pk=event.pk).update(
                sales_executive=user,
                sales_team=_display(user),
            )

    return report


def route_events_for_user(user, commit=True):
    """
    The events this one user should already have been able to see. Called after a
    user is created, or after their name, username or email changes, because all
    three change what `sales_team` text resolves to.

    Returns the same report shape as route_events(), so a caller can log how many
    rows a new account picked up. An inactive account is skipped; resolution only
    ever looks at active users, so there would be nothing to find.
    """
    if not user or not user.is_active:
        return {"routed": [], "ambiguous": [], "unmatched": [],
                "considered": 0, "committed": False}
    return route_events(users=[user], commit=commit)
