"""
Register the "credit_control" module on every existing team.

Credit Control chases unpaid invoices by phone. Every invoice whose payment
status is Pending, minus the editions the Performance Matrix verdict has
withdrawn and minus SpEx, becomes a lead on somebody's queue, ages from its
invoice date, and moves from the team lead to an exec on day 4 unless a case is
already in flight with the lead.

WHY ITS OWN MODULE RATHER THAN "bookings"
The audience is the four people who make those calls, which is not the audience
for the booking pipeline, and the bookings grant carries create/update/delete
over every delegate row in the CRM. One module for both would mean the calling
queue could not be handed to anybody without also handing them that.

UNLIKE pre_event_docs, THIS ONE IS IN SCOPED_MODULES. A queue is personal: an
exec should open the page and see the leads assigned to them, while the team
lead and anyone else granted the `all` cell sees the whole team's. That single
grid cell is the whole of the row-level security, and it replaces the sheet full
of per-range protections the predecessor build needed to stop one caller editing
another's rows.

A team with no TeamPermission row for a module is already treated as no-access
by crm_permission() and by my-permissions, so this backfill is not needed for
safety. It IS needed for the permissions grid: the page renders one row per
module and reads its initial state from the stored rows, so without these the
new row would render unticked and then save back a full set anyway.
Materialising them all-False keeps what the admin sees and what the database
holds identical. Same reasoning and shape as 0027, 0031 and 0032.

NOBODY GAINS ACCESS HERE. Every row is all-False, `can_all` included, so the
module is invisible until it is granted deliberately. All-access teams and the
HP account are unaffected either way: their matrix is generated from CRM_MODULES
at request time, so they pick the module up automatically.
"""
from django.db import migrations

NEW_MODULES = ["credit_control"]


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
                    "can_all": False,
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
