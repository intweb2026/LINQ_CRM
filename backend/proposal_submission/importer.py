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
    absolute_url, as_bool, as_int, as_text, as_url, build_header_mapper, clean_header,
    column_errors, excel_serial_to_date, normalise_row, parse_import_date,
    plain_text_cell, plan_hash, public_plan, summarise, unwrap_anchor,
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
    "DERIVED_HEADERS", "NON_TEXT_FIELDS",
    "REQUIRED", "URL_FIELDS", "MAX_URL_LEN",
    "excel_serial_to_date", "parse_import_date", "map_headers",
    "normalise_row", "file_has_mr_content", "classify_rows",
    "plan_hash", "summarise", "public_plan",
    "unwrap_anchor", "absolute_url", "as_url", "plain_text_cell",
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

    # ── The agenda tracker's own columns ─────────────────────────────────────
    # New stored fields.
    #
    # THE BARE SUB-HEADERS "approached?" AND "topic" WERE HERE AND WERE REMOVED.
    # They rested on an unconfirmed guess that the sheet carries a two-row header
    # with a merged Panel group. "Topic" in particular is a plausible header for a
    # presentation topic in any pasted file, and mapping it here filed that column
    # into panel_topic silently, with no unrecognised-column warning to notice. An
    # unmapped header is REPORTED; a wrongly mapped one is not, so a guess of this
    # shape has to earn its place and this one could not. Re-add them only against
    # a real sheet that needs them.
    "panel approached?":          "panel_approached",
    "panel topic":                "panel_topic",
    "panel status":               "panel_status",
    # The sheet spells it "Re-Offerred"; both spellings are accepted rather than
    # making a correct header the one that fails.
    "speaker slot re-offerred":   "speaker_slot_reoffered",
    "speaker slot re-offered":    "speaker_slot_reoffered",
    # As above, "Assesment" is the sheet's spelling.
    "risk assesment (live)":      "risk_assessment_live",
    "risk assessment (live)":     "risk_assessment_live",

    # ── Tracker spellings of columns that already existed ────────────────────
    # The tracker names several of these differently from the Zoho export. Both
    # labels reach the same column, so a paste from either source lands.
    # clean_header() collapses newlines, so the two-line headers in the sheet
    # arrive here as one space-separated string.
    "full name":                                        "speaker_name",
    "sponsorship":                                      "sponsorship_status",
    # TWO different columns, confirmed by the business: the MRE recommends a slot
    # on the paper review, and the agenda team assigns one. "Agenda Slot" above
    # stays pointed at the recommendation, because that is what the 1,877 rows
    # already under that header hold, so an older export re-imports unchanged.
    "speaking slot assignment":                         "speaking_slot_assignment",
    "slot recommendation by mre":                       "agenda_slot",
    # The CHECKBOX, and NOT agenda_addition. Mapping this to the prose column was
    # the wrong guess: a tick in the sheet would have been stored as the words
    # "TRUE" inside the session outline, and the checkbox would have stayed off
    # on every imported row.
    "added to agenda":                                  "added_to_agenda",
    "slot recommendation from mr":                      "slot_recommendation_mr",
    # The label the CSV export now writes for this column. Without this entry the
    # export stopped re-importing, which tests_extras.py asserts as an invariant
    # and duly caught. All three spellings reach the same column, so an export
    # from any version of the app still lands.
    "internal slot note (mr)":                          "slot_recommendation_mr",
    "sales pitch factor (low score = more commercial)": "sales_pitch_factor",
}

# Tracker columns that are DERIVED, so a paste of the whole sheet must neither
# store them nor report them as mistakes. Read from the event catalogue and the
# bookings pipeline instead — see
# ProposalSubmissionViewSet._annotate_tracker_context.
#
# THEY ARE DISCARDED SILENTLY, which is the one place this module departs from
# build_header_mapper's "report, never silently drop" rule, and it is deliberate:
# these are not mistyped headers, they are columns whose value has another owner.
# The consequence to know about is that a sheet whose Event Date disagrees with
# the catalogue loses its version on import, and the grid then shows the
# catalogue's. That is the intended direction; the catalogue is the source of
# truth for an event's date and status, and Bookings for a booking.
#
# Production Executive and SPEX Manager belong here for the same reason as the
# rest: they are the event's AGENDA team and SPEX team, maintained on the event
# in the catalogue, so a value pasted against one proposal row is a copy of
# somebody else's column.
DERIVED_HEADERS = frozenset([
    "event date", "event status", "production executive", "spex manager",
    "booking date", "payment date", "booking status by se",
])

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
    "spex_remarks": "SpEx Remarks",
    "agenda_slot": "Slot Recommendation by MRE",
    "speaking_slot_assignment": "Speaking Slot Assignment",
    "revenue_possibility": "Revenue Possibility",
    "internal_footnotes_mr": "Internal Footnotes (MR)",
    # NOT "Slot Recommendation by MR". agenda_slot exports as "Slot Recommendation
    # by MRE", and two adjacent CSV headers differing by a single letter, pointing
    # at two unrelated columns, is a trap for anyone editing the file by hand. The
    # importer still accepts the old spelling, so an existing export re-imports
    # unchanged.
    "slot_recommendation_mr": "Internal Slot Note (MR)",
    "agenda_addition": "Agenda Addition",
    "panel_approached": "Panel Approached?",
    "panel_topic": "Panel Topic",
    "panel_status": "Panel Status",
    "speaker_slot_reoffered": "Speaker Slot Re-Offered",
    "risk_assessment_live": "Risk Assessment (Live)",
    "added_to_agenda": "Added to Agenda",
}

