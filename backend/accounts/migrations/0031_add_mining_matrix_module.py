"""
Register the "mining_matrix" module on every existing team.

The Mining Resource Matrix is a new page: one row per event, showing how much
Market Research output is still waiting on Data Mining, split by priority. It
reads Ticket Central and the Events catalogue and owns no table of its own.

WHY ITS OWN MODULE RATHER THAN "ticket_central"
Gating it on ticket_central would have been the smaller change, and wrong. The
matrix is a planning surface for whoever schedules mining capacity, which is not
the same audience as the ticket queue itself — and ticket_central's grant carries
create/update/delete over live tickets. One module for both would mean the
planning view could not be handed to anyone without also handing them the queue.

A team with no TeamPermission row for a module is already treated as no-access by
crm_permission() and by my-permissions, so this backfill is not needed for
safety. It IS needed for the permissions grid: the page renders one row per
module and reads its initial state from the stored rows, so without these the new
row would render unticked and then save back a full set anyway. Materialising
them all-False keeps what the admin sees and what the database holds identical.
This is exactly the reasoning of 0027, and the shape is copied from it.

NOBODY GAINS ACCESS HERE. Every row is all-False. All-access teams and the HP
account are unaffected either way: their matrix is generated from CRM_MODULES at
request time, so they pick the module up automatically.
"""
from django.db import migrations

NEW_MODULES = ["mining_matrix"]


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
        ("accounts", "0030_user_managed_team"),
        ("teams", "0003_team_permissions"),
    ]

    operations = [
        # Reverse is a deliberate no-op rather than a delete, following 0020 and
        # 0027. The rows this creates are all-False and `module` has no
        # referential constraint, so leaving them behind after an unapply is
        # inert — an unrecognised module key surfaces as a no-access entry and
        # nothing reads it.
        migrations.RunPython(add_modules, migrations.RunPython.noop),
    ]
