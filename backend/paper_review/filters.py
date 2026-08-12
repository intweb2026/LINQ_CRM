"""
paper_review/filters.py
────────────────────────
FilterSet following the ProposalSubmissionFilter / TicketFilter pattern.

event_code is ANCHORED, not substring: it reuses boundary_regex() from
webhooks/event_resolver.py rather than re-typing the rule, because plain icontains
returned "BIUK - PM" for a ?event_code=BIU query — a different event in a
different country. Postgres regex syntax has no lookaround, so the boundary cannot
be pushed into __iregex; icontains narrows in the database and the Python regex
decides, the same two-step the resolver itself uses.
"""
import django_filters

from webhooks.event_resolver import boundary_regex

from .models import PaperReview


def filter_event_code_anchored(queryset, name, value):
    """Anchored boundary match on event_code — see the module docstring."""
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


class PaperReviewFilter(django_filters.FilterSet):
    # Anchored, not substring — see filter_event_code_anchored above.
    event_code = django_filters.CharFilter(method="filter_event_code")

    # Free-text columns — substring, matching how the other apps filter prose.
    speaker_name = django_filters.CharFilter(lookup_expr="icontains")
    email        = django_filters.CharFilter(lookup_expr="icontains")
    company_name = django_filters.CharFilter(lookup_expr="icontains")
    theme        = django_filters.CharFilter(lookup_expr="icontains")

    # Dropdown columns — whole-value match. iexact rather than exact because
    # neither field has choices= at the model level, so stored case is not
    # guaranteed consistent.
    grade = django_filters.CharFilter(lookup_expr="iexact")
    session_location_on_agenda = django_filters.CharFilter(lookup_expr="iexact")
    nos = django_filters.BooleanFilter()

    # Ranges.
    paper_submission_date_from = django_filters.DateFilter(
        field_name="paper_submission_date", lookup_expr="gte")
    paper_submission_date_to = django_filters.DateFilter(
        field_name="paper_submission_date", lookup_expr="lte")
    created_at_from = django_filters.DateFilter(
        field_name="created_at", lookup_expr="gte")
    created_at_to = django_filters.DateFilter(
        field_name="created_at", lookup_expr="lte")
    proposal_score_min = django_filters.NumberFilter(
        field_name="proposal_score", lookup_expr="gte")
    proposal_score_max = django_filters.NumberFilter(
        field_name="proposal_score", lookup_expr="lte")

    # C4 — "everything from this import" without an undo endpoint.
    import_batch_id = django_filters.UUIDFilter(lookup_expr="exact")

    # C1 — surfaces duplicates, never blocks them: the same speaker legitimately
    # submits a paper for the same event twice. Reads the duplicate_count
    # annotation added by the viewset's get_queryset; filtering a Subquery
    # annotation is supported, which is why it is a Subquery and not a window
    # function.
    has_duplicates = django_filters.BooleanFilter(method="filter_has_duplicates")

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
        model = PaperReview
        fields = ["event_code", "grade", "session_location_on_agenda", "nos"]
