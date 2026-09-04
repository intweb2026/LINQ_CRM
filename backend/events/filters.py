import django_filters
from django.utils import timezone
from django.db.models import Q
from .models import Event

class EventFilter(django_filters.FilterSet):
    status          = django_filters.CharFilter(method='filter_status')
    event_date_from = django_filters.DateFilter(field_name="event_date", lookup_expr="gte")
    event_date_to   = django_filters.DateFilter(field_name="event_date", lookup_expr="lte")
    city            = django_filters.CharFilter(lookup_expr="icontains")
    event_code      = django_filters.CharFilter(lookup_expr="icontains")
    year            = django_filters.NumberFilter(field_name="year")
    base_code       = django_filters.CharFilter(lookup_expr="iexact")
    name            = django_filters.CharFilter(lookup_expr="icontains")
    official_name   = django_filters.CharFilter(lookup_expr="icontains")
    accepting_web_bookings = django_filters.BooleanFilter()
    sales_executive = django_filters.NumberFilter(field_name="sales_executive__id")
    team_leader     = django_filters.CharFilter(lookup_expr="icontains")

    class Meta:
        model  = Event
        fields = ["status", "event_date_from", "event_date_to", "city", "event_code", "base_code", "year", "name", "official_name", "accepting_web_bookings", "sales_executive", "team_leader"]

    def filter_status(self, queryset, name, value):
        today = timezone.now().date()
        val_lower = value.lower()
        if val_lower == "completed":
            return queryset.filter(event_date__lt=today)
        elif val_lower == "live":
            return queryset.filter(Q(event_date__gte=today) | Q(event_date__isnull=True))
        
        valid_statuses = [c[0].lower() for c in Event.Status.choices]
        if val_lower in valid_statuses:
            return queryset.filter(status__iexact=value)
        return queryset
