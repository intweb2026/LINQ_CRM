"""
paper_review/importer.py
─────────────────────────
Header mapping, per-row classification and score reconciliation for the two-phase
JSON-row import.

No file ever reaches this module. The browser parses the .xlsx/.csv and posts JSON
rows, matching every other import in this backend — there is no multipart endpoint
and MEDIA_ROOT is unconfigured.

The generic plumbing (date parsing, coercion, header cleaning, plan hash, summary)
is shared with proposal_submission via accounts/import_common.py rather than
copied. Event-code resolution goes through webhooks/event_code_normalization.py,
the same spacing-tolerant layer the form uses, so 'AFS-JS' in a spreadsheet
resolves rather than becoming an ERROR row.

WHAT THIS IMPORTER DOES THAT proposal_submission's DOES NOT
Score reconciliation (B5). Legacy Zoho rows carry a stored "Proposal Score" that
may not equal the sum of their six criteria — the criteria were edited after the
score was written, or the score was typed by hand. The rule here is: RECOMPUTE
from the criteria, import the computed value, and classify the row
CREATE_WITH_WARNING naming both numbers. Never silently trust the file, never
silently overwrite it without saying so.

`grade` is also NOT reconciled, but for the opposite reason it used to be: it is
DERIVED (models.py computed_grade()), and the commit writes through obj.save()
like every other path, so the file's Grade column is overwritten by the value the
criteria imply. It is still mapped and still accepted — refusing the column would
break every existing export round-trip — but it decides nothing, and no warning is
raised for it. B+ and E therefore cannot enter the table through an import.

WHY IMPORTED ROWS MAY BE MORE INCOMPLETE THAN FORM ROWS
The serializer's REQUIRED_FIELDS marks all six criteria required; the MODEL keeps
them nullable. That split was deliberate — "so historical imports can land
incomplete rows" (serializers.py). This importer is that path, so it enforces only
event_code / speaker_name / email and lets a null criterion through, recomputing
proposal_score from whatever is present and leaving it null when nothing is.
"""
from accounts.import_common import (
    CREATE, CREATE_WITH_WARNING, ERROR, MAX_ROWS,
    as_bool, as_int, as_text, build_header_mapper, column_errors, normalise_row,
    parse_import_date, plan_hash, public_plan, summarise,
)
from webhooks.event_code_normalization import resolve_with_spacing_tolerance

from .access import has_full_visibility, permitted_event_codes
from .models import CRITERIA, CRITERIA_FIELDS, CRITERIA_MAX, PaperReview

__all__ = [
    "MAX_ROWS", "CREATE", "CREATE_WITH_WARNING", "ERROR",
    "ZOHO_HEADERS", "MODEL_FIELDS", "FIELD_TO_LABEL", "MR_COLUMNS", "REQUIRED",
    "map_headers", "file_has_mr_content", "classify_rows",
    "plan_hash", "summarise", "public_plan",
]

# ── Header mapping (B3) ──────────────────────────────────────────────────────
# Zoho display label (trimmed, whitespace-collapsed, lower-cased by
# clean_header) → model field. Several are not a slug of their label: the
# LinkedIn columns name the speaker explicitly, the six criteria carry their
# maximum in parentheses, and two carry punctuation ("Case Study, Results,
# Examples (5)" has commas; "Not an obvious 'Sales Pitch' (5)" has apostrophes)
# which is exactly why the CSV export in views.py must quote its headers.
ZOHO_HEADERS = {
    # Identification
    "event code":                            "event_code",
    "paper submission date":                 "paper_submission_date",
    "speaker email ref":                     "speaker_email_ref",
    "research email ref":                    "research_email_ref",
    # Speaker & company
    "speaker name":                          "speaker_name",
    "company name":                          "company_name",
    "email address of the speaker":          "email",
    "linkedin profile of speaker":           "linkedin_speaker",
    "linkedin followers count of speaker":   "linkedin_followers",
    "linkedin company profile":              "linkedin_company",
    "nos?":                                  "nos",
    # Rubric — the maximum in each label is part of the Zoho column name.
    "closeness to topic (10)":               "closeness_to_topic",
    "closeness to region (5)":               "closeness_to_region",
    "clear solution to challenges (10)":     "clear_solution_to_challenges",
    "case study, results, examples (5)":     "case_study_results_examples",
    "not an obvious 'sales pitch' (5)":      "not_obvious_sales_pitch",
    "company profile (10)":                  "company_profile_score",
    "proposal score":                        "proposal_score",
    "grade":                                 "grade",
    # Outcome
    "session or location on agenda":         "session_location_on_agenda",
    "internal footnotes":                    "internal_footnotes",
    "feedback to speaker or request information": "feedback_to_speaker",
    "proposal received":                     "proposal_received",
    "theme":                                 "theme",
    "agenda addition":                       "agenda_addition",
    # Audit columns Zoho exports. Accepted so a full export round-trips without
    # reporting them unrecognised; see AUDIT_COLUMNS for how they are treated.
    "added user":                            "created_by",
    "added time":                            "created_at",
}

