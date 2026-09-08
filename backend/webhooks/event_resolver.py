"""
webhooks/event_resolver.py
──────────────────────────
Single source of truth for turning an inbound free-text event code into an
Event row. Nothing else in the webhook path may match event codes.

THE BUG THIS FIXES
Resolution used to be five OR'd clauses in one queryset — iexact, istartswith
and icontains over both the raw and the normalised code — with -event_date
picking the winner. A payload carrying "BIU" therefore attached to `BIUK - PM`,
because "BIUK - PM" contains "BIU" and happened to have the later date. The
booking was filed against the wrong event and the caller was told 200.

THE RULE
A code matches only where it appears with a non-alphanumeric character, or a
string edge, on BOTH sides. "BIU" matches `BIU/GS - PM` and `BIU - XX`; it does
not match `BIUK - PM`, because "K" is alphanumeric. The trailing segment of an
event code is dynamic and may be anything, so the boundary — not the shape of
what follows it — is the whole test.

The character class is spelled out as [A-Za-z0-9] rather than using \b, because
\b treats "_" as a word character. `BIU_EU` must resolve from "BIU"; under \b it
would not.

DEVIATION FROM THE DELUGE REFERENCE
The legacy processDelegates1 boundary-checks only the FIRST occurrence, via
indexOf. This regex checks every occurrence, so a code buried later in a longer
event code still matches. That is a strict superset of the Deluge behaviour and
never matches something Deluge would reject. Deliberate; do not narrow it back
to first-occurrence without a decision.

TIER SEMANTICS — load-bearing
Tier 1 is exact (raw, then normalised); tier 2 is anchored boundary (raw, then
normalised). The first search code producing ANY match wins outright, and the
outcome is decided entirely within that winning set — even when every match in
it has web bookings switched off. It must never fall through to a later tier to
find a more agreeable answer: `BIUK - PM26` is an exact hit that is bookings-off,
and the correct answer is "that edition is closed", not "here is a different
edition". Those are different operator actions.

THE ONE EXCEPTION — base-code placeholders, and only for web bookings
A row whose event_code IS its own base_code (`WSU`, `PPTX`, `BGE`) is a FAMILY
placeholder, not an edition; the bookable edition carries MRE initials
(`WSU - MP`). An inbound `WSU` exact-matched that placeholder, which is closed
by default, and every booking for the family was refused with "web bookings is
disabled on that edition" while the real edition sat open. So when EVERY tier-1
exact hit is a closed placeholder, the tier does not win and matching continues
to the boundary tier, where the internal code resolves.

This does not weaken the rule above. `BIUK - PM26` has base_code `BIUK`, so it
is a real edition and still answers "that edition is closed". Only a row that
names the family and nothing else steps aside, and only when it is closed — a
placeholder an admin has deliberately opened still wins outright.

It is opt-in via `for_web_booking`, and the default is OFF, because "closed"
only means something to a caller that is taking a booking. paper_review and
proposal_submission resolve the SAME catalogue for paper and proposal
submissions, where web bookings is irrelevant and every event is closed by
default — and they read `.matches` directly rather than `.event`, so widening
the set there would turn a clean single match into a false ambiguity error.
Only webhooks/services.py, the booking ingest path, passes it.

Within a winning set every match is collected before deciding — never .first().
Two or more matches that accept web bookings is an ambiguity, answered with 409.
Ambiguity is never broken by -event_date; picking the newest edition is how the
original bug silently chose the wrong event.

The resolver never writes and never raises. The caller maps outcome → HTTP.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from events.models import Event


class Outcome(str, Enum):
    """Why the resolver returned what it did. The caller maps these to HTTP."""
    EXACT        = "exact"          # resolved on a case-insensitive exact match
    BOUNDARY     = "boundary"       # resolved on anchored boundary matching
    NO_MATCH     = "no_match"       # nothing matched under the anchored rule
    BOOKINGS_OFF = "bookings_off"   # matched, but no match accepts web bookings
    AMBIGUOUS    = "ambiguous"      # 2+ matches accept web bookings

    @property
    def resolved(self) -> bool:
        return self in (Outcome.EXACT, Outcome.BOUNDARY)


# Diagnostic markers, mirroring the Deluge DIAG-A/B/C convention so a failure is
# identifiable from the log line alone without re-reading this module.
DIAG = {
    Outcome.EXACT:        "DIAG-OK-EXACT",
    Outcome.BOUNDARY:     "DIAG-OK-BOUNDARY",
    Outcome.NO_MATCH:     "DIAG-A-NO-MATCH",
    Outcome.BOOKINGS_OFF: "DIAG-B-BOOKINGS-OFF",
    Outcome.AMBIGUOUS:    "DIAG-C-AMBIGUOUS",
}


def boundary_regex(code: str) -> re.Pattern:
    """
    `code` must sit between non-alphanumerics or string edges.

    re.escape matters: real codes contain "/", "." and "-", all of which are
    regex metacharacters or range syntax.
    """
    return re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(code)}(?![A-Za-z0-9])", re.IGNORECASE
    )


@dataclass
class Resolution:
    """Everything the caller needs to answer, log and diagnose, in one object."""
    outcome:    Outcome
    event:      Event | None      = None
    matches:    list              = field(default_factory=list)  # winning tier, all of it
    candidates: list              = field(default_factory=list)  # prefilter codes
    raw:        str               = ""
    normalized: str               = ""
    tier:       str               = ""

    @property
    def ok(self) -> bool:
        return self.event is not None

    @property
    def matched_codes(self) -> list:
        return [e.event_code for e in self.matches]

    @property
    def diagnostic(self) -> str:
        """
        One block, everything needed to explain the decision without the payload
        or a debugger: what arrived, what it normalised to, what the DB prefilter
        offered, what survived the rule, and which rule fired.
        """
        return "\n".join([
            f"{DIAG[self.outcome]} event-code resolution",
            f"  raw code received : {self.raw!r}",
            f"  normalized code   : {self.normalized!r}",
            f"  prefilter candidates ({len(self.candidates)}): {self.candidates}",
            f"  matched under rule ({len(self.matches)}): {self.matched_codes}",
            f"  tier applied      : {self.tier or 'none'}",
            f"  outcome           : {self.outcome.value}",
            f"  resolved to       : {self.event.event_code!r}" if self.event
            else "  resolved to       : nothing",
        ])

    @property
    def error_message(self) -> str:
        """The operator-facing message. Empty when resolution succeeded."""
        if self.outcome is Outcome.NO_MATCH:
            return (f"No event matches code {self.raw!r} under anchored boundary "
                    f"matching. Prefilter candidates: {self.candidates}")
        if self.outcome is Outcome.BOOKINGS_OFF:
            return (f"Event code {self.raw!r} matched {self.matched_codes}, but web "
                    f"bookings is disabled on "
                    f"{'that edition' if len(self.matches) == 1 else 'every matched edition'}.")
        if self.outcome is Outcome.AMBIGUOUS:
            return (f"Ambiguous event code {self.raw!r} matched {len(self.matches)} "
                    f"editions: {self.matched_codes}. Disambiguate at source.")
        return ""

    @property
    def http_status(self) -> int:
        if self.outcome is Outcome.AMBIGUOUS:
            return 409
        return 200 if self.ok else 400


def _decide(outcome: Outcome, matches: list, *, tier: str, raw: str,
            normalized: str, candidates: list) -> Resolution:
    """
    Turn a winning tier's full match set into an answer.

    Ambiguity is judged only among matches that accept web bookings: two
    editions where one is closed is not ambiguous, it is one open edition.
    """
    accepting = [e for e in matches if e.accepting_web_bookings]
    base = dict(matches=matches, candidates=candidates, raw=raw,
                normalized=normalized, tier=tier)

    if not accepting:
        return Resolution(outcome=Outcome.BOOKINGS_OFF, **base)
    if len(accepting) > 1:
        return Resolution(outcome=Outcome.AMBIGUOUS, matches=accepting,
                          candidates=candidates, raw=raw,
                          normalized=normalized, tier=tier)
    return Resolution(outcome=outcome, event=accepting[0], **base)


def _prefilter_codes(qs, searches: list) -> list:
    """
    Every code the cheap DB prefilter offered, for the diagnostic. This is what
    the rule was applied TO — the difference between it and the matched set is
    exactly what the boundary rule rejected, which is the question an operator
    reading a 400 actually has.
    """
    codes = set()
    for code in searches:
        codes.update(qs.filter(event_code__icontains=code)
                       .values_list("event_code", flat=True))
    return sorted(codes)


def _is_base_placeholder(event) -> bool:
    """
    True when this row names the FAMILY and nothing else — event_code equals
    base_code, so it carries no edition identity (`WSU`, not `WSU - MP`).

    base_code must be non-empty: a row saved before the column existed has both
    sides blank, and "" == "" would make every such row a placeholder.
    """
    base = (event.base_code or "").strip().upper()
    return bool(base) and (event.event_code or "").strip().upper() == base


def _closed_placeholders_only(hits: list) -> bool:
    """A tier-1 set that must step aside: all placeholders, none of them open."""
    return (not any(e.accepting_web_bookings for e in hits)
            and all(_is_base_placeholder(e) for e in hits))


def resolve_event_code(raw: str, normalized: str, *, queryset=None,
                       for_web_booking: bool = False) -> Resolution:
    """
    Resolve an inbound code to an Event. Never writes, never raises.

    `queryset` is injectable so callers can scope the search; it defaults to all
    events, which is correct for webhook ingestion — an inbound booking is not
    scoped to a user.

    `for_web_booking` lets a closed base-code placeholder step aside for the
    real edition it shadows. Off by default; see THE ONE EXCEPTION above for
    why only the booking path may ask for it.
    """
    qs = Event.objects.all() if queryset is None else queryset
    raw        = (raw or "").strip()
    normalized = (normalized or "").strip()

    # Order matters and duplicates are dropped: when normalising is a no-op we
    # must not run the same search twice and call the result ambiguous.
    searches = [c for c in dict.fromkeys([raw, normalized]) if c]
    if not searches:
        return Resolution(outcome=Outcome.NO_MATCH, raw=raw, normalized=normalized,
                          tier="none", candidates=[])

    # ── Tier 1: exact ────────────────────────────────────────────────────────
    for code in searches:
        hits = list(qs.filter(event_code__iexact=code))
        # A closed family placeholder does not win the tier; the real edition it
        # shadows is found by the boundary tier below. See THE ONE EXCEPTION.
        if hits and not (for_web_booking and _closed_placeholders_only(hits)):
            return _decide(Outcome.EXACT, hits,
                           tier=f"exact({code!r})", raw=raw, normalized=normalized,
                           candidates=sorted(e.event_code for e in hits))

    # ── Tier 2: anchored boundary ────────────────────────────────────────────
    # icontains is a cheap prefilter to keep the row count down; the regex is
    # the authority and is what actually decides.
    for code in searches:
        prefilter = list(qs.filter(event_code__icontains=code))
        rx = boundary_regex(code)
        hits = [e for e in prefilter if rx.search(e.event_code)]
        if hits:
            return _decide(Outcome.BOUNDARY, hits,
                           tier=f"boundary({code!r})", raw=raw,
                           normalized=normalized,
                           candidates=sorted(e.event_code for e in prefilter))

    # No tier 3. A code that reaches here has no anchored match, and guessing is
    # what caused the bug.
    return Resolution(outcome=Outcome.NO_MATCH, raw=raw, normalized=normalized,
                      tier="none", candidates=_prefilter_codes(qs, searches))
