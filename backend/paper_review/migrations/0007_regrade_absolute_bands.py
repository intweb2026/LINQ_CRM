"""
Recompute every stored grade under the absolute score bands.

WHY A DATA MIGRATION AND NOT "IT WILL FIX ITSELF ON THE NEXT SAVE"
PaperReview.save() derives grade from proposal_score, so a row rescored after the
band change picks up the new letter on its own. Nothing rescores 3,492 historical
rows, though, and until something did the table would show two grading systems
side by side: an untouched row graded on percentages next to a new row graded on
ranges, with no way to tell which rule produced which letter. The grade is a
filter dropdown and a sort column, so that is a reporting fault, not a cosmetic
one.

THE BANDS ARE WRITTEN OUT HERE RATHER THAN IMPORTED FROM models.GRADE_BANDS.
A migration is a historical record of one change and has to keep producing the
same result after the model moves on again; importing the live table would make
this file silently mean whatever the bands say years from now.

    A  36-45      B+  31-35      B  26-30
    C  21-25      D   11-20      E   0-10

REVERSE IS A NO-OP, deliberately. grade holds nothing that is not derivable from
proposal_score, so reverting means reverting GRADE_BANDS in models.py; running
this migration forward again then reproduces the old letters exactly. Storing the
previous letters to restore them would be keeping a backup of a computed column.
"""
from django.db import migrations

# (floor, ceiling, letter) — contiguous and exhaustive over 0-45.
BANDS = (
    (36, 45, "A"),
    (31, 35, "B+"),
    (26, 30, "B"),
    (21, 25, "C"),
    (11, 20, "D"),
    (0,  10, "E"),
)


def regrade(apps, schema_editor):
    PaperReview = apps.get_model("paper_review", "PaperReview")

    for floor, ceiling, letter in BANDS:
        PaperReview.objects.filter(
            proposal_score__gte=floor, proposal_score__lte=ceiling,
        ).exclude(grade=letter).update(grade=letter)

    # An unscored review has no grade. Imported rows carried a letter with no
    # score behind it, and save() would blank it; nothing had ever called save().
    PaperReview.objects.filter(proposal_score__isnull=True).exclude(
        grade="").update(grade="")


class Migration(migrations.Migration):

    dependencies = [
        ("paper_review", "0006_perf_indexes"),
    ]

    operations = [
        migrations.RunPython(regrade, migrations.RunPython.noop),
    ]