# Model field names are accepted verbatim too, so an export → import round trip
# works whichever header style the file carries.
MODEL_FIELDS = set(ZOHO_HEADERS.values())

# Reverse map for CSV export headers — the label a field is written out as. Drives
# the export in views.py, so the two cannot drift: an export header that does not
# appear here as a key would not re-import.
FIELD_TO_LABEL = {
    "event_code": "Event Code",
    "paper_submission_date": "Paper Submission Date",
    "speaker_email_ref": "Speaker Email Ref",
    "research_email_ref": "Research Email Ref",
    "speaker_name": "Speaker Name",
    "company_name": "Company Name",
    "email": "Email Address of the Speaker",
    "linkedin_speaker": "LinkedIn Profile of Speaker",
    "linkedin_followers": "LinkedIn Followers Count of Speaker",
    "linkedin_company": "LinkedIn Company Profile",
    "nos": "NOS?",
    "closeness_to_topic": "Closeness to Topic (10)",
    "closeness_to_region": "Closeness to Region (5)",
    "clear_solution_to_challenges": "Clear Solution to Challenges (10)",
    "case_study_results_examples": "Case Study, Results, Examples (5)",
    "not_obvious_sales_pitch": "Not an obvious 'Sales Pitch' (5)",
    "company_profile_score": "Company Profile (10)",
    "proposal_score": "Proposal Score",
    "grade": "Grade",
    "session_location_on_agenda": "Session or Location on Agenda",
    "internal_footnotes": "Internal Footnotes",
    "feedback_to_speaker": "Feedback to Speaker or Request Information",
    "proposal_received": "Proposal Received",
    "theme": "Theme",
    "agenda_addition": "Agenda Addition",
}

# MR-restricted, mirroring serializers.py: _MR_ONLY_FIELDS. A file carrying
# content here is refused WHOLE for a non-permitted user (B7).
MR_COLUMNS = ("internal_footnotes",)

REQUIRED = ("event_code", "speaker_name", "email")
# Descriptive now rather than enforcing: over-length is checked generically
# against the model by column_errors() in classify_rows, which reads the same 500
# off URLField.max_length.
URL_FIELDS = ("linkedin_speaker", "linkedin_company")
MAX_URL_LEN = 500

# Recognised so they do not report as unrecognised columns, but NOT written.
#
# "Added User" is a Zoho display name with no reliable mapping to a User row —
# matching it by name is the exact failure mode Event.sales_team's icontains
# lookup already demonstrates (events/models.py:112), and mis-attributing
# authorship is worse than leaving it blank. "Added Time" is likewise dropped:
# created_at is the row's real creation instant in THIS system, and back-dating it
# to the Zoho timestamp would make "imported today" indistinguishable from
# "created in 2023" in every audit query. Both are surfaced in the preview as
# ignored rather than silently swallowed.
AUDIT_COLUMNS = ("created_by", "created_at")

# Text columns, i.e. everything that is not a date, an integer or a boolean.
_NON_TEXT = set(CRITERIA_FIELDS) | {
    "paper_submission_date", "linkedin_followers", "proposal_score", "nos",
    *AUDIT_COLUMNS,
}
TEXT_FIELDS = tuple(f for f in MODEL_FIELDS if f not in _NON_TEXT)

map_headers = build_header_mapper(ZOHO_HEADERS, MODEL_FIELDS)


def file_has_mr_content(rows, mapping):
    """Which MR columns carry actual content anywhere in the file."""
    inverse = {}
    for column, field in mapping.items():
        inverse.setdefault(field, []).append(column)
    offending = []
    for field in MR_COLUMNS:
        for column in inverse.get(field, []):
            if any(as_text(r.get(column)) for r in rows):
                offending.append(FIELD_TO_LABEL[field])
                break
    return offending