# Columns that are NOT text and are therefore coerced individually rather than
# through the text_fields pass in classify_rows. Named once because that pass and
# the payload build both have to skip exactly this set, and they sat as two
# hand-kept copies of the same tuple.
NON_TEXT_FIELDS = (
    "submission_date", "qc_score", "linkedin_followers", "added_to_agenda",
)

MR_COLUMNS = ("slot_recommendation_mr", "internal_footnotes_mr")

REQUIRED = ("event_code", "speaker_name", "email")
# The columns that hold a LINK rather than text, so classify_rows knows which
# ones to run through as_url — an anchor-wrapped cell collapses to the address
# inside it, and a scheme-less one gains https://, so what gets stored is
# something a browser can navigate to. Every other column takes plain_text_cell
# instead, which unwraps the same markup but keeps the words.
#
# Over-length is NOT checked here: column_errors() reads the same 500 off
# URLField.max_length, and it runs after the unwrapping, so a long address is
# judged as the address rather than as address-plus-tags.
URL_FIELDS = ("linkedin_speaker", "linkedin_company")
MAX_URL_LEN = 500

# Classifications, the Excel-serial window, the date-format cascade and the
# text/int coercers now live in accounts/import_common.py, shared with
# paper_review/importer.py — see this module's docstring. They are imported at
# the top and re-exported via __all__, so nothing that reads them from here
# changes.
_map_headers = build_header_mapper(ZOHO_HEADERS, MODEL_FIELDS)


def map_headers(columns):
    """
    The shared mapper, with the derived tracker columns dropped from the
    unrecognised list rather than reported — see DERIVED_HEADERS.

    Wrapped here instead of teaching build_header_mapper a third argument:
    paper_review uses the same builder and has no derived columns, so the
    behaviour belongs to this app.
    """
    mapping, unrecognised = _map_headers(columns)
    return mapping, [c for c in unrecognised
                     if clean_header(c) not in DERIVED_HEADERS]

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
            if f not in NON_TEXT_FIELDS
        }

        # added_to_agenda is a checkbox in the tracker, so the cell holds
        # something like TRUE/Yes/1 rather than prose. as_bool is the shared
        # coercer paper_review/importer.py already uses for its own `nos`
        # checkbox; a blank cell is False, and anything unrecognisable is a row
        # ERROR rather than a silent False, because "we could not read this" and
        # "this speaker is not on the agenda" are different answers.
        added, added_error = as_bool(values.get("added_to_agenda"))
        if added_error:
            errors.append({
                "field": FIELD_TO_LABEL["added_to_agenda"],
                "problem": added_error,
                "value": _as_text(values.get("added_to_agenda")),
            })

        # ── cells that arrived as an anchor tag ───────────────────────────────
        # Zoho writes several columns as HTML, so a LinkedIn cell can arrive as
        # `<a href="https://…">Eli Jasso</a>` and an email column as a mailto:
        # anchor. Stored as-is that markup is not a link and not a readable
        # value; unwrapped here, the URL columns hold a navigable address and the
        # text columns hold words. See accounts/import_common.py for the rules
        # and for why only http/https survive.
        #
        # BEFORE everything below, not after. The required-field check would
        # otherwise pass on a cell holding nothing but an empty `<a>`, and
        # column_errors would measure a 40-character URL as the 120 characters of
        # tags around it.
        for field in text_fields:
            if field in URL_FIELDS:
                url, url_error = as_url(values.get(field))
                text_fields[field] = url
                if url_error:
                    raw = _as_text(values.get(field))
                    errors.append({
                        "field": FIELD_TO_LABEL[field], "problem": url_error,
                        "value": raw[:80] + "…" if len(raw) > 80 else raw,
                    })
            else:
                text_fields[field] = plain_text_cell(text_fields[field])

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
                       if f not in NON_TEXT_FIELDS}
            payload["event_code"] = resolved_code
            payload["submission_date"] = parsed_date.isoformat() if parsed_date else None
            payload["qc_score"] = numbers.get("qc_score")
            payload["linkedin_followers"] = numbers.get("linkedin_followers")
            payload["added_to_agenda"] = added
            entry["_payload"] = payload

        plan.append(entry)

    return plan
