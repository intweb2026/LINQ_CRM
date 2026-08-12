"""
book_event/booking_code.py
───────────────────────────
One place that decides what a `booking_code` means.

THE BUG THIS FIXES
Classification was five inline `icontains` clauses in the reports queryset
(views.py:182-187):

    SPEX_Q    = Q(booking_code__icontains="spex") | Q(booking_code__iexact="Add-Ons")
    SPEAKER_Q = Q(booking_code__icontains="speaker") | Q(booking_code__icontains="spp")

`booking_code` is free text (CharField(100)). Unanchored substring matching is
exactly the failure that webhooks/event_resolver.py was written to end: a code
containing another category's marker as a substring is silently misfiled. "SPP"
is three characters and will appear incidentally — "SUPPLEMENT", "SPPX", a
supplier reference — and every one of those would have been counted as speaker
sales. Nothing would have surfaced it; the number would just have been wrong.

THE RULE — identical to event_resolver's, and single-sourced from it
A marker matches only where it appears with a non-alphanumeric character, or a
string edge, on BOTH sides. `boundary_regex` is imported rather than re-typed so
the two cannot drift, and so the "_ is not a word character" reasoning documented
there applies here too.

CONFIG, NOT CODE
The marker lists live in settings (BOOKING_CODE_SPEX_MARKERS,
BOOKING_CODE_SPEAKER_MARKERS, BOOKING_CODE_SPEX_EXACT) so they can be populated
from the real export without touching query code. The defaults below are the
values the old inline query used — they are CARRIED FORWARD, NOT VERIFIED. The
real distinct-value list is unknown until the Zoho export arrives; run
`analyse_zoho_export` against it and correct these lists before trusting any
number that depends on them.

READ AND WRITE SIDES AGREE
classify() is the authority for Python-side work (the loader, tests). category_q()
expresses the SAME rule as a Q object so aggregate queries do not fall back to
substring matching. Both are driven from the same lists, and a test asserts they
agree on a shared corpus — otherwise the two could diverge silently, which is how
this class of bug survives.
"""
from django.conf import settings
from django.db.models import Q

from webhooks.event_resolver import boundary_regex

# Categories. `DELEGATE` is the fall-through: anything that is neither SpEx nor
# speaker sales is ordinary delegate/sales revenue.
SPEX = "spex"
SPEAKER_SALES = "speaker_sales"
DELEGATE = "delegate"

CATEGORIES = (SPEX, SPEAKER_SALES, DELEGATE)

# Defaults = what the inline query used. See CONFIG, NOT CODE above.
_DEFAULT_SPEX_MARKERS = ("spex",)
_DEFAULT_SPEAKER_MARKERS = ("speaker", "spp")
# Matched whole-string, case-insensitively — not as a marker inside a longer code.
_DEFAULT_SPEX_EXACT = ("Add-Ons",)


def _cfg(name, default):
    return tuple(getattr(settings, name, None) or default)


def spex_markers():
    return _cfg("BOOKING_CODE_SPEX_MARKERS", _DEFAULT_SPEX_MARKERS)


def speaker_markers():
    return _cfg("BOOKING_CODE_SPEAKER_MARKERS", _DEFAULT_SPEAKER_MARKERS)


def spex_exact():
    return _cfg("BOOKING_CODE_SPEX_EXACT", _DEFAULT_SPEX_EXACT)


def _matches_any(code, markers):
    return any(boundary_regex(m).search(code) for m in markers)


def classify(booking_code):
    """
    Return one of CATEGORIES for a free-text booking code.

    Precedence is SpEx, then speaker sales, then delegate. It is explicit rather
    than incidental: a code carrying both markers has to land somewhere, and the
    old Q-object version resolved that collision by whichever branch the caller
    happened to evaluate first, which was not a decision anyone made.
    """
    code = (booking_code or "").strip()
    if not code:
        return DELEGATE
    if any(code.lower() == e.lower() for e in spex_exact()):
        return SPEX
    if _matches_any(code, spex_markers()):
        return SPEX
    if _matches_any(code, speaker_markers()):
        return SPEAKER_SALES
    return DELEGATE


def _boundary_q(field, markers):
    """
    The anchored rule as a Q. Postgres `~*` via __iregex, so the DATABASE applies
    the same boundary test classify() applies in Python — not a substring
    approximation of it.
    """
    q = Q()
    for marker in markers:
        q |= Q(**{f"{field}__iregex": rf"(^|[^A-Za-z0-9]){marker}([^A-Za-z0-9]|$)"})
    return q


# ── ORM predicates ───────────────────────────────────────────────────────────
# TWO APIs ON PURPOSE, because the two call sites want different things:
#
#   spex_q() / speaker_q()  — OVERLAPPING. A hybrid code ("Speaker / SLV SpEx")
#                             satisfies both, which views.py:181 documents as
#                             intentional for the KPI cards: that booking is
#                             genuinely both SpEx and speaker-sales revenue and
#                             is counted on both cards. Preserved exactly.
#   classify() / category_q — EXCLUSIVE. One row, one label, for the loader and
#                             for any report that must sum to the total without
#                             double-counting.
#
# Only the MATCHING RULE changed (substring → anchored). The overlap semantics
# are untouched.

def spex_q(field="booking_code"):
    """Anchored replacement for `icontains="spex" | iexact="Add-Ons"`."""
    q = Q()
    for value in spex_exact():
        q |= Q(**{f"{field}__iexact": value})
    return q | _boundary_q(field, spex_markers())


def speaker_q(field="booking_code"):
    """Anchored replacement for `icontains="speaker" | icontains="spp"`."""
    return _boundary_q(field, speaker_markers())


def category_q(category, field="booking_code"):
    """
    Q selecting rows of `category`, EXCLUSIVELY — the three categories are
    exhaustive and mutually disjoint by construction rather than by three lists
    kept disjoint by hand. Precedence matches classify(): SpEx wins a collision.

    `field` lets callers reach through a relation ("invoice__booking_code").
    """
    if category not in CATEGORIES:
        raise ValueError(f"unknown booking-code category {category!r}")

    spex = spex_q(field)
    if category == SPEX:
        return spex

    speaker = speaker_q(field)
    if category == SPEAKER_SALES:
        return speaker & ~spex

    return ~spex & ~speaker
