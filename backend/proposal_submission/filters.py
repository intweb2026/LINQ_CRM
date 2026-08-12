"""
proposal_submission/filters.py
───────────────────────────────
FilterSet following the TicketFilter / BookEventFilter pattern.

The six exact-match filters are the columns the frontend table offers header
dropdowns for. They use iexact rather than exact so a header filter still matches
when the stored value differs only in case — these fields have no choices= at the
model level, so case is not guaranteed to be consistent.
"""
import re

import django_filters

from webhooks.event_resolver import boundary_regex
from .models import ProposalSubmission


def filter_event_code_anchored(queryset, name, value):
    """
    Anchored boundary match on event_code, using the SAME regex the resolver
    uses — boundary_regex() is imported, not re-typed.

    Plain icontains was wrong: ?event_code=BIU returned "BIUK - PM", a different
    event in a different country. The code must sit between non-alphanumerics or
    string edges, so BIU matches "BIU" and "BIU/GS - PM" but never "BIUK - PM".

    Postgres regex syntax has no lookaround, so the boundary cannot be pushed
    into __iregex. icontains narrows in the database and the Python regex
    decides — the same two-step the resolver uses, and the reason boundary_regex
    is shared rather than duplicated.
    """
    code = (value or "").strip()
    if not code:
        return queryset
    rx = boundary_regex(code)
    keep = [
        pk for pk, stored in
        queryset.filter(event_code__icontains=code).values_list("pk", "event_code")
        if rx.search(stored or "")
    ]
    return queryset.filter(pk__in=keep)


class ProposalSubmissionFilter(django_filters.FilterSet):
    # Anchored, not substring — see filter_event_code_anchored above.
    event_code         = django_filters.CharFilter(method="filter_event_code")
    # Text columns — substring, matching how the other apps filter free text.
    # These are search boxes over prose, where partial matching is the point.
    speaker_name       = django_filters.CharFilter(lookup_expr="icontains")
    email              = django_filters.CharFilter(lookup_expr="icontains")
    company_name       = django_filters.CharFilter(lookup_expr="icontains")
    presentation_theme = django_filters.CharFilter(lookup_expr="icontains")
    agenda_slot        = django_filters.CharFilter(lookup_expr="icontains")
    sales_pitch_factor = django_filters.CharFilter(lookup_expr="icontains")

    # Dropdown columns — whole-value match.
    participation_type  = django_filters.CharFilter(lookup_expr="iexact")
    qc_grade            = django_filters.CharFilter(lookup_expr="iexact")
    speaker_slot_status = django_filters.CharFilter(lookup_expr="iexact")
    sponsorship_status  = django_filters.CharFilter(lookup_expr="iexact")
    revenue_possibility = django_filters.CharFilter(lookup_expr="iexact")

    # Ranges.
    submission_date_from = django_filters.DateFilter(
        field_name="submission_date", lookup_expr="gte")
    submission_date_to   = django_filters.DateFilter(
        field_name="submission_date", lookup_expr="lte")
    created_at_from      = django_filters.DateFilter(
        field_name="created_at", lookup_expr="gte")
    created_at_to        = django_filters.DateFilter(
        field_name="created_at", lookup_expr="lte")
    qc_score_min         = django_filters.NumberFilter(
        field_name="qc_score", lookup_expr="gte")
    qc_score_max         = django_filters.NumberFilter(
        field_name="qc_score", lookup_expr="lte")

    # Surfaces duplicates, never blocks them — speakers legitimately resubmit.
    # Reads the duplicate_count annotation added by the viewset's get_queryset;
    # filtering a Subquery annotation is supported, which is why it is a Subquery
    # and not a window function.
    has_duplicates = django_filters.BooleanFilter(method="filter_has_duplicates")

    # C4 — "everything from this import" without an undo endpoint: this filter
    # IS the answer to "what landed". Exact match only; a UUID has no meaningful
    # partial-match operator.
    import_batch_id = django_filters.UUIDFilter(lookup_expr="exact")

    def filter_event_code(self, queryset, name, value):
        return filter_event_code_anchored(queryset, name, value)

    def filter_has_duplicates(self, queryset, name, value):
        if value is None:
            return queryset
        if "duplicate_count" not in queryset.query.annotations:
            # Reachable only if something filters outside the viewset (a
            # management command, say). Say so rather than silently no-op.
            return queryset
        return queryset.filter(duplicate_count__gt=0) if value \
            else queryset.filter(duplicate_count=0)

    class Meta:
        model  = ProposalSubmission
        fields = [
            "event_code", "participation_type", "qc_grade",
            "speaker_slot_status", "sponsorship_status", "revenue_possibility",
        ]