def computed_score(criteria_values):
    """
    B4 — the model-layer rule, applied to a dict of {criterion: int|None}.

    Sum of the filled criteria, or None when none is filled. Excluding nulls
    rather than zeroing them is the difference between "scored 0 on that
    criterion" and "not yet scored", and the two must not collapse into one
    number. Mirrors PaperReview.computed_score() deliberately — the model is
    still the authority at write time, this is only what the PREVIEW reports.
    """
    filled = [v for v in criteria_values.values() if v is not None]
    return sum(filled) if filled else None


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
        warnings = []

        text_fields = {f: as_text(values.get(f)) for f in TEXT_FIELDS}

        for field in REQUIRED:
            if not text_fields.get(field):
                errors.append({
                    "field": FIELD_TO_LABEL[field],
                    "problem": "required value is missing",
                    "value": "",
                })

        # ── event code ────────────────────────────────────────────────────────
        resolved_code = ""
        raw_code = text_fields.get("event_code", "")
        if raw_code:
            resolution = resolve_with_spacing_tolerance(raw_code)
            matches = resolution.matches
            if len(matches) == 1:
                # BOOKINGS_OFF is a success here: paper reviews arrive for events
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
        parsed_date, date_error = parse_import_date(
            values.get("paper_submission_date"))
        if date_error:
            errors.append({
                "field": "Paper Submission Date", "problem": date_error,
                "value": as_text(values.get("paper_submission_date")),
            })

        # ── the six criteria, each bounded by its own maximum ─────────────────
        criteria = {}
        for field, maximum in CRITERIA:
            value, num_error = as_int(values.get(field))
            if num_error:
                errors.append({
                    "field": FIELD_TO_LABEL[field], "problem": num_error,
                    "value": as_text(values.get(field)),
                })
                continue
            if value is not None and (value < 0 or value > maximum):
                errors.append({
                    "field": FIELD_TO_LABEL[field],
                    "problem": f"must be between 0 and {maximum}",
                    "value": as_text(values.get(field)),
                })
                continue
            criteria[field] = value

        # ── followers ─────────────────────────────────────────────────────────
        followers, followers_error = as_int(values.get("linkedin_followers"))
        if followers_error:
            errors.append({
                "field": FIELD_TO_LABEL["linkedin_followers"],
                "problem": followers_error,
                "value": as_text(values.get("linkedin_followers")),
            })
        elif followers is not None and followers < 0:
            errors.append({
                "field": FIELD_TO_LABEL["linkedin_followers"],
                "problem": "cannot be negative", "value": str(followers),
            })
            followers = None

        # ── NOS? ──────────────────────────────────────────────────────────────
        nos, nos_error = as_bool(values.get("nos"))
        if nos_error:
            errors.append({
                "field": FIELD_TO_LABEL["nos"], "problem": nos_error,
                "value": as_text(values.get("nos")),
            })

        # ── score reconciliation (B5) ─────────────────────────────────────────
        # Only meaningful once the criteria parsed cleanly; a criterion that
        # errored has already failed the row, and comparing against a partial sum
        # would report a second, misleading problem for the same cause.
        recomputed = computed_score(criteria) if len(criteria) == len(CRITERIA) else None
        stated_score, score_error = as_int(values.get("proposal_score"))
        if score_error:
            errors.append({
                "field": "Proposal Score", "problem": score_error,
                "value": as_text(values.get("proposal_score")),
            })
        elif (len(criteria) == len(CRITERIA) and stated_score is not None
              and stated_score != recomputed):
            warnings.append(
                f"Proposal Score in the file is {stated_score}; the six criteria "
                f"sum to {recomputed}. The computed value "
                f"({'none — no criteria scored' if recomputed is None else recomputed})"
                f" will be imported."
            )

        # ── does every value FIT its column ───────────────────────────────────
        # Supersedes the hand-written URL_FIELDS check this used to carry. The two
        # LinkedIn columns are URLField(max_length=MAX_URL_LEN), so the generic
        # check reports them identically, and it also covers every other column.
        # Without this an overlong value passes preview and dies as a DataError
        # 500 mid-commit, taking the whole chunk down with it; see
        # accounts/import_common.column_errors.
        #
        # The RESOLVED code is checked, not the raw cell, because the payload
        # writes the resolved one; a long raw spelling that resolves is not a
        # problem.
        errors.extend(column_errors(
            PaperReview,
            {**text_fields, "event_code": resolved_code,
             "linkedin_followers": followers, **criteria},
            FIELD_TO_LABEL,
        ))

        email_key = text_fields.get("email", "").lower()
        pair = (email_key, resolved_code)
        duplicate_of_stored = bool(email_key and resolved_code
                                   and pair in existing_pairs)
        duplicate_in_file = bool(email_key and resolved_code
                                 and pair in seen_in_file)

        if duplicate_of_stored:
            warnings.append(
                f"A paper review for {text_fields.get('email')} on "
                f"{resolved_code} already exists in your events."
            )
        elif duplicate_in_file:
            warnings.append(
                f"This file already contains {text_fields.get('email')} on "
                f"{resolved_code}."
            )

        if errors:
            classification = ERROR
        elif warnings:
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
        if warnings:
            # One string, so the frontend renders one warning cell per row
            # whether the cause was a duplicate, a score mismatch, or both.
            entry["warning"] = " ".join(warnings)

        # The payload actually written on commit. Only present for importable
        # rows, so a stale-hash comparison never depends on ERROR-row content.
        if classification != ERROR:
            payload = {f: text_fields.get(f, "") for f in TEXT_FIELDS}
            payload["event_code"] = resolved_code
            payload["paper_submission_date"] = (
                parsed_date.isoformat() if parsed_date else None)
            payload["linkedin_followers"] = followers
            payload["nos"] = nos
            payload.update(criteria)
            # proposal_score is deliberately ABSENT from the payload: the model's
            # save() recomputes it from the criteria on every write, so sending a
            # value here would be written and then immediately overwritten. B5's
            # "import the COMPUTED value" is therefore satisfied by the model
            # itself, and the preview reports what that will be.
            entry["_payload"] = payload

        plan.append(entry)

    return plan
