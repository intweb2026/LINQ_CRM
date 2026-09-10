"""
Register the "attendance" module on every existing team.

QR Attendance is the on-site door: a phone camera reads a badge, the server
checks the person against the Pre-Event Docs check-in sheet, and one arrival row
is written.

WHY ITS OWN MODULE RATHER THAN "pre_event_docs"
Gating it on Pre-Event Docs would have been the smaller change and wrong for the
same reason gating Pre-Event Docs on "bookings" would have been. The audience is
whoever is standing at a turnstile with a phone, and they need exactly this;
the Pre-Event Docs grant carries the badge issue log and the speed networking
draw with it, so one module for both would mean the door could not be handed to
anybody without also handing them those.

The module's own two cells then carry the rest of the role model: `view` reads
the arrival log, `create` records an arrival. That is what lets a supervisor
watch the door without being able to work it, and it is why there is no third
kind of account here.

A team with no TeamPermission row for a module is already treated as no-access
by crm_permission() and by my-permissions, so this backfill is not needed for
safety. It IS needed for the permissions grid: the page renders one row per
module and reads its initial state from the stored rows, so without these the
new row would render unticked and then save back a full set anyway.
Materialising them all-False keeps what the admin sees and what the database
holds identical. Same reasoning and same shape as 0027, 0031 and 0032.

NOBODY GAINS ACCESS HERE. Every row is all-False. All-access teams and the HP
account are unaffected either way: their matrix is generated from CRM_MODULES at
request time, so they pick the module up automatically.
"""
from django.db import migrations

NEW_MODULES = ["attendance"]


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
        ("accounts", "0032_add_pre_event_docs_module"),
        ("teams", "0003_team_permissions"),
    ]

    operations = [
        # Reverse is a deliberate no-op rather than a delete, following 0020,
        # 0027, 0031 and 0032. The rows this creates are all-False and `module`
        # has no referential constraint, so leaving them behind after an unapply
        # is inert.
        migrations.RunPython(add_modules, migrations.RunPython.noop),
    ]
