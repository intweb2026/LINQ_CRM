"""
proposal_submission/importer.py
────────────────────────────────
Header mapping, value normalisation, per-row classification and the plan hash for
the two-phase JSON-row import.

No file ever reaches this module. The browser parses the .xlsx/.csv and posts
JSON rows, matching every other import in this backend (book_event/views.py:560,
events/views.py:291, ticket_central/views.py:309) — there is no multipart
endpoint and MEDIA_ROOT is unconfigured, so introducing one here would be the
only upload path in the project.

The generic plumbing — date parsing, numeric/text coercion, header cleaning, the
plan hash and the plan summary — lives in accounts/import_common.py, shared with
paper_review/importer.py. What stays here is everything model-specific: the Zoho
header map, the reverse label map, the MR columns, and classify_rows() itself.
The names this module used to define locally are re-exported below so callers and
tests that import them from here keep working unchanged.
"""
from accounts.import_common import (
    CREATE, CREATE_WITH_WARNING, ERROR, MAX_ROWS,
    as_int, as_text, build_header_mapper, clean_header, column_errors,
    excel_serial_to_date, normalise_row, parse_import_date, plan_hash,
    public_plan, summarise,
)
from webhooks.event_code_normalization import resolve_with_spacing_tolerance

from .access import has_full_visibility, permitted_event_codes
from .models import ProposalSubmission

# Re-exported for callers that already import these from this module (views.py,
# tests_extras.py, the frontend contract tests). Listed explicitly rather than
# left implicit so a reader can see what this module still promises.
__all__ = [
    "MAX_ROWS", "CREATE", "CREATE_WITH_WARNING", "ERROR",
    "ZOHO_HEADERS", "MODEL_FIELDS", "FIELD_TO_LABEL", "MR_COLUMNS",
    "REQUIRED", "URL_FIELDS", "MAX_URL_LEN",
    "excel_serial_to_date", "parse_import_date", "map_headers",
    "normalise_row", "file_has_mr_content", "classify_rows",
    "plan_hash", "summarise", "public_plan",
]

# ── Header mapping ───────────────────────────────────────────────────────────
# Zoho display label → model field. Three are not a slug of their label:
#   "Email Address" → email, and the two MR columns.
ZOHO_HEADERS = {
    "event code":                 "event_code",
    "submission date":            "submission_date",
    "participation type":         "participation_type",
    "speaker name":               "speaker_name",
    "email address":              "email",
    "company name":               "company_name",
    "qc grade":                   "qc_grade",
    "qc score":                   "qc_score",
    "sales pitch factor":         "sales_pitch_factor",
    "presentation theme":         "presentation_theme",
    "linkedin (speaker)":         "linkedin_speaker",
    "linkedin (company)":         "linkedin_company",
    "linkedin followers":         "linkedin_followers",
    "speaker slot status":        "speaker_slot_status",
    "sponsorship status":         "sponsorship_status",
    "spex remarks":               "spex_remarks",
    "agenda slot":                "agenda_slot",
    "revenue possibility":        "revenue_possibility",
    "internal footnotes (mr)":    "internal_footnotes_mr",
    "slot recommendation by mr":  "slot_recommendation_mr",
    "agenda addition":            "agenda_addition",
}

# Model field names are accepted verbatim too, so an export → import round trip
# works whichever header style the file carries.
MODEL_FIELDS = set(ZOHO_HEADERS.values())

# Reverse map for CSV export headers — the label a field is written out as.
FIELD_TO_LABEL = {
    "event_code": "Event Code", "submission_date": "Submission Date",
    "participation_type": "Participation Type", "speaker_name": "Speaker Name",
    "email": "Email Address", "company_name": "Company Name",
    "qc_grade": "QC Grade", "qc_score": "QC Score",
    "sales_pitch_factor": "Sales Pitch Factor",
    "presentation_theme": "Presentation Theme",
    "linkedin_speaker": "LinkedIn (Speaker)",
    "linkedin_company": "LinkedIn (Company)",
    "linkedin_followers": "LinkedIn Followers",
    "speaker_slot_status": "Speaker Slot Status",
    "sponsorship_status": "Sponsorship Status",
    "spex_remarks": "SpEx Remarks", "agenda_slot": "Agenda Slot",
    "revenue_possibility": "Revenue Possibility",
    "internal_footnotes_mr": "Internal Footnotes (MR)",
    "slot_recommendation_mr": "Slot Recommendation by MR",
    "agenda_addition": "Agenda Addition",
}

