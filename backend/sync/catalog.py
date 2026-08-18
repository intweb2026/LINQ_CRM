"""
sync/catalog.py
────────────────
What a user is allowed to push to a spreadsheet, and how those rows are built.

This is the backing store for the module and column pickers on the Google Sync
page. A picker that offered anything this file does not describe would let
someone save a target that fails only when it next runs, so the catalogue is the
single source of truth for both halves: the frontend reads it to draw the
choices, and the runner reads it to build the rows.

Two kinds of module live here.

Raw modules are one database table each, exactly the shape sync/crm_mirror.py
mirrors, and they reuse its field selection so the two cannot drift apart.

Composed modules are a join presented as one row, which is the shape a person
means by "bookings" — an invoice and the delegate on it, together, because
"delegate email" and "payment status" are not columns of the same table. There
is one, and it reuses sync/bookings_sync.py's own row builder so a column called
Delegate Name here holds precisely what the nightly bookings push puts under
that heading.
"""
import re

from sync.crm_mirror import (
    _available_fields,
    _coerce,
    _fields_for,
    _get_model,
    _header_for,
    _rows,
)

# Invoices pulled per batch when composing booking rows. Matches the batch size
# sync/bookings_sync.py uses for the same query.
_BOOKING_BATCH = 500


class CatalogError(ValueError):
    """A module or column that the catalogue does not describe."""


def _slug(label):
    """
    Stable key for a human column heading.

    "Pre-Tax Amount" becomes "pre_tax_amount", so a saved target survives a
    heading being reworded, and reads as a field name in the API payload.
    """
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


# ── Raw modules ───────────────────────────────────────────────────────────────
# key → (label, "app_label.ModelName", description)

_RAW_MODULES = {
    "events":               ("Events",               "events.Event",
                             "The event catalogue."),
    "invoices":             ("Invoices",             "book_event.BookEvent",
                             "Invoice records only, without their delegates."),
    "delegates":            ("Delegates",            "book_delegate.BookDelegate",
                             "Delegate records only, without their invoice."),
    "companies":            ("Companies",            "companies.Company",
                             "The company directory."),
    "tickets":              ("Tickets",              "ticket_central.Ticket",
                             "Ticket Central records."),
    "paper_reviews":        ("Paper Reviews",        "paper_review.PaperReview",
                             "Paper review submissions."),
    "proposal_submissions": ("Proposal Submissions", "proposal_submission.ProposalSubmission",
                             "Proposal submissions."),
    "users":                ("Users",                "accounts.User",
                             "CRM users. Password hashes are never available here."),
    "teams":                ("Teams",                "teams.Team",
                             "Teams and their leads."),
}


def _raw_columns(path):
    model = _get_model(path)
    return [
        {"key": f.name, "label": _header_for(f)}
        for f in _available_fields(model, path)
    ]


def _raw_rows(path, column_keys):
    model = _get_model(path)
    fields = _fields_for(model, path, column_keys)
    headers = [_header_for(f) for f in fields]
    return headers, _rows(model, fields)


# ── Bookings, invoice joined to delegate ──────────────────────────────────────

def _booking_columns():
    from sync.bookings_sync import BOOKINGS_HEADERS

    return [{"key": _slug(h), "label": h} for h in BOOKINGS_HEADERS]


def _booking_rows(column_keys):
    """
    One row per delegate, or one row per invoice where an invoice has none.

    Every column is computed and then the selected ones are picked out, rather
    than each column being computed on its own. That keeps a single definition
    of what "Delegate Company" means, shared with the bookings push, and the
    cost of the columns nobody asked for is nothing next to one Sheets request.
    """
    from book_event.models import BookEvent
    from sync.bookings_sync import BOOKINGS_HEADERS, _row

    index_of = {_slug(h): i for i, h in enumerate(BOOKINGS_HEADERS)}
    picked = [index_of[k] for k in column_keys]
    headers = [BOOKINGS_HEADERS[i] for i in picked]

    def rows():
        query = BookEvent.objects.all().order_by("id")
        total = query.count()
        for start in range(0, total, _BOOKING_BATCH):
            batch = (
                query[start:start + _BOOKING_BATCH]
                .select_related("sales_executive", "team_leader")
                .prefetch_related("delegates", "delegates__company")
            )
            for inv in batch:
                delegates = list(inv.delegates.all())
                for delegate in (delegates or [None]):
                    full = _row(inv, delegate)
                    yield [_coerce(full[i]) for i in picked]

    return headers, rows()


_COMPOSED_MODULES = {
    "bookings": (
        "Bookings",
        "One row per delegate, with their invoice, company and payment detail "
        "joined onto it. This is the module to pick for delegate name or email.",
        _booking_columns,
        _booking_rows,
    ),
}


# ── Public surface ────────────────────────────────────────────────────────────

def list_modules():
    """
    Every module a target may be pointed at, with its selectable columns.

    Composed modules come first because they are the ones a person is usually
    after; a raw table is the specialist choice, not the default.
    """
    out = []
    for key, (label, description, columns_fn, _) in _COMPOSED_MODULES.items():
        out.append({
            "key": key,
            "label": label,
            "description": description,
            "columns": columns_fn(),
        })
    for key, (label, path, description) in _RAW_MODULES.items():
        out.append({
            "key": key,
            "label": label,
            "description": description,
            "columns": _raw_columns(path),
        })
    return out


def module_keys():
    return set(_COMPOSED_MODULES) | set(_RAW_MODULES)


def columns_for(module_key):
    """The selectable columns of one module. Raises CatalogError if unknown."""
    if module_key in _COMPOSED_MODULES:
        return _COMPOSED_MODULES[module_key][2]()
    if module_key in _RAW_MODULES:
        return _raw_columns(_RAW_MODULES[module_key][1])
    raise CatalogError(
        f"Unknown module '{module_key}'. "
        f"Available: {', '.join(sorted(module_keys()))}"
    )


def validate(module_key, column_keys):
    """
    Check a saved selection before it is stored or run.

    Raises CatalogError naming what is wrong. Called by the serializer so a bad
    target is rejected at the form rather than at 05:30 the next morning.
    """
    available = {c["key"] for c in columns_for(module_key)}

    if not column_keys:
        raise CatalogError("Select at least one column.")

    unknown = [k for k in column_keys if k not in available]
    if unknown:
        raise CatalogError(
            f"{module_key} has no column {', '.join(unknown)}. "
            f"Available: {', '.join(sorted(available))}"
        )


def build_rows(module_key, column_keys):
    """
    Return (headers, row iterator) for one selection.

    Column order follows the order the keys are given, so a target's columns
    land in the sheet in the order they were picked. Rows stream; nothing here
    holds a whole table in memory.
    """
    validate(module_key, column_keys)

    # De-duplicate while keeping order. A repeated key would otherwise widen the
    # header row past the rows underneath it.
    seen, keys = set(), []
    for k in column_keys:
        if k not in seen:
            seen.add(k)
            keys.append(k)

    if module_key in _COMPOSED_MODULES:
        return _COMPOSED_MODULES[module_key][3](keys)
    return _raw_rows(_RAW_MODULES[module_key][1], keys)
