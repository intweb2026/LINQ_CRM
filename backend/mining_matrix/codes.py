"""
mining_matrix/codes.py
───────────────────────
The one rule that joins the Events catalogue to Ticket Central.

THE MISMATCH THIS EXISTS TO CLOSE
Ticket Central stores a SHORT, STABLE code in `purpose` — AFS, DDU, BAPE, SFIL —
and it is the same string every year, because ticket_number is built from it
(utils.build_ticket_number) and TicketSequence is keyed on it. The Events
catalogue stores a per-EDITION code, and those vary freely:

    AFS              the bare master code
    AFS - JS         a stream or co-located suffix
    Feb2027_AFS-JS   a month/year prefix as well

All three are the same event family, and every ticket raised for any of them
carries `purpose = "AFS"`. So the matrix cannot join on event_code at all; it has
to reduce an event code to the purpose it belongs to.

WHY NOT events.codes.normalize_master_code
That function takes the first THREE alphabetic characters, which is wrong twice
over here. It truncates the real codes that are longer — BAPE→BAP, SFIL→SFI,
FLNU→FLN, WLKE→WLK, SCSG→SCS — and it reads a leading month as the code, so
"Feb2027_AFS-JS" resolves to "FEB". Both failures are silent: the row simply
shows zero unmined links, which is indistinguishable from an event that is
genuinely fully mined. It is left alone because the booking importers depend on
its exact behaviour.

THE APPROACH: MATCH AGAINST THE REAL CODE LIST, DO NOT INVENT A SHAPE
`purpose` is a closed set — whatever values Ticket Central actually holds — so
this reads that set and asks which member an event code contains, rather than
guessing at a length or a separator convention. A new purpose code of any length
is understood the day the first ticket carries it, with no change here.
"""
import re

# Dropped from a code before matching, but only when something else survives —
# "Feb2027_AFS-JS" leads with a calendar month, and a bare month is never an
# event family. One list, shared with events.codes.derive_base_code.
from events.codes import MONTHS as _MONTHS

_ALPHA = re.compile(r"[A-Za-z]+")


def known_purpose_codes():
    """
    Every distinct `purpose` Ticket Central holds, upper-cased.

    One query over an indexed column, and the answer is small: 76 distinct values
    across ~43,000 tickets in the live table. Read once per request by the
    service and passed down, never per row.
    """
    from ticket_central.models import Ticket

    return frozenset(
        p.strip().upper()
        for p in Ticket.objects.exclude(purpose="")
                               .values_list("purpose", flat=True)
                               .distinct()
        if p and p.strip()
    )


def canonical_code(raw, known):
    """
    The Ticket Central purpose an Events code belongs to, or a best-effort stand-in.

    Four passes, narrowest first, so an exact answer is never beaten by a fuzzy one:

      1. the whole code IS a purpose            "AFS"            → AFS
      2. one of its alphabetic tokens is        "Feb2027_AFS-JS" → AFS
      3. a token STARTS WITH a purpose          "AFS26"/"WSEEU"  → AFS / WSE
      4. nothing matched — the first token, so the row still names itself and
         reports honestly that it found no tickets

    Pass 2 walks the tokens LEFT TO RIGHT because an event code leads with its
    family and qualifies afterwards ("MMU/GS - JS26" is an MMU event). Pass 3
    takes the LONGEST purpose a token starts with, so a two-letter stem can never
    beat the four-letter code that actually matches.
    """
    s = (raw or "").strip().upper()
    if not s:
        return ""
    if s in known:
        return s

    tokens = _ALPHA.findall(s)
    if not tokens:
        return ""
    # `or tokens` so a code that is nothing but a month still returns something
    # rather than falling off the end into an empty string.
    meaningful = [t for t in tokens if t not in _MONTHS] or tokens

    for token in meaningful:
        if token in known:
            return token

    for token in meaningful:
        hits = [k for k in known if len(k) > 1 and token.startswith(k)]
        if hits:
            return max(hits, key=len)

    return meaningful[0]


def resolve_codes(event_codes, known):
    """{original event_code: canonical purpose} — memoised across repeats."""
    cache = {}
    out = {}
    for code in event_codes:
        key = (code or "").strip().upper()
        if key not in cache:
            cache[key] = canonical_code(code, known)
        out[code] = cache[key]
    return out
