"""
Move access off the per-user CustomRole and onto the team.

THE GUARANTEE THIS MIGRATION MAKES
Nobody's effective permissions change. Not "roughly the same", not "the common
case is fine" — every user's resolved matrix after this migration is identical
to the one they had before it, cell for cell.

It is done by construction rather than by hope:

  1. Each team adopts the permission set MOST of its members already held. That
     is the answer that needs the fewest exceptions, not a guess.
  2. Every user is then compared, cell by cell, against the grid their team just
     adopted, and every difference is written as a UserPermission delta.

Step 2 is what makes step 1 safe. A team whose members disagreed does not have
to be resolved by picking a winner and quietly demoting or promoting the rest —
the minority keep exactly what they had, as explicit per-person exceptions.

In the data this was written against, one team needed that: Sales Team held 15
people on the Sales set and 4 on Speaker Sales. Those four come out the other
side with deltas, which is precisely the mechanism that replaced the second
hierarchy.
"""
from collections import Counter

from django.db import migrations

# The module list AS IT STANDS AT THIS MIGRATION. Deliberately a literal and not
# an import of accounts.models.CRM_MODULES: a migration replays history, and
# reading a constant that later grows would make this step behave differently on
# a database migrated tomorrow than it did on the one migrated today.
MODULES = [
    "bookings", "ticket_central", "events", "reports",
    "users", "teams", "performance", "webhooks", "roles",
    "paper_review", "proposal_submission",
]
ACTIONS = ["view", "create", "update", "delete"]


def _matrix_from_role(role, permission_rows):
    """The dense matrix a CustomRole resolved to, under the OLD rules."""
    if role is None:
        return {m: {a: False for a in ACTIONS} for m in MODULES}
    if role.is_all_access:
        return {m: {a: True for a in ACTIONS} for m in MODULES}
    matrix = {m: {a: False for a in ACTIONS} for m in MODULES}
    for row in permission_rows:
        if row.module in matrix:
            matrix[row.module] = {a: bool(getattr(row, f"can_{a}")) for a in ACTIONS}
    return matrix


def forwards(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    UserPermission = apps.get_model("accounts", "UserPermission")
    RolePermission = apps.get_model("accounts", "RolePermission")
    Team = apps.get_model("teams", "Team")
    TeamPermission = apps.get_model("teams", "TeamPermission")

    # Every role's matrix, resolved once.
    rows_by_role = {}
    for row in RolePermission.objects.all():
        rows_by_role.setdefault(row.custom_role_id, []).append(row)

    roles = {}
    for role in apps.get_model("accounts", "CustomRole").objects.all():
        roles[role.id] = (role, _matrix_from_role(role, rows_by_role.get(role.id, [])))

    users = list(User.objects.all())

    # ── 1. Each team adopts its members' majority set ───────────────────────
    for team in Team.objects.all():
        held = Counter(
            u.custom_role_id for u in users
            if u.team_id == team.id and u.custom_role_id is not None
        )
        if not held:
            continue
        # most_common is insertion-ordered on ties, so a tie resolves to the
        # first role encountered rather than at random; either way step 2 makes
        # the losers whole.
        winner_id, _ = held.most_common(1)[0]
        role, matrix = roles[winner_id]

        if role.is_all_access:
            team.is_all_access = True
            team.save(update_fields=["is_all_access"])
            continue

        TeamPermission.objects.bulk_create([
            TeamPermission(
                team=team, module=module,
                can_view=cells["view"], can_create=cells["create"],
                can_update=cells["update"], can_delete=cells["delete"],
            )
            for module, cells in matrix.items()
        ])

    # ── 2. Every difference becomes an explicit per-person delta ────────────
    team_matrices = {}
    for team in Team.objects.all():
        if team.is_all_access:
            team_matrices[team.id] = {m: {a: True for a in ACTIONS} for m in MODULES}
        else:
            matrix = {m: {a: False for a in ACTIONS} for m in MODULES}
            for row in TeamPermission.objects.filter(team_id=team.id):
                if row.module in matrix:
                    matrix[row.module] = {a: bool(getattr(row, f"can_{a}")) for a in ACTIONS}
            team_matrices[team.id] = matrix

    empty = {m: {a: False for a in ACTIONS} for m in MODULES}
    deltas = []
    for user in users:
        had = roles[user.custom_role_id][1] if user.custom_role_id in roles else empty
        # HP bypasses every check in code, so it needs no rows either way.
        if user.username == "HP":
            continue
        now = team_matrices.get(user.team_id, empty)
        for module in MODULES:
            cells = {
                f"can_{a}": (had[module][a] if had[module][a] != now[module][a] else None)
                for a in ACTIONS
            }
            if any(v is not None for v in cells.values()):
                deltas.append(UserPermission(user_id=user.id, module=module, **cells))

    UserPermission.objects.bulk_create(deltas)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0022_userpermission"),
        ("teams", "0003_team_permissions"),
    ]

    operations = [
        # Irreversible in the only sense that matters: 0024 drops the source
        # tables, so there is nothing for a reverse to read back from.
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
