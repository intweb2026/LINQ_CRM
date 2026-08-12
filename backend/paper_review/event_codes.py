"""
paper_review/event_codes.py
────────────────────────────
Thin re-export of webhooks/event_code_normalization.py.

C2 EXTRACTED THE REAL LOGIC OUT OF THIS FILE. It used to be defined here; it now
lives in webhooks/event_code_normalization.py so proposal_submission/importer.py
can use the SAME spacing-tolerant resolution without importing across the two
sibling pipeline apps (paper_review importing FROM proposal_submission, or vice
versa, would be exactly that). See that module's docstring for the full mechanism
and the safety argument for why this cannot weaken event_resolver.py's anchored
boundary rule.

This module is kept, rather than updating every caller to the new import path,
because paper_review/serializers.py:validate_event_code already reads
`resolve_paper_event_code` from here, and a rename with no behaviour change is
pure churn. `resolve_paper_event_code` is a thin, paper_review-flavoured alias
for the shared `resolve_with_spacing_tolerance` — same signature, same Resolution
object, same everything, only the name differs.
"""
from webhooks.event_code_normalization import (
    canonical_matches, normalise_event_code, resolve_with_spacing_tolerance,
)

# Public re-exports — unchanged names, so every existing import of
# `from paper_review.event_codes import normalise_event_code / canonical_matches`
# (including this app's own tests) keeps working untouched.
__all__ = ["normalise_event_code", "canonical_matches", "resolve_paper_event_code"]


def resolve_paper_event_code(raw: str, queryset=None):
    """Alias for resolve_with_spacing_tolerance — see the module docstring."""
    return resolve_with_spacing_tolerance(raw, queryset=queryset)