MR_COLUMNS = ("slot_recommendation_mr", "internal_footnotes_mr")

REQUIRED = ("event_code", "speaker_name", "email")
# Descriptive now rather than enforcing: over-length is checked generically
# against the model by column_errors() in classify_rows, which reads the same 500
# off URLField.max_length. Kept because they are in __all__ and name, in one
# place, which columns are URLs.
URL_FIELDS = ("linkedin_speaker", "linkedin_company")
MAX_URL_LEN = 500

# Classifications, the Excel-serial window, the date-format cascade and the
# text/int coercers now live in accounts/import_common.py, shared with
# paper_review/importer.py — see this module's docstring. They are imported at
# the top and re-exported via __all__, so nothing that reads them from here
# changes.
map_headers = build_header_mapper(ZOHO_HEADERS, MODEL_FIELDS)

# Local aliases for the two private coercer names this module's own code uses.
_as_text = as_text
_as_int = as_int
_clean_header = clean_header


def file_has_mr_content(rows, mapping):
    """Which MR columns carry actual content anywhere in the file."""
    inverse = {}
    for column, field in mapping.items():
        inverse.setdefault(field, []).append(column)
    offending = []
    for field in MR_COLUMNS:
        for column in inverse.get(field, []):
            if any(_as_text(r.get(column)) for r in rows):
                offending.append(FIELD_TO_LABEL[field])
                break
    return offending


