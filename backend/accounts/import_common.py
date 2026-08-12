"""
accounts/import_common.py
──────────────────────────
Shared plumbing for the two-phase JSON-row importers.

WHY THIS FILE EXISTS
proposal_submission/importer.py was written first and paper_review/importer.py
needs the same date parsing, the same numeric/text coercion, the same header
cleaning and the same plan-hash/summary shape. Two ways to get that were
available and both were wrong:

  * COPY it into paper_review — the dirty-date fix (whitespace hugging a hyphen
    in "20 - Dec - 2025") would then need making twice, and the second copy would
    drift the first time only one was touched.
  * IMPORT from proposal_submission — a lateral dependency between two sibling
    pipeline apps whose only intended relationship is the one-directional FK
    ProposalSubmission.source_paper_review → PaperReview.

So the genuinely generic half is extracted here, exactly as
webhooks/event_code_normalization.py was extracted for spacing-tolerant event
codes. accounts/ is already this codebase's home for cross-module machinery —
filter_spec.py, bulk_update.py and ordering.py are all shared from here and used
by four or more apps.

WHAT STAYS IN EACH APP'S OWN importer.py
Everything model-specific: the Zoho header map, the reverse label map, which
columns are MR-restricted, which fields are required, and classify_rows() itself
(the classification rules differ — paper_review reconciles a stored score against
its six criteria, proposal_submission has no such rule).

accounts/bulk_update.py is NOT touched by this file; the two solve different
problems and share nothing.
"""
import hashlib
import json
import re
from datetime import date, datetime, timedelta

# Per-call row cap. The browser chunks anything larger, because
# DATA_UPLOAD_MAX_MEMORY_SIZE sits at Django's 2.5 MB default and an uncapped
# paste fails opaquely rather than with a message naming the limit.
MAX_ROWS = 500

# Row classifications, shared so the frontend's three-way pill rendering is the
# same component for every importer.
CREATE = "CREATE"
CREATE_WITH_WARNING = "CREATE_WITH_WARNING"
ERROR = "ERROR"

# ── Excel serial dates ───────────────────────────────────────────────────────
# Excel's 1900 epoch, with its phantom 29-Feb-1900. Serial 1 = 1900-01-01;
# serials 1-59 are offset from 1899-12-31, serial 60 IS the phantom date, and
# from 61 on the extra day means the base becomes 1899-12-30.
_SERIAL_BASE_EARLY = date(1899, 12, 31)
_SERIAL_BASE_LATE  = date(1899, 12, 30)
# A bare integer in a date column is ambiguous: 2026 is both a plausible year and
# a valid serial (→ 1905-07-18). Rather than silently produce a 1905 date, only
# serials inside a sane window are accepted and anything else becomes a row
# ERROR quoting the raw value. 25569 = 1970-01-01, 73415 ≈ 2100-12-31.
_SERIAL_MIN, _SERIAL_MAX = 25569, 73415

# Order mirrors webhooks/services.py: parse_webhook_date — dd/mm before mm/dd,
# because these exports are UK/IN-formatted.
_DATE_FORMATS = (
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y", "%d-%B-%Y",
    "%d %b %Y", "%d %B %Y", "%B %d, %Y", "%b %d, %Y", "%d.%m.%Y",
)

# Collapses whitespace hugging a hyphen ("20 - Dec - 2025" -> "20-Dec-2025",
# "21-February -2026" -> "21-February-2026") so the dd-Mon-yyyy / dd-Month-yyyy
# formats above still match. See parse_import_date.
_HYPHEN_SPACING = re.compile(r"\s*-\s*")


def excel_serial_to_date(serial):
    """None when the serial is outside the plausible window or is the phantom."""
    n = int(serial)
    if n == 60 or n < _SERIAL_MIN or n > _SERIAL_MAX:
        return None
    base = _SERIAL_BASE_EARLY if n <= 59 else _SERIAL_BASE_LATE
    return base + timedelta(days=n)


