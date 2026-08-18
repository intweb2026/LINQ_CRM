"""
sync/crm_mirror.py
───────────────────
Mirror the whole CRM into one spreadsheet ("CRM data"), one tab per module.

Shape: raw table per tab. A tab either mirrors every concrete field on its
model, in which case new model fields appear in the sheet automatically with no
column list to maintain, or it mirrors an explicit list of fields in an explicit
order. Which of the two is decided per module in CRM_MODULES below. Foreign keys
are written as their raw id column (e.g. `company_id`) so rows can be joined in
the sheet with VLOOKUP, and their header says ID for that reason.

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

# Sentinel for "every concrete field on the model, in definition order". Named
# rather than left as a bare None so a line in CRM_MODULES says which of the two
# kinds of tab it is without anyone having to come back up here to check.
ALL_COLUMNS = None

# (tab name, "app_label.ModelName", columns).
#
# `columns` is ALL_COLUMNS, or a list of field names in the order they should
# appear in the sheet. A foreign key may be named either way, "company" or
# "company_id"; the sheet gets the raw id column in both cases. An unknown name
# fails that tab loudly rather than being skipped, because a dropped column and
# an empty column are indistinguishable once they are in a spreadsheet.
#
# Only real database columns can be named. Model properties cannot, since the
# mirror reads concrete fields; BookDelegate.full_name is a property, so a tab
# wanting it selects "first_name", "last_name" instead.
#
# To narrow a tab, put a list where ALL_COLUMNS is, e.g.
#
#     ("Delegates", "book_delegate.BookDelegate",
#         ["id", "first_name", "last_name", "email", "company",
#          "delegate_payment_status"]),
#
# To stop mirroring a module altogether, comment its line out. Both kinds of
# change apply everywhere, the nightly cron included, because every entry point
# reads this table. Order controls tab creation order.
CRM_MODULES = [
    ("Events",               "events.Event",                            ALL_COLUMNS),
    ("Invoices",             "book_event.BookEvent",                    ALL_COLUMNS),
    ("Delegates",            "book_delegate.BookDelegate",              ALL_COLUMNS),
    ("Companies",            "companies.Company",                       ALL_COLUMNS),
    ("Tickets",              "ticket_central.Ticket",                   ALL_COLUMNS),
    ("PaperReviews",         "paper_review.PaperReview",                ALL_COLUMNS),
    ("ProposalSubmissions",  "proposal_submission.ProposalSubmission",  ALL_COLUMNS),
    ("Users",                "accounts.User",                           ALL_COLUMNS),
    ("Teams",                "teams.Team",                              ALL_COLUMNS),
]

# Rows pulled from the DB per batch. Independent of the Sheets append chunk size.
_QUERY_CHUNK = 2000


def _get_model(path):
    from django.apps import apps
    app_label, model_name = path.split(".")
    return apps.get_model(app_label, model_name)


def _available_fields(model, path):
    """Every field this module is permitted to mirror, in model definition order."""
    excluded = _EXCLUDED_FIELDS.get(path, set())
    return [
        f for f in model._meta.fields
        if f.name not in excluded and f.attname not in excluded
    ]


def _fields_for(model, path, columns=ALL_COLUMNS):
    """
    Concrete columns to mirror.

    With ALL_COLUMNS, every permitted field in model definition order. With an
    explicit list, only those fields and in the order given, so a tab's column
    order is the order it is written in CRM_MODULES.

    Raises ValueError on a name that is unknown or excluded. Failing here is
    deliberate; mirror_all() records the failure against that one tab and leaves
    the rest running, whereas a silently dropped column would ship a sheet that
    looks complete and is not.
    """
    available = _available_fields(model, path)

    if columns is ALL_COLUMNS:
        return available

    excluded = _EXCLUDED_FIELDS.get(path, set())
    by_name = {}
    for f in available:
        by_name[f.name] = f
        by_name[f.attname] = f

    selected, seen, unknown, blocked = [], set(), [], []
    for name in columns:
        if name in excluded:
            blocked.append(name)
        elif name not in by_name:
            unknown.append(name)
        elif by_name[name].attname not in seen:
            # A name repeated in the list, or given once as `company` and once as
            # `company_id`, is one column; it must not widen the header row.
            seen.add(by_name[name].attname)
            selected.append(by_name[name])

    if blocked:
        raise ValueError(
            f"{path}: {', '.join(blocked)} cannot be selected, it is excluded "
            f"from the mirror."
        )
    if unknown:
        raise ValueError(
            f"{path}: no such field {', '.join(unknown)}. "
            f"Available: {', '.join(f.name for f in available)}"
        )
    if not selected:
        raise ValueError(
            f"{path}: the column list is empty. Use ALL_COLUMNS to mirror every field."
        )
    return selected


def _titleise(text):
    """
    Title-case a label without flattening anything already capitalised.

    Django's default verbose_name is the field name lowercased with underscores
    swapped for spaces, so "full name" needs capitalising. An explicitly set one
    may already carry an acronym, and .title() would turn "URL" into "Url".
    """
    words = []
    for w in text.split():
        if w.lower() == "id":
            # capitalize() would give "Id", which reads as a typo in a header row
            # sitting next to the primary key's own "ID".
            words.append("ID")
        elif w == w.lower():
            words.append(w.capitalize())
        else:
            words.append(w)
    return " ".join(words)


def _header_for(field):
    """
    The sheet header for one field.

    Relation fields are written as their raw id column, so the header says ID.
    "Company" over a column of integers reads as a company name, which is a trap
    for whoever opens the sheet.
    """
    label = _titleise(str(field.verbose_name).strip()) or field.attname
    if field.is_relation and not label.lower().endswith("id"):
        label = f"{label} ID"
    return label


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


def mirror_module(service, tab_name, model_path, columns=ALL_COLUMNS):
    """
    Full-replace one tab. Returns the number of data rows written.
    Raises whatever the Sheets API raises; the caller decides how to log it.
    """
    model = _get_model(model_path)
    fields = _fields_for(model, model_path, columns)
    headers = [_header_for(f) for f in fields]

    count = service.replace_data_chunked(tab_name, headers, _rows(model, fields))
    logger.info("CRM mirror: %s → %d rows, %d cols", tab_name, count, len(fields))
    return count


def _normalise(entry):
    """
    Accept (tab, model_path) as well as (tab, model_path, columns).

    The two-element form predates column selection and means every column, which
    keeps any caller passing its own module list working unchanged.
    """
    tab, model_path, *rest = entry
    return tab, model_path, rest[0] if rest else ALL_COLUMNS


def mirror_all(modules=None):
    """
    Mirror every configured module into the CRM spreadsheet.

    Each tab is independent: one failing tab is recorded and the rest still run,
    so a single bad module cannot leave the whole sheet stale. Returns
    (summary, errors) where summary is {tab: row_count}.
    """
    from services.google_sheets import GoogleSheetsService

    modules = [_normalise(m) for m in (modules or CRM_MODULES)]

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

    service.ensure_tabs([tab for tab, _, _ in modules])

    summary, errors = {}, []
    for tab_name, model_path, columns in modules:
        try:
            summary[tab_name] = mirror_module(service, tab_name, model_path, columns)
        except Exception as exc:
            errors.append(f"{tab_name}: {exc}")
            logger.error("CRM mirror failed for %s: %s", tab_name, exc, exc_info=True)

    return summary, errors
