"""
Rewrites every stored booking_code to its canonical spelling, on both invoices
and delegates, so production self-corrects at deploy.

WHY IT LIVES IN book_event AND TOUCHES book_delegate TOO
The two columns hold the same vocabulary and got out of step for the same
reason, so splitting the fix across two migrations would let a database sit
half-corrected between them. run_before/dependencies make the ordering explicit:
book_delegate.0013 is a dependency, so its booking_code column exists by the
time this runs.

REVERSIBLE, HONESTLY
The reverse is a genuine no-op, not a lie about one. "delegate" and "Delegate"
are the same value; restoring the lowercase spelling would recreate the defect
and there is nothing else this migration destroyed. Rolling back past it leaves
canonical data, which every reader — including the pre-migration code — handles.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    from book_event.booking_code_repair import repair_all
    repair_all((apps.get_model("book_event", "BookEvent"),
                apps.get_model("book_delegate", "BookDelegate")), apply=True)


class Migration(migrations.Migration):

    dependencies = [
        ("book_event", "0022_perf_indexes"),
        ("book_delegate", "0013_booked_on"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
