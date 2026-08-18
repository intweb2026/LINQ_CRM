import logging
import os
from django.conf import settings
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger('book_event')


def extract_spreadsheet_id(url_or_id):
    """
    Pull the spreadsheet id out of whatever a person pasted.

    A full sheet URL, a URL with a ?usp= or #gid= tail, or a bare id all work,
    because the id is what a user has least reason to be able to find on their
    own and the URL is what their address bar hands them.
    """
    raw = (url_or_id or "").strip()
    if "/" not in raw:
        return raw.split("?")[0].split("#")[0]

    parts = [p for p in raw.split("/") if p]
    candidate = parts[parts.index("d") + 1] if "d" in parts else parts[0]
    return candidate.split("?")[0].split("#")[0]

class GoogleSheetsService:
    def __init__(self, spreadsheet_id=None):
        """
        spreadsheet_id defaults to settings.GOOGLE_SHEET_ID so the module-level
        singleton below keeps its original behaviour. The CRM mirror passes its
        own sheet ID (settings.GOOGLE_SHEET_CRM_ID) instead.
        """
        if not os.path.exists(settings.GOOGLE_SHEETS_CREDENTIALS):
            raise FileNotFoundError(f"Credentials not found at {settings.GOOGLE_SHEETS_CREDENTIALS}")

        self.creds = Credentials.from_service_account_file(
            settings.GOOGLE_SHEETS_CREDENTIALS,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )

        self.spreadsheet_id = extract_spreadsheet_id(
            spreadsheet_id or settings.GOOGLE_SHEET_ID
        )

        self.service = build("sheets", "v4", credentials=self.creds)

    def get_sheet_data(self, sheet_name):
        """Fetch all data from a sheet."""
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f"{sheet_name}!A:Z"
            ).execute()
            return result.get('values', [])
        except Exception as e:
            logger.error(f"Error fetching sheet data for {sheet_name}: {e}")
            return []

    def clear_sheet(self, sheet_name):
        """
        Wipe every value in the tab.

        The range is the tab name alone rather than "A:Z", because a bounded
        range clears only the columns it names. Six of the nine mirrored modules
        are wider than 26 columns, so a tab written wide and then written narrow
        would keep stale values, and stale headers, everywhere past column Z.

        Failure re-raises. The caller is always about to write a replacement over
        the top, and doing that into a tab that was not cleared produces a sheet
        of new rows followed by leftover old ones, which is worse than no run at
        all because nothing about it looks wrong.
        """
        try:
            self.service.spreadsheets().values().clear(
                spreadsheetId=self.spreadsheet_id,
                range=sheet_name,
            ).execute()
        except Exception as e:
            logger.error(f"Error clearing sheet {sheet_name}: {e}")
            raise

    def replace_data(self, sheet_name, headers, rows):
        """Wipe and refill the entire sheet."""
        self.clear_sheet(sheet_name)
        body = {'values': [headers] + rows}
        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{sheet_name}!A1",
            valueInputOption="RAW",
            body=body
        ).execute()
        return len(rows)

    def sync_data(self, sheet_name, headers, rows, id_index=0):
        """
        Update strategy:
        1. Ensure headers exist.
        2. Map existing rows by ID.
        3. Update existing or append new.
        """
        existing_data = self.get_sheet_data(sheet_name)
        
        if not existing_data:
            # Sheet is empty or doesn't exist, start with headers + all rows
            body = {'values': [headers] + rows}
            self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"{sheet_name}!A1",
                valueInputOption="RAW",
                body=body
            ).execute()
            return len(rows)

        # Map existing rows by ID (skipping header)
        existing_ids = {}
        for idx, row in enumerate(existing_data[1:], start=2): # 1-indexed, skipping header
            if len(row) > id_index:
                row_id = str(row[id_index])
                existing_ids[row_id] = idx

        updates = []
        new_rows = []

        for row in rows:
            row_id = str(row[id_index])
            if row_id in existing_ids:
                # Update existing row
                row_num = existing_ids[row_id]
                updates.append({
                    'range': f"{sheet_name}!A{row_num}",
                    'values': [row]
                })
            else:
                new_rows.append(row)

        # Execute batch updates
        if updates:
            body = {
                'valueInputOption': 'RAW',
                'data': updates
            }
            self.service.spreadsheets().values().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body=body
            ).execute()

        # Append new rows
        if new_rows:
            body = {'values': new_rows}
            self.service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"{sheet_name}!A1",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body=body
            ).execute()

        return len(rows)

    # ── Tab management ────────────────────────────────────────────────────────
    # replace_data()/sync_data() above write to "{tab}!A1", which fails if the
    # tab does not exist. The CRM mirror creates one tab per module, so it needs
    # to be able to add them to an otherwise-empty spreadsheet.

    def list_tabs(self):
        """Return the titles of every tab in the spreadsheet."""
        meta = self.service.spreadsheets().get(
            spreadsheetId=self.spreadsheet_id,
            fields="sheets.properties.title",
        ).execute()
        return [s["properties"]["title"] for s in meta.get("sheets", [])]

    def ensure_tabs(self, names):
        """Create any of `names` that don't exist yet. Returns the created names."""
        existing = set(self.list_tabs())
        missing = [n for n in names if n not in existing]
        if not missing:
            return []

        self.service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"requests": [
                {"addSheet": {"properties": {"title": name}}} for name in missing
            ]},
        ).execute()
        logger.info("Created Google Sheet tabs: %s", ", ".join(missing))
        return missing

    def replace_data_chunked(self, sheet_name, headers, row_iter, chunk_size=5000):
        """
        Wipe `sheet_name` and refill it from an iterable of rows.

        Unlike replace_data(), rows are streamed and appended in batches rather
        than sent in one request — a single update carrying tens of thousands of
        rows is too large a payload for the Sheets API. Returns the row count.
        """
        self.clear_sheet(sheet_name)

        # Header row first, so the appends below land from row 2 onwards.
        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{sheet_name}!A1",
            valueInputOption="RAW",
            body={"values": [headers]},
        ).execute()

        total = 0
        batch = []
        for row in row_iter:
            batch.append(row)
            if len(batch) >= chunk_size:
                self._append_rows(sheet_name, batch)
                total += len(batch)
                batch = []
        if batch:
            self._append_rows(sheet_name, batch)
            total += len(batch)

        return total

    def _append_rows(self, sheet_name, rows):
        self.service.spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id,
            range=f"{sheet_name}!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()


# Singleton instance
google_sheets = None
try:
    google_sheets = GoogleSheetsService()
except Exception as e:
    logger.error(f"Google Sheets Service initialization failed: {e}")
