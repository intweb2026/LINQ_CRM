"""
events/codes.py
────────────────
The three code readers the catalogue and its importers share.

BASE CODE vs INTERNAL CODE. One event family runs every year under one BASE code
(AFS), while each edition carries its own INTERNAL code for display: AFS in 2025,
AFS - JS in 2026, Feb2027_AFS-JS in 2027. Everything that groups editions, the
Performance Matrix above all, keys on (base_code, year); the internal code is a
label. `derive_base_code` is the DEFAULT written when an event is saved without
one, so an admin only has to correct the odd family the rule gets wrong.
"""
import re

_ALPHA = re.compile(r"[A-Za-z]+")

# A leading calendar month is a prefix, never a family: "Feb2027_AFS-JS" is AFS.
MONTHS = frozenset({
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "SEPT", "OCT", "NOV", "DEC",
    "JANUARY", "FEBRUARY", "MARCH", "APRIL", "JUNE",
    "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
})


def derive_base_code(event_code: str) -> str:
    """
    The family an internal code most plausibly belongs to.

        'AFS'             -> 'AFS'
        'AFS - JS'        -> 'AFS'
        'Feb2027_AFS-JS'  -> 'AFS'
        'BIU/GS - PM'     -> 'BIU'
        'ACU25'           -> 'ACU'

    First alphabetic token that is not a month. A default, not a verdict: the
    Events form lets an admin overwrite it, and every calculation reads the
    stored column rather than calling this again.
    """
    tokens = _ALPHA.findall(event_code or "")
    meaningful = [t for t in tokens if t.upper() not in MONTHS] or tokens
    return meaningful[0].upper() if meaningful else ""


def normalize_master_code(event_code: str) -> str:
    """
    First three alphabetic characters, upper-cased. 'ACU - RS26' -> 'ACU'.

    Kept ONLY for the two booking importers that were written against it
    (book_event/management/commands/import_booking_excel.py and
    import_remaining_bookings.py). New code keys on Event.base_code.
    """
    if not event_code:
        return ""
    return re.sub(r"[^A-Za-z]", "", event_code)[:3].upper()


def extract_year_from_code(event_code: str):
    """Trailing two digits as a year: 'ACU - RS26' -> 2026, 'DDU' -> None."""
    if not event_code:
        return None
    m = re.search(r"(\d{2})\s*$", event_code.strip())
    return int("20" + m.group(1)) if m else None