def parse_import_date(raw):
    """
    Returns (date|None, error|None). A blank cell is (None, None) — these columns
    are nullable and an import must NOT apply any create-path default; a row that
    arrived undated stays undated.

    THE THREE PARSERS, AND WHY THIS ONE IS THE AUTHORITY
    (matching note in frontend/src/lib/importParse.js)

      frontend/src/lib/importParse.js  reads .xlsx with SheetJS `raw: false`, so
                                       cells arrive as DISPLAYED TEXT and a
                                       serial never reaches the server that way
      THIS function                    accepts both — serials (bounded, phantom
                                       day 60 rejected) and strings — and ERRORS
                                       on anything it cannot read
      _parse_date in events/views.py
        and book_event/views.py        six string formats, no serial support, and
                                       returns None on failure, so a column of
                                       unreadable dates is indistinguishable from
                                       a column of blanks

    Accepting both representations is what lets `load_zoho_export` read a workbook
    SERVER-side via openpyxl — where serials do arrive raw — without the browser
    having to normalise them first. Anything reading import files should call this.
    """
    if raw is None:
        return None, None
    # datetime before date: datetime is a date subclass.
    if isinstance(raw, datetime):
        return raw.date(), None
    if isinstance(raw, date):
        return raw, None
    # bool is an int subclass; reject before the numeric branch accepts it.
    if isinstance(raw, bool):
        return None, f"{raw!r} is not a date"
    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and not raw.is_integer():
            # Excel fractional serials carry a time; the date part is what we want.
            resolved = excel_serial_to_date(int(raw))
        else:
            resolved = excel_serial_to_date(raw)
        if resolved is None:
            return None, f"{raw!r} is not a recognisable date or Excel serial"
        return resolved, None

    text = str(raw).strip()
    if not text:
        return None, None
    # numpy/pandas datetime64 stringifies as "2026-08-10T00:00:00.000000000"
    if "T" in text:
        text = text.split("T", 1)[0]
    if " " in text and len(text) > 10 and text[4:5] == "-":
        text = text.split(" ", 1)[0]
    # Dirty spreadsheet exports from this same Zoho instance carry stray
    # whitespace around the hyphen in dd-Mon-yyyy style dates — "20 - Dec - 2025",
    # "21-February -2026" — which no entry in _DATE_FORMATS matches as typed.
    # Collapsed here so "%d-%b-%Y" / "%d-%B-%Y" still match. A no-op on every
    # other format this function accepts: ISO ("2026-08-10") and slash dates
    # ("10/08/2026") carry no whitespace around their own separators, so this can
    # only ever help the hyphenated formats, never change one that already parses.
    text = _HYPHEN_SPACING.sub("-", text)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date(), None
        except ValueError:
            continue
    if text.isdigit():
        resolved = excel_serial_to_date(int(text))
        if resolved is not None:
            return resolved, None
    return None, f"{raw!r} is not a recognisable date"


# ── Editions ─────────────────────────────────────────────────────────────────
# An edition is a YEAR. The window is deliberately wide enough to be obviously
# safe (no real edition falls outside it) and narrow enough to catch the actual
# failure: an Excel serial. Serial 45678 is 2025-01-15 as a date, but read as an
# integer it is a plausible-looking 45678th edition, and `edition` is an
# IntegerField so nothing raises — it just stores and is silently wrong forever.
EDITION_MIN, EDITION_MAX = 2000, 2100


