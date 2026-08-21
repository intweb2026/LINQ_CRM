"""
Drop report_definitions, report_rows and report_sync_logs.

These three tables existed for the Reports page: report_rows held what its Report
Data tab previewed, report_sync_logs what its Sync Logs tab listed, and
report_definitions the saved reports it never grew a UI for. With the page gone
nothing writes or reads any of them — the importer and sync orchestrator that
filled them are deleted too.

DESTRUCTIVE. The tables are dropped, not emptied. Checked before writing this:
0 definitions, 0 rows, 1 sync log on the dev database, and report_sheet_sources —
the one table this app keeps, behind the Google Sync page's "Add sheet source" —
is untouched. Confirm the same on production before applying, because a sheet
that was actually being synced there would have rows here.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('reports', '0002_alter_googlesheetsource_options_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='reportrow',
            name='source',
        ),
        migrations.RemoveField(
            model_name='reportsynclog',
            name='source',
        ),
        migrations.DeleteModel(
            name='ReportDefinition',
        ),
        migrations.DeleteModel(
            name='ReportRow',
        ),
        migrations.DeleteModel(
            name='ReportSyncLog',
        ),
    ]
