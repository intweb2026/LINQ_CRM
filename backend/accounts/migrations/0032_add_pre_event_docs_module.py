"""
Register the "pre_event_docs" module on every existing team.

Pre-Event Docs replaces the PRE EVENT DOCS workbook: the registered list, the
name badge list, the change lists against a badge issue log, the check-in sheet
and the speed networking draw, all for one event at a time.

WHY ITS OWN MODULE RATHER THAN "bookings"
Gating it on bookings would have been the smaller change, and wrong twice over.
The audience is whoever runs the badge table and the check-in desk, which is not
the audience for the booking pipeline; and the bookings grant carries
create/update/delete over every delegate row in the CRM, so one module for both
would mean the desk could not be handed to anybody without also handing them
that. It is also left OUT of SCOPED_MODULES: bookings is row-scoped so a sales
executive sees their own bookings, and a badge list showing one person's own
bookings would be useless, because whoever works the desk works the whole event.

A team with no TeamPermission row for a module is already treated as no-access
by crm_permission() and by my-permissions, so this backfill is not needed for
safety. It IS needed for the permissions grid: the page renders one row per
module and reads its initial state from the stored rows, so without these the new
row would render unticked and then save back a full set anyway. Materialising
them all-False keeps what the admin sees and what the database holds identical.
This is exactly the reasoning of 0027 and 0031, and the shape is copied from
0031.

NOBODY GAINS ACCESS HERE. Every row is all-False. All-access teams and the HP
account are unaffected either way: their matrix is generated from CRM_MODULES at
request time, so they pick the module up automatically.
"""
from django.db import migrations

NEW_MODULES = ["pre_event_docs"]


def add_modules(apps, schema_editor):
    Team = apps.get_model("teams", "Team")
    TeamPermission = apps.get_model("teams", "TeamPermission")

    for team in Team.objects.all():
        for module in NEW_MODULES:
            TeamPermission.objects.get_or_create(
                team=team,
                module=module,
                defaults={
                    "can_view": False,
                    "can_create": False,
                    "can_update": False,
                    "can_delete": False,
                },
            )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0031_add_mining_matrix_module"),
        ("teams", "0003_team_permissions"),
    ]

    operations = [
        # Reverse is a deliberate no-op rather than a delete, following 0020,
        # 0027 and 0031. The rows this creates are all-False and `module` has no
        # referential constraint, so leaving them behind after an unapply is
        # inert.
        migrations.RunPython(add_modules, migrations.RunPython.noop),
    ]
