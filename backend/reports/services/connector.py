"""
reports/services/connector.py
──────────────────────────────
GoogleSheetsConnector: scalable read-only connector for 40–50 spreadsheets.

Features:
- Multi-spreadsheet support (any sheet_id, not just the one in settings)
- Batch reads via batchGet API (one HTTP call for multiple worksheets)
- Exponential-backoff retry on transient errors
- Sheet ID extraction from full URLs
- Worksheet listing via spreadsheets.get metadata call
- Graceful degradation: raises clear exceptions the caller catches and logs
"""
import logging
import os
import time

from django.conf import settings

logger = logging.getLogger(__name__)

_MAX_RETRIES  = 3
_RETRY_BASE   = 1.5   # seconds — multiplied by attempt number
_DEFAULT_RANGE = "A:ZZ"


class ConnectorError(Exception):
    """Raised when the connector cannot initialise or a non-retriable error occurs."""


class GoogleSheetsConnector:
    """
    Initialises once and can read from any spreadsheet the service account
    has been granted access to.
    """

    def __init__(self):
        self._service = None
        self._init_service()

    def _init_service(self):
        creds_path = getattr(settings, "GOOGLE_SHEETS_CREDENTIALS", "")
        if not creds_path or not os.path.exists(creds_path):
            raise ConnectorError(
                "Google Sheets credentials file not found. "
                "Set GOOGLE_SHEETS_CREDENTIALS in your environment to the path of your "
                "service-account JSON key file."
            )
        try:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build

            creds = Credentials.from_service_account_file(
                creds_path,
                scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
            )
            self._service = build("sheets", "v4", credentials=creds, cache_discovery=False)
            logger.debug("GoogleSheetsConnector: service initialised")
        except Exception as exc:
            raise ConnectorError(f"Failed to initialise Google Sheets client: {exc}") from exc

    # ── Public API ─────────────────────────────────────────────────────────────

    @staticmethod
    def extract_sheet_id(url_or_id: str) -> str:
        """
        Accept either a raw spreadsheet ID or a full Google Sheets URL and
        return just the spreadsheet ID.

        Handles formats like:
          https://docs.google.com/spreadsheets/d/<ID>/edit#gid=0
          https://docs.google.com/spreadsheets/d/<ID>/
          <ID> (raw)
        """
        if "/" not in url_or_id:
            return url_or_id.strip()
        parts = url_or_id.split("/")
        for i, part in enumerate(parts):
            if part == "d" and i + 1 < len(parts):
                candidate = parts[i + 1].split("?")[0].split("#")[0]
                if candidate:
                    return candidate
        return url_or_id.strip()

    def list_worksheets(self, sheet_id: str) -> list[str]:
        """Return the names of all tabs/worksheets in a spreadsheet."""
        sheet_id = self.extract_sheet_id(sheet_id)
        meta = self._call_with_retry(
            self._service.spreadsheets().get,
            spreadsheetId=sheet_id,
            fields="sheets.properties.title",
        )
        return [s["properties"]["title"] for s in meta.get("sheets", [])]

    def read_worksheet(
        self,
        sheet_id: str,
        worksheet_name: str,
        range_notation: str = _DEFAULT_RANGE,
    ) -> list[list]:
        """
        Return all values from `worksheet_name` as a list-of-lists.
        Row 0 is the header row; subsequent rows are data.
        Empty trailing cells are omitted by the API (rows may be shorter than headers).
        """
        sheet_id = self.extract_sheet_id(sheet_id)
        result = self._call_with_retry(
            self._service.spreadsheets().values().get,
            spreadsheetId=sheet_id,
            range=f"'{worksheet_name}'!{range_notation}",
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING",
        )
        return result.get("values", [])

    def batch_read_worksheets(
        self,
        sheet_id: str,
        worksheet_names: list[str],
        range_notation: str = _DEFAULT_RANGE,
    ) -> dict[str, list[list]]:
        """
        Read multiple worksheets from the SAME spreadsheet in a single API call.
        Returns {worksheet_name: [[row], [row], ...]} mapping.
        """
        sheet_id = self.extract_sheet_id(sheet_id)
        ranges   = [f"'{ws}'!{range_notation}" for ws in worksheet_names]
        result   = self._call_with_retry(
            self._service.spreadsheets().values().batchGet,
            spreadsheetId=sheet_id,
            ranges=ranges,
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING",
        )
        output = {}
        for i, value_range in enumerate(result.get("valueRanges", [])):
            name = worksheet_names[i] if i < len(worksheet_names) else f"sheet_{i}"
            output[name] = value_range.get("values", [])
        return output

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _call_with_retry(self, callable_factory, **kwargs):
        """
        Execute an API call created by callable_factory(**kwargs).execute().
        Retries up to _MAX_RETRIES times on transient errors (429, 503, timeout).
        """
        last_exc = None
        for attempt in range(_MAX_RETRIES):
            try:
                return callable_factory(**kwargs).execute()
            except Exception as exc:
                last_exc = exc
                err_str = str(exc).lower()
                # Retry on rate-limit or server errors
                is_retriable = (
                    "429" in err_str
                    or "503" in err_str
                    or "quota" in err_str
                    or "timeout" in err_str
                    or "connection" in err_str
                )
                if is_retriable and attempt < _MAX_RETRIES - 1:
                    wait = _RETRY_BASE * (attempt + 1)
                    logger.warning(
                        "GoogleSheetsConnector: retriable error (attempt %d/%d), "
                        "retrying in %.1fs — %s",
                        attempt + 1, _MAX_RETRIES, wait, exc,
                    )
                    time.sleep(wait)
                else:
                    break
        raise ConnectorError(
            f"Google Sheets API call failed after {_MAX_RETRIES} attempts: {last_exc}"
        ) from last_exc
