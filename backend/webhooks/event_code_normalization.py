"""
webhooks/event_code_normalization.py
──────────────────────────────────────
C2 — spacing-tolerant event-code resolution, extracted to a shared location so
paper_review and proposal_submission's importer can both use it without one
pipeline app importing from the other.

WHY IT LIVES HERE, NOT IN EITHER PIPELINE APP
This started as paper_review/event_codes.py, written for that one app. Wiring
proposal_submission/importer.py to the same normalisation by importing FROM
paper_review would make a lateral dependency between two sibling pipeline
modules that otherwise know nothing about each other (their only existing
relationship is the one-directional FK proposal_submission.source_paper_review →
paper_review.PaperReview, added deliberately and documented there). webhooks/ is
already this codebase's single home for event-code resolution — event_resolver.py
says so in its own module docstring — so a second file in the SAME app, next to
it rather than inside it, is the natural shared location. event_resolver.py
itself is NOT modified: this module only ADDS a tolerant tier that runs BEFORE
calling into it, exactly as paper_review/event_codes.py always did.

THE GAP THIS CLOSES
resolve_event_code takes (raw, normalized), but every existing caller passed the
raw code twice — proposal_submission/serializers.py and importer.py:284 (before
this file existed) both called resolve_event_code(code, code) — so the
normalised slot never carried anything. The result, measured:

    'AFS-JS'     -> no_match, candidates=[]
    'afs-js'     -> no_match, candidates=[]
    'AFS  -  JS' -> no_match, candidates=[]
    'AFS - JS'   -> exact

Only the exact spelling resolved, and the empty candidate list meant the error
told the caller nothing. "AFS-JS" is not even a substring of "AFS - JS", so the
icontains prefilter had nothing to offer.

HOW THIS STAYS SAFE
The boundary rule in event_resolver.py is not touched. This adds a tier that runs
BEFORE the resolver and can only ever produce a WHOLE-STRING match against a
WHOLE catalogue entry, modulo spacing and case:

    key("AFS-JS") == key("AFS - JS") == "AFS-JS"   -> canonical "AFS - JS"

Because both sides are normalised in full and compared for equality, this tier
cannot turn a partial match into a hit. If it finds nothing, the raw code falls
straight through to resolve_event_code unchanged, so the anchored boundary rule
behaves exactly as before:

    key("BIU") == "BIU";  key("BIUK - PM") == "BIUK-PM";  key("BIU/GS - PM") == "BIU/GS-PM"

"BIU" therefore matches no catalogue key unless an event literally named BIU
exists, and otherwise falls through to the anchored regex — which matches
BIU/GS - PM and never BIUK. The prefix pair is preserved by construction, not by
luck, and paper_review/tests_event_codes.py pins it (including the one subtlety
that is easy to over-state: if a literal 'BIU' event ALSO exists, tier 1 of the
underlying resolver answers on that alone and 'BIU/GS - PM' is never reached —
stricter than "also BIU/GS - PM", not a violation of the boundary rule).
"""
import re

from webhooks.event_resolver import resolve_event_code

# Collapse runs of whitespace, then drop whitespace hugging a separator.
_WS = re.compile(r"\s+")
_AROUND_SEPARATOR = re.compile(r"\s*([-/])\s*")


def normalise_event_code(code: str) -> str:
    """
    The comparison key: surrounding whitespace stripped, internal whitespace
    collapsed, spacing around '-' and '/' neutralised, upper-cased.

        "  afs  -  js "  -> "AFS-JS"
        "AFS - JS"       -> "AFS-JS"
        "BIU/GS - PM"    -> "BIU/GS-PM"

    Separators are kept, never removed: dropping them would make "BIU-K" and
    "BIUK" collide, and that is exactly the class of over-match this codebase has
    already been burned by.
    """
    text = _WS.sub(" ", str(code or "").strip())
    text = _AROUND_SEPARATOR.sub(r"\1", text)
    return text.upper()


def canonical_matches(code: str, queryset=None):
    """
    Catalogue codes whose comparison key equals this code's key.

    Normally 0 or 1. Two entries can only collide if the catalogue itself holds
    both "AFS-JS" and "AFS - JS" — event_code is unique on the exact string, so
    that is possible, and the caller must treat it as ambiguous rather than
    picking one.
    """
    from events.models import Event

    key = normalise_event_code(code)
    if not key:
        return []
    qs = Event.objects.all() if queryset is None else queryset
    return sorted(
        stored for stored in qs.values_list("event_code", flat=True)
        if normalise_event_code(stored) == key
    )


def resolve_with_spacing_tolerance(raw: str, queryset=None):
    """
    Returns the resolver's own Resolution, so every downstream check
    (matches / outcome / candidates) keeps working unchanged.

    Exactly one spacing-equivalent catalogue entry → resolve against that
    canonical spelling, which the resolver then answers EXACT. Otherwise the raw
    code goes to the resolver untouched and the anchored boundary rule decides.
    """
    code = (raw or "").strip()
    if not code:
        return resolve_event_code("", "", queryset=queryset)

    canonical = canonical_matches(code, queryset=queryset)
    if len(canonical) == 1:
        return resolve_event_code(canonical[0], canonical[0], queryset=queryset)

    # 0 matches → fall through to boundary matching.
    # 2+ matches → let the resolver speak for the raw code; the caller reports
    # the spacing collision separately using canonical_matches().
    return resolve_event_code(code, code, queryset=queryset)
