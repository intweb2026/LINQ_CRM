"""
Collapse anchor-wrapped LinkedIn cells to the address inside them.

WHAT WAS STORED
Every one of the 1,876 rows in this table held markup rather than a URL:

    <a href= "https://www.linkedin.com/in/x/" target = "_blank">https://…</a>

The edit form renders these columns with <input type="url">, so the whole tag sat
in the box as text, and the grid's ExtLink refused to linkify it because it is not
an address. The column is a URLField; the value was never a URL.

paper_reviews carries the same two columns and had ZERO affected rows, which is
what identifies this as a load-path defect in this table alone rather than
something about the source data. The importer already runs these columns through
as_url (proposal_submission/importer.py URL_FIELDS), so the rows predate that or
arrived around it.

WHY as_url AND NOT A REGEX HERE
accounts/import_common.py:as_url already owns this exact conversion, including
the quoting variations, the entity decoding and the "text that is not a link at
all passes through untouched" rule. A second implementation in a migration is how
the two start disagreeing about the same cell.

REVERSIBILITY
Deliberately one-way. The reverse would have to re-wrap a clean URL in markup,
which nothing wants and which could not reconstruct the original tag anyway
(target attributes, spacing, the visible text). Set as a no-op so a rollback of
neighbouring migrations is not blocked; the cleaned values simply stay clean.

THE WRITE SIDE is guarded separately, in serializers.py, so nothing puts markup
back. This migration fixes what is already stored; that guard stops the next one.
"""
from django.db import migrations

# The two link columns, matching importer.URL_FIELDS.
URL_FIELDS = ("linkedin_speaker", "linkedin_company")


def unwrap(apps, schema_editor):
    from accounts.import_common import as_url

    ProposalSubmission = apps.get_model("proposal_submission",
                                        "ProposalSubmission")

    # Only rows that actually carry a tag. `queryset.iterator()` plus
    # bulk_update in batches rather than a save() per row: this is 1,876 rows
    # today and the same command has to stay sane if it is ever re-run against a
    # larger table.
    dirty = ProposalSubmission.objects.filter(
        linkedin_speaker__contains="<a "
    ) | ProposalSubmission.objects.filter(
        linkedin_company__contains="<a "
    )

    batch = []
    for row in dirty.distinct().iterator(chunk_size=500):
        changed = False
        for field in URL_FIELDS:
            stored = getattr(row, field) or ""
            if "<a " not in stored:
                continue
            # as_url returns (value, error). An error here means the tag carried
            # no usable address; its second element is the cleaned text, which is
            # still strictly better than the raw markup, so it is written either
            # way rather than leaving one row as tags.
            cleaned, _error = as_url(stored)
            if cleaned != stored:
                setattr(row, field, cleaned)
                changed = True
        if changed:
            batch.append(row)
        if len(batch) >= 500:
            ProposalSubmission.objects.bulk_update(batch, URL_FIELDS)
            batch = []
    if batch:
        ProposalSubmission.objects.bulk_update(batch, URL_FIELDS)


class Migration(migrations.Migration):

    dependencies = [
        ("proposal_submission", "0007_proposalsubmission_added_to_agenda_and_more"),
    ]

    operations = [
        migrations.RunPython(unwrap, migrations.RunPython.noop),
    ]