def parse_edition(raw):
    """
    Returns (int|None, error|None). Blank is (None, None) — edition is nullable
    and an absent edition is not an error.

    Two-digit years are expanded ("26" -> 2026) to match the convention
    BookEvent.save() already uses when it strips a year off an event code.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, None
    if isinstance(raw, bool):
        return None, f"{raw!r} is not an edition year"

    value, error = as_int(raw)
    if error:
        return None, f"{raw!r} is not an edition year"
    if value is None:
        return None, None

    if 0 <= value <= 99:
        value = 2000 + value

    if not (EDITION_MIN <= value <= EDITION_MAX):
        return None, (f"{raw!r} is not a plausible edition year "
                      f"(expected {EDITION_MIN}-{EDITION_MAX}) — a value this far "
                      f"out is usually an Excel serial date")
    return value, None


def clean_header(name):
    """Trimmed, whitespace-collapsed, lower-cased — the header lookup key."""
    return " ".join(str(name or "").strip().split()).lower()


def build_header_mapper(zoho_headers, model_fields):
    """
    Returns a map_headers(columns) -> (mapping, unrecognised) bound to one app's
    header table.

    Unrecognised columns are REPORTED, never silently dropped — a mistyped header
    would otherwise present as an entire column of empty data.
    """
    def map_headers(columns):
        mapping, unrecognised = {}, []
        for col in columns:
            key = clean_header(col)
            if key in zoho_headers:
                mapping[col] = zoho_headers[key]
            elif key.replace(" ", "_") in model_fields:
                mapping[col] = key.replace(" ", "_")
            else:
                unrecognised.append(col)
        return mapping, unrecognised
    return map_headers


def as_text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def as_int(value):
    """Returns (int|None, error|None)."""
    if value is None:
        return None, None
    if isinstance(value, bool):
        return None, f"{value!r} is not a whole number"
    if isinstance(value, int):
        return value, None
    if isinstance(value, float):
        if not value.is_integer():
            return None, f"{value!r} is not a whole number"
        return int(value), None
    text = str(value).strip().replace(",", "")
    if not text:
        return None, None
    try:
        return int(text), None
    except ValueError:
        try:
            f = float(text)
        except ValueError:
            return None, f"{value!r} is not a whole number"
        if not f.is_integer():
            return None, f"{value!r} is not a whole number"
        return int(f), None


def as_bool(value):
    """
    Returns (bool, error|None). Spreadsheet booleans arrive as "Yes"/"No",
    "TRUE"/"FALSE", 1/0 or a real bool depending on which tool wrote the file.

    A blank cell is False rather than an error: these columns are
    BooleanField(default=False), so "absent" and "not set" are the same state and
    refusing the row would be inventing a requirement the form does not have.
    """
    if value is None or value == "":
        return False, None
    if isinstance(value, bool):
        return value, None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value), None
    text = str(value).strip().lower()
    if text in ("yes", "y", "true", "t", "1"):
        return True, None
    if text in ("no", "n", "false", "f", "0", "-"):
        return False, None
    return False, f"{value!r} is not a yes/no value"


def normalise_row(raw_row, mapping):
    """Map one inbound dict onto model field names."""
    out = {}
    for column, field in mapping.items():
        if column not in raw_row:
            continue
        out[field] = raw_row[column]
    return out


# ── Reading an actual file, server-side ──────────────────────────────────────
# The two-phase importers are fed JSON rows by the browser and never see a file.
# A management command that wants to run a REAL spreadsheet through the same
# classification needs to produce that same list-of-dicts shape itself, which is
# what this does.
#
# WHAT THIS SEES THAT THE BROWSER DOES NOT
# frontend/src/lib/importParse.js reads .xlsx with SheetJS `raw: false`, so every
# cell arrives as DISPLAYED TEXT and an Excel serial never reaches the server by
# that route. openpyxl with data_only=True returns the TYPED value: a real
# datetime where Excel stored a date, an int where it stored a serial. Both are
# handled by parse_import_date above, which is why routing every date through it
# is what makes a server-side read safe. The consequence worth stating plainly:
# reading a workbook here and uploading the same workbook through the browser are
# NOT identical operations — this path sees more of the truth.
#
# book_event/management/commands/load_zoho_export.py carries an equivalent private
# reader (_read_xlsx / _read_csv / _read_json). It is deliberately left alone
# rather than repointed here: it is covered by 32 passing tests and rewiring it is
# a refactor nobody asked for. This is the shared home for the next caller.
def read_import_rows(path):
    """
    Read .xlsx/.xlsm/.csv/.json into the list-of-dicts the importers expect.

    Keys are the file's own header labels, verbatim and untrimmed — header
    cleaning is map_headers' job, and trimming here would hide the fact that a
    label like "Closeness to Topic (10) " carries a trailing space.

    Rows that are entirely blank are skipped: a spreadsheet with formatting
    applied below the data yields hundreds of all-None tuples, and each one would
    otherwise become an ERROR row for three missing required fields.
    """
    lowered = str(path).lower()
    if lowered.endswith(".json"):
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []
    if lowered.endswith((".xlsx", ".xlsm")):
        return _read_xlsx_rows(path)
    return _read_csv_rows(path)


def _read_csv_rows(path):
    import csv
    # utf-8-sig: Excel writes a BOM, and without this the first header becomes
    # "﻿Event Code" and reports as an unrecognised column.
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _read_xlsx_rows(path):
    # data_only=True so a formula cell yields its cached VALUE rather than the
    # formula text. read_only=True to stream rather than materialise the sheet.
    from openpyxl import load_workbook

    book = load_workbook(path, data_only=True, read_only=True)
    try:
        sheet = book[book.sheetnames[0]]
        rows = sheet.iter_rows(values_only=True)
        try:
            header = [as_text(h) for h in next(rows)]
        except StopIteration:
            return []
        out = []
        for values in rows:
            if values is None or all(v is None or as_text(v) == "" for v in values):
                continue
            out.append({
                header[i]: values[i]
                for i in range(min(len(header), len(values)))
                if header[i] != ""
            })
        return out
    finally:
        book.close()


def plan_hash(plan):
    """
    Fingerprint of exactly what a commit would write: the normalised payload of
    every importable row, in row order, each including its RESOLVED event_code.

    Shape follows accounts/bulk_update.py:152 (sha256 over sorted, tight JSON) so
    the two behave identically for the caller, but computed here — that method's
    signature describes a single-field mass update and cannot express a row set.

    ERROR rows are excluded on purpose: they are never written, so a change to one
    must not invalidate an otherwise-current plan.
    """
    digest_input = [
        {"row": e["row"], "payload": e["_payload"]}
        for e in plan if e["classification"] != ERROR
    ]
    payload = json.dumps(digest_input, sort_keys=True, separators=(",", ":"),
                         default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def summarise(plan):
    counts = {CREATE: 0, CREATE_WITH_WARNING: 0, ERROR: 0}
    for entry in plan:
        counts[entry["classification"]] += 1
    return counts


def public_plan(plan):
    """Strip the internal payload before returning the plan to the client."""
    return [{k: v for k, v in entry.items() if k != "_payload"} for entry in plan]
