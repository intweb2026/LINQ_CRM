"""
Register the "google_sync" module on every existing team.

Google Sync had no module of its own: the page was gated on "webhooks" in the
frontend and on IsAdminRole in the backend, so one cell decided both "may manage
sheet pushes" and "may replay webhook deliveries", and neither could be granted
to a non-admin at all. CRM_MODULES gained "google_sync" so the two can be
granted apart.

A team with no TeamPermission row for a module is already treated as no-access by
crm_permission() and by my-permissions, so this backfill is not needed for
safety. It IS needed for the permissions grid: the page renders one row per
module and reads its initial state from the stored rows, so without these the new
row would render unticked and then save back a full set anyway. Materialising
them all-False keeps what the admin sees and what the database holds identical.

NOBODY GAINS ACCESS HERE. Every row is all-False, which is exactly the access a
non-admin had before — Google Sync was admin-only. All-access teams and the HP
account are unaffected either way: their matrix is generated from CRM_MODULES at
request time, so they pick the module up automatically. Teams that could reach
the page by being admin still can, for the same reason.
"""
from django.db import migrations

NEW_MODULES = ["google_sync"]


def add_modules(apps, schema_editor):
    Team           = apps.get_model("teams", "Team")
    TeamPermission = apps.get_model("teams", "TeamPermission")

    for team in Team.objects.all():
        for module in NEW_MODULES:
            TeamPermission.objects.get_or_create(
                team=team,
                module=module,
                defaults={
                    "can_view":   False,
                    "can_create": False,
                    "can_update": False,
                    "can_delete": False,
                },
            )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0026_drop_reports_module"),
        ("teams", "0003_team_permissions"),
    ]

    operations = [
        # Reverse is a deliberate no-op rather than a delete, following 0020. The
        # rows this creates are all-False and `module` has no referential
        # constraint, so leaving them behind after an unapply is inert — an
        # unrecognised module key surfaces as a no-access entry and nothing reads
        # it.
        migrations.RunPython(add_modules, migrations.RunPython.noop),
    ]
