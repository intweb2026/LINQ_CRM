"""
reports/services/worksheets.py
───────────────────────────────
WorksheetInspector: read a spreadsheet's tab names and column headers live.

Was ReportSyncOrchestrator in sync.py, which also ran the importer over every
active source and wrote a ReportSyncLog per run. Those paths went with the
Reports page — nothing triggers a sync and nothing reads the rows — so what is
left are the two read-only lookups the "Add sheet source" form calls: list the
tabs in a pasted spreadsheet URL, and detect the headers on a stored source.
Fully independent of the existing google_sync app.
"""
import logging

from reports.models import GoogleSheetSource
from .connector import GoogleSheetsConnector, ConnectorError

logger = logging.getLogger(__name__)


class WorksheetInspector:

    @classmethod
    def detect_columns(cls, source: GoogleSheetSource) -> dict:
        """
        Detect column headers and row count from a source's Google Sheet.
        Returns {"columns": [...], "sample_count": int} or {"error": str}.
        """
        try:
            connector = GoogleSheetsConnector()
            rows = connector.read_worksheet(
                sheet_id=GoogleSheetSource.extract_sheet_id(
                    source.sheet_url or source.sheet_id
                ),
                worksheet_name=source.worksheet_name,
            )
        except ConnectorError as exc:
            return {"error": str(exc)}

        if not rows:
            return {"columns": [], "sample_count": 0}

        headers = [str(h).strip() for h in rows[0]]
        return {
            "columns":      headers,
            "sample_count": max(0, len(rows) - 1),
        }

    @classmethod
    def list_worksheets(cls, sheet_id_or_url: str) -> dict:
        """
        Return the worksheet/tab names for a given spreadsheet.
        Returns {"worksheets": [...]} or {"error": str}.
        """
        try:
            connector = GoogleSheetsConnector()
            sheet_id  = GoogleSheetSource.extract_sheet_id(sheet_id_or_url)
            worksheets = connector.list_worksheets(sheet_id)
            return {"worksheets": worksheets}
        except ConnectorError as exc:
            return {"error": str(exc)}
