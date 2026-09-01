"""
ticket_central/filters.py
──────────────────────────
FilterSet following BookEventFilter pattern.
"""
import django_filters
from .models import Ticket


class TicketFilter(django_filters.FilterSet):
    ticket_number         = django_filters.CharFilter(lookup_expr="icontains")
    external_id           = django_filters.CharFilter(lookup_expr="icontains")
    event_code            = django_filters.CharFilter(lookup_expr="icontains")
    purpose               = django_filters.CharFilter(lookup_expr="icontains")
    competitor_event_name = django_filters.CharFilter(lookup_expr="icontains")
    organizer             = django_filters.CharFilter(lookup_expr="icontains")
    event_location        = django_filters.CharFilter(lookup_expr="icontains")
    status                = django_filters.MultipleChoiceFilter(choices=Ticket.Status.choices)
    # D26: priority and relationship are free-text CharFields after D4
    priority              = django_filters.CharFilter(lookup_expr="iexact")
    relationship          = django_filters.CharFilter(lookup_expr="iexact")
    type_of_ticket        = django_filters.CharFilter(lookup_expr="icontains")
    ticket_type           = django_filters.CharFilter(lookup_expr="icontains")
    assigned_mr           = django_filters.CharFilter(lookup_expr="icontains")
    assign_name           = django_filters.CharFilter(lookup_expr="icontains")
    assign_name_lx2       = django_filters.CharFilter(lookup_expr="icontains")
    linkedin_keywords     = django_filters.CharFilter(lookup_expr="icontains")
    mr_comments           = django_filters.CharFilter(lookup_expr="icontains")
    dm_comments           = django_filters.CharFilter(lookup_expr="icontains")
    source_tab            = django_filters.CharFilter(lookup_expr="icontains")

    # Date range filters
    created_at_from       = django_filters.DateFilter(field_name="created_at",        lookup_expr="gte")
    created_at_to         = django_filters.DateFilter(field_name="created_at",        lookup_expr="lte")
    complete_date_from    = django_filters.DateFilter(field_name="complete_date",      lookup_expr="gte")
    complete_date_to      = django_filters.DateFilter(field_name="complete_date",      lookup_expr="lte")
    assign_date_from      = django_filters.DateFilter(field_name="assign_date",        lookup_expr="gte")
    assign_date_to        = django_filters.DateFilter(field_name="assign_date",        lookup_expr="lte")
    hubspot_date_from     = django_filters.DateFilter(field_name="hubspot_entry_date", lookup_expr="gte")
    hubspot_date_to       = django_filters.DateFilter(field_name="hubspot_entry_date", lookup_expr="lte")
    event_month_from      = django_filters.DateFilter(field_name="event_month_year",   lookup_expr="gte")
    event_month_to        = django_filters.DateFilter(field_name="event_month_year",   lookup_expr="lte")

    # Number range filters
    actual_number_gte     = django_filters.NumberFilter(field_name="actual_number",    lookup_expr="gte")
    actual_number_lte     = django_filters.NumberFilter(field_name="actual_number",    lookup_expr="lte")
    mined_count_gte       = django_filters.NumberFilter(field_name="mined_count",      lookup_expr="gte")
    mined_count_lte       = django_filters.NumberFilter(field_name="mined_count",      lookup_expr="lte")

    class Meta:
        model  = Ticket
        fields = [
            "ticket_number", "external_id", "event_code",
            "status", "priority", "relationship",
            "type_of_ticket", "ticket_type",
            "purpose", "competitor_event_name", "organizer",
            "event_location", "linkedin_keywords",
            "assigned_mr", "assign_name", "assign_name_lx2",
            "mr_comments", "dm_comments", "source_tab",
            "created_at_from", "created_at_to",
            "complete_date_from", "complete_date_to",
            "assign_date_from", "assign_date_to",
            "hubspot_date_from", "hubspot_date_to",
            "event_month_from", "event_month_to",
            "actual_number_gte", "actual_number_lte",
            "mined_count_gte", "mined_count_lte",
        ]
