"""
book_event/booking_code_canonical.py
────────────────────────────────────
One place that decides how a `booking_code` is SPELLED.

THE BUG THIS FIXES
webhooks/services.py wrote the literal lowercase "delegate" on every booking it
created (services.py:337) and on every existing booking that had no code yet
(services.py:384). Everywhere else in the product the value is "Delegate":
frontend/src/lib/constants.js declares BOOKING_CODES as a closed list of
canonical spellings, and the 266 invoices / 339 delegates that predate the
webhook path all hold "Delegate" with a capital D. So the column had two
spellings for one value, and the webhook-created rows were the odd ones out.

WHY THAT MATTERS BEYOND LOOKS
The Bookings table renders booking_code as a select whose options are
BOOKING_CODES. A stored value not on that list is appended to its own dropdown
(DelegateTable), so "delegate" showed up as a SECOND, separate option sitting
next to "Delegate" — one logical code presenting as two. Grouping, distinct-value
filters and any exact-match comparison split the same code across two buckets.

WHY THIS IS SAFE — WHAT IT DOES NOT TOUCH
Canonicalisation is a WHOLE-STRING lookup against the closed list, compared on a
case- and spacing-insensitive key. A value only ever changes into a spelling
that is already on the list; anything the list does not know is returned
BYTE-FOR-BYTE UNCHANGED. There is no substring matching and no fuzzy tier, so a
free-text code this codebase has not seen cannot be rewritten into something
else — which is the failure mode event_resolver.py and booking_code.py both
exist to avoid.

Classification is unaffected either way: booking_code.classify() and its Q
objects already match case-insensitively (__iregex / __iexact), so no revenue
number moves as a result of this file. It fixes the SPELLING, not the meaning.

CONFIG, NOT CODE
The list is overridable with settings.BOOKING_CODE_CANONICAL, so a spelling can
be added without a code change. The default below mirrors
frontend/src/lib/constants.js BOOKING_CODES exactly; the two are asserted equal
by tests_booking_code_canonical.py so they cannot drift.
"""
import re

from django.conf import settings

# Mirrors frontend/src/lib/constants.js BOOKING_CODES. Keep the two in step.
_DEFAULT_CANONICAL = (
    "Add-Ons",
    "Advisory Board Member",
    "Complimentary",
    "Delegate",
    "GLD SpEx",
    "Group Pass",
    "Media",
    "PLT SpEx",
    "PTN SpEx",
    "SLV SpEx",
    "Speaker",
    "Speaker / GLD SpEx",
    "Speaker / Group Pass",
    "Speaker / PLT SpEx",
    "Speaker / PTN SpEx",
    "Speaker / SLV SpEx",
    "Speaker Table",
    "SPP",
    "SPP / Group Pass",
    "Upgraded to GLD SpEx",
    "Upgraded to PLT SpEx",
    "Upgraded to SLV SpEx",
)

# The code the webhook stamps on a booking that arrives without one. Named
# rather than typed inline, so the two call sites in webhooks/services.py cannot
# drift from each other or from the canonical list again.
DEFAULT_BOOKING_CODE = "Delegate"

_WS = re.compile(r"\s+")
_AROUND_SEPARATOR = re.compile(r"\s*([-/])\s*")


def canonical_codes():
    return tuple(getattr(settings, "BOOKING_CODE_CANONICAL", None) or _DEFAULT_CANONICAL)


def comparison_key(code):
    """
    The key two spellings are compared on: outer whitespace stripped, internal
    runs collapsed, spacing around '-' and '/' neutralised, case folded.

        "delegate"            -> "delegate"
        "  Delegate "         -> "delegate"
        "Speaker/ SLV SpEx"   -> "speaker/slv spex"

    Separators are KEPT, never removed, for the same reason
    webhooks/event_code_normalization.py keeps them: dropping '-' would let
    "Add-Ons" and "AddOns" collide with anything that merely spells a code
    differently, and this codebase has been burned by exactly that over-match.
    """
    text = _WS.sub(" ", str(code or "").strip())
    text = _AROUND_SEPARATOR.sub(r"\1", text)
    return text.casefold()


def _lookup():
    table = {}
    for canonical in canonical_codes():
        table.setdefault(comparison_key(canonical), canonical)
    return table


def canonicalize(code):
    """
    The canonical spelling of `code`, or `code` unchanged.

    UNCHANGED means unchanged: None stays None, "" stays "", and an unknown code
    is returned with its own whitespace and casing intact. Only a whole-string
    key match against the closed list rewrites anything, and it rewrites it to a
    spelling that list already holds.
    """
    if not code:
        return code
    return _lookup().get(comparison_key(code), code)


def is_canonical(code):
    """True when `code` is already spelled the way canonicalize() would spell it."""
    return canonicalize(code) == code


def canonicalize_on_save(instance, args, kwargs):
    """
    Canonicalise `instance.booking_code` IN PLACE, and make sure the corrected
    value is actually written.

    THE SUBTLETY THIS EXISTS FOR
    Fixing the attribute in save() is not enough on its own. The webhook updates
    an existing booking with `invoice.save(update_fields=[...])`, listing only
    the fields whose payload value changed (webhooks/services.py:405). An
    invoice already storing lowercase "delegate" has no NEW booking code in the
    payload, so booking_code is not in that list, and Django writes only the
    listed columns — the corrected value would be computed and then silently
    dropped. So when a correction is made and the caller restricted the write,
    booking_code is ADDED to update_fields, which is what makes every webhook
    touch of a stale row repair it.

    update_fields is only ever WIDENED, never replaced, and only when the value
    genuinely changed. A save that was already going to write the whole row is
    untouched, and a save of an already-canonical row adds nothing.

    Django 5.2 takes force_insert/force_update/using/update_fields as
    keyword-only arguments, so kwargs is the only place update_fields can be;
    `args` is accepted and returned unchanged so the call site stays a
    straightforward passthrough if that ever changes.
    """
    before = instance.booking_code
    after = canonicalize(before)
    if after == before:
        return args, kwargs

    instance.booking_code = after
    update_fields = kwargs.get("update_fields")
    if update_fields is not None:
        kwargs = dict(kwargs)
        kwargs["update_fields"] = list(update_fields) + ["booking_code"]
    return args, kwargs
