"""
sync/crm_mirror.py
───────────────────
Mirror the whole CRM into one spreadsheet ("CRM data"), one tab per module.

Shape: raw table per tab. Columns are introspected from each model's concrete
fields, so a tab reflects the database table as-is and new model fields appear
in the sheet automatically — no per-module column list to maintain. Foreign keys
are written as their raw id column (e.g. `company_id`) so rows can be joined in
the sheet with VLOOKUP.

Every run is a full replace of every tab. That is deliberate: a mirror has no
incremental story, which is what makes it safe. (sync/bookings_sync.py filters
to changed rows and then calls replace_data() — clearing the tab and writing
only that subset. This module cannot do that, because it never filters.)

Not mirrored: webhook logs, sync logs, OTP tokens, action logs, API keys. Those
are operational data, not CRM records, and webhook logs alone would dominate the
spreadsheet's cell budget.
"""
import datetime
import decimal
import json
import logging

from django.conf import settings

logger = logging.getLogger("book_event")

# Fields never written to the sheet, per model. Credentials and hashes must not
# leave the database.
_EXCLUDED_FIELDS = {
    "accounts.User": {"password"},
}

# Tab name → "app_label.ModelName". Order controls tab creation order.
CRM_MODULES = [
    ("Events",               "events.Event"),
    ("Invoices",             "book_event.BookEvent"),
    ("Delegates",            "book_delegate.BookDelegate"),
    ("Companies",            "companies.Company"),
    ("Tickets",              "ticket_central.Ticket"),
    ("PaperReviews",         "paper_review.PaperReview"),
    ("ProposalSubmissions",  "proposal_submission.ProposalSubmission"),
    ("Users",                "accounts.User"),
    ("Teams",                "teams.Team"),
]

# Rows pulled from the DB per batch. Independent of the Sheets append chunk size.
_QUERY_CHUNK = 2000


def _get_model(path):
    from django.apps import apps
    app_label, model_name = path.split(".")
    return apps.get_model(app_label, model_name)


def _fields_for(model, path):
    """Concrete columns to mirror, in model definition order."""
    excluded = _EXCLUDED_FIELDS.get(path, set())
    return [f for f in model._meta.fields if f.name not in excluded]


def _coerce(value):
    """
    Render a Python value as something Sheets will store faithfully.

    Everything is written with valueInputOption=RAW, so strings are stored
    verbatim rather than being re-parsed as formulas or reformatted dates.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, datetime.datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value)


def _rows(model, fields):
    """Stream every row of `model` as a list of cell values."""
    attnames = [f.attname for f in fields]
    qs = model.objects.all().order_by("pk")
    for obj in qs.iterator(chunk_size=_QUERY_CHUNK):
        yield [_coerce(getattr(obj, a, None)) for a in attnames]


def mirror_module(service, tab_name, model_path):
    """
    Full-replace one tab. Returns the number of data rows written.
    Raises whatever the Sheets API raises; the caller decides how to log it.
    """
    model = _get_model(model_path)
    fields = _fields_for(model, model_path)
    headers = [f.attname for f in fields]

    count = service.replace_data_chunked(tab_name, headers, _rows(model, fields))
    logger.info("CRM mirror: %s → %d rows", tab_name, count)
    return count


def mirror_all(modules=None):
    """
    Mirror every configured module into the CRM spreadsheet.

    Each tab is independent: one failing tab is recorded and the rest still run,
    so a single bad module cannot leave the whole sheet stale. Returns
    (summary, errors) where summary is {tab: row_count}.
    """
    from services.google_sheets import GoogleSheetsService

    modules = modules or CRM_MODULES

    # Deliberately no fallback to GOOGLE_SHEET_ID. This mirror creates a tab
    # named "Events", and so does sync/events_sync.py with entirely different
    # columns — pointing both at one spreadsheet would have them overwrite each
    # other every run. The CRM sheet must be named explicitly.
    sheet_id = getattr(settings, "GOOGLE_SHEET_CRM_ID", "")
    if not sheet_id:
        raise RuntimeError(
            "GOOGLE_SHEET_CRM_ID is not set. Create the 'CRM data' spreadsheet, "
            "share it with the service account as an Editor, and set its ID in "
            "backend/.env. It must not be the same sheet as GOOGLE_SHEET_ID."
        )

    service = GoogleSheetsService(spreadsheet_id=sheet_id)

    service.ensure_tabs([tab for tab, _ in modules])

    summary, errors = {}, []
    for tab_name, model_path in modules:
        try:
            summary[tab_name] = mirror_module(service, tab_name, model_path)
        except Exception as exc:
            errors.append(f"{tab_name}: {exc}")
            logger.error("CRM mirror failed for %s: %s", tab_name, exc, exc_info=True)

    return summary, errors