def classify_rows(rows, mapping, user, existing_pairs):
    """
    Build the per-row plan. Writes nothing and never raises on bad data — every
    problem becomes an ERROR row so a 500-row paste returns one readable answer.

    `existing_pairs` is a set of (lower(email), event_code) already stored within
    the caller's scope, so the duplicate warning is scope-consistent.
    """
    bypass = has_full_visibility(user)
    allowed = None if bypass else set(permitted_event_codes(user))

    plan = []
    # (lower(email), code) seen earlier in THIS file — a file duplicating itself
    # must warn too, not just against what is already stored.
    seen_in_file = set()

    for index, raw_row in enumerate(rows):
        row_number = index + 1
        values = normalise_row(raw_row, mapping)
        errors = []

        text_fields = {
            f: _as_text(values.get(f))
            for f in MODEL_FIELDS
            if f not in ("submission_date", "qc_score", "linkedin_followers")
        }

        for field in REQUIRED:
            if not text_fields.get(field):
                errors.append({
                    "field": FIELD_TO_LABEL[field],
                    "problem": "required value is missing",
                    "value": "",
                })

        # ── event code ────────────────────────────────────────────────────────
        # C2 — routed through the shared spacing-tolerant layer rather than
        # resolve_event_code(raw_code, raw_code) directly, so 'AFS-JS' in a
        # spreadsheet resolves the same canonical spelling a paper review or
        # proposal form submission would, instead of becoming an ERROR row for a
        # spacing difference alone. The BIU / BIUK anchored-boundary guarantee is
        # unchanged — this only adds a tier that runs before it and can only ever
        # match a WHOLE catalogue entry; see
        # webhooks/event_code_normalization.py for the mechanism and
        # tests_event_codes.py in this app for the pinned BIU/BIUK assertions.
        resolved_code = ""
        raw_code = text_fields.get("event_code", "")
        if raw_code:
            resolution = resolve_with_spacing_tolerance(raw_code)
            matches = resolution.matches
            if len(matches) == 1:
                # BOOKINGS_OFF is a success here: proposals arrive for events
                # that are not selling tickets online.
                resolved_code = matches[0].event_code
                if allowed is not None and resolved_code not in allowed:
                    errors.append({
                        "field": "Event Code",
                        "problem": f"you are not assigned to '{resolved_code}'",
                        "value": raw_code,
                    })
            elif len(matches) > 1:
                errors.append({
                    "field": "Event Code",
                    "problem": "ambiguous — matched "
                               f"{sorted(e.event_code for e in matches)}",
                    "value": raw_code,
                })
            else:
                errors.append({
                    "field": "Event Code",
                    "problem": "no matching event; prefilter candidates "
                               f"{resolution.candidates}",
                    "value": raw_code,
                })

        # ── submission date ───────────────────────────────────────────────────
        parsed_date, date_error = parse_import_date(values.get("submission_date"))
        if date_error:
            errors.append({
                "field": "Submission Date", "problem": date_error,
                "value": _as_text(values.get("submission_date")),
            })

        # ── numerics ──────────────────────────────────────────────────────────
        numbers = {}
        for field in ("qc_score", "linkedin_followers"):
            value, num_error = _as_int(values.get(field))
            if num_error:
                errors.append({
                    "field": FIELD_TO_LABEL[field], "problem": num_error,
                    "value": _as_text(values.get(field)),
                })
            elif value is not None and value < 0:
                errors.append({
                    "field": FIELD_TO_LABEL[field],
                    "problem": "cannot be negative", "value": str(value),
                })
            else:
                numbers[field] = value

        # ── does every value FIT its column ───────────────────────────────────
        # Supersedes the hand-written URL_FIELDS check this used to carry. The two
        # LinkedIn columns are URLField(max_length=MAX_URL_LEN), so the generic
        # check reports them identically, and it also covers the fifteen other
        # columns the bespoke version left unguarded. Without this an overlong
        # value passes preview and dies as a DataError 500 mid-commit, taking the
        # whole chunk down with it; see accounts/import_common.column_errors.
        #
        # The RESOLVED code is checked, not the raw cell, because the payload
        # writes the resolved one; a long raw spelling that resolves is not a
        # problem.
        errors.extend(column_errors(
            ProposalSubmission,
            {**text_fields, "event_code": resolved_code, **numbers},
            FIELD_TO_LABEL,
        ))

        email_key = text_fields.get("email", "").lower()
        pair = (email_key, resolved_code)
        duplicate_of_stored = bool(email_key and resolved_code
                                   and pair in existing_pairs)
        duplicate_in_file = bool(email_key and resolved_code
                                 and pair in seen_in_file)

        if errors:
            classification = ERROR
        elif duplicate_of_stored or duplicate_in_file:
            classification = CREATE_WITH_WARNING
        else:
            classification = CREATE

        if not errors:
            seen_in_file.add(pair)

        entry = {
            "row": row_number,
            "classification": classification,
            "event_code": resolved_code,
            "speaker_name": text_fields.get("speaker_name", ""),
            "email": text_fields.get("email", ""),
            "errors": errors,
        }
        if duplicate_of_stored:
            entry["warning"] = (
                f"A proposal for {text_fields.get('email')} on "
                f"{resolved_code} already exists in your events."
            )
        elif duplicate_in_file:
            entry["warning"] = (
                f"This file already contains {text_fields.get('email')} on "
                f"{resolved_code}."
            )

        # The payload actually written on commit. Only present for importable
        # rows, so a stale-hash comparison never depends on ERROR-row content.
        if classification != ERROR:
            payload = {f: text_fields.get(f, "") for f in MODEL_FIELDS
                       if f not in ("submission_date", "qc_score",
                                    "linkedin_followers")}
            payload["event_code"] = resolved_code
            payload["submission_date"] = parsed_date.isoformat() if parsed_date else None
            payload["qc_score"] = numbers.get("qc_score")
            payload["linkedin_followers"] = numbers.get("linkedin_followers")
            entry["_payload"] = payload

        plan.append(entry)

    return plan
