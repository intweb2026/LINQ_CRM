import django_filters
from django.db.models import Q
from .models import BookDelegate
from book_event.models import BookEvent


class BookDelegateFilter(django_filters.FilterSet):
    # Text Search (icontains)
    first_name   = django_filters.CharFilter(lookup_expr="icontains")
    last_name    = django_filters.CharFilter(lookup_expr="icontains")
    email        = django_filters.CharFilter(lookup_expr="icontains")
    position     = django_filters.CharFilter(lookup_expr="icontains")
    # The delegate's own column, matching what the Bookings table displays and
    # edits. It was invoice__booking_code, which now disagrees with the cell for
    # any invoice whose delegates carry different codes.
    booking_code = django_filters.CharFilter(lookup_expr="icontains")
    invoice_number = django_filters.CharFilter(field_name="invoice__invoice_number", lookup_expr="icontains")
    
    # Exact / Multiple
    event_code     = django_filters.CharFilter(lookup_expr="icontains")
    edition        = django_filters.CharFilter(method="filter_edition")
    attendance     = django_filters.CharFilter(method="filter_attendance")
    company        = django_filters.NumberFilter(field_name="company__id")
    company_name   = django_filters.CharFilter(field_name="invoice__company_name", lookup_expr="icontains")
    delegate_count = django_filters.NumberFilter()
    discount       = django_filters.NumberFilter()
    
    # Overrides (Effective values)
    payment_status = django_filters.MultipleChoiceFilter(
        choices=BookEvent.PaymentStatus.choices,
        method="filter_payment_status",
    )
    payment_type = django_filters.MultipleChoiceFilter(
        choices=BookEvent.PaymentType.choices,
        method="filter_payment_type",
    )
    paid_or_free = django_filters.MultipleChoiceFilter(
        choices=BookEvent.PaidOrFree.choices,
        method="filter_paid_or_free",
    )
    ticket_tier = django_filters.MultipleChoiceFilter(
        choices=BookEvent.TicketTier.choices,
        method="filter_ticket_tier",
    )

    # Date Ranges
    request_date_from = django_filters.DateFilter(field_name="invoice__request_date", lookup_expr="gte")
    request_date_to   = django_filters.DateFilter(field_name="invoice__request_date", lookup_expr="lte")
    invoice_date_from = django_filters.DateFilter(field_name="invoice__invoice_date", lookup_expr="gte")
    invoice_date_to   = django_filters.DateFilter(field_name="invoice__invoice_date", lookup_expr="lte")
    payment_date_from = django_filters.DateFilter(field_name="invoice__payment_date", lookup_expr="gte")
    payment_date_to   = django_filters.DateFilter(field_name="invoice__payment_date", lookup_expr="lte")

    def _effective_filter(self, queryset, delegate_field, invoice_field, values):
        """Filter on effective value: delegate override if set, else invoice value."""
        if not values:
            return queryset
        q = Q()
        for v in values:
            q |= (
                Q(**{f"{delegate_field}__iexact": v}) |
                Q(**{f"{delegate_field}__isnull": True, f"{invoice_field}__iexact": v}) |
                Q(**{f"{delegate_field}": "", f"{invoice_field}__iexact": v})
            )
        return queryset.filter(q)

    def filter_payment_status(self, queryset, name, value):
        return self._effective_filter(
            queryset, "delegate_payment_status", "invoice__payment_status", value
        )

    def filter_payment_type(self, queryset, name, value):
        return self._effective_filter(
            queryset, "delegate_payment_type", "invoice__payment_type", value
        )

    def filter_paid_or_free(self, queryset, name, value):
        # `value` is now a list (MultipleChoiceFilter); _effective_filter ORs across it.
        return self._effective_filter(
            queryset, "delegate_paid_or_free", "invoice__paid_or_free", value
        )

    def filter_ticket_tier(self, queryset, name, value):
        return self._effective_filter(
            queryset, "delegate_ticket_tier", "invoice__ticket_tier", value
        )

    def filter_attendance(self, queryset, name, value):
        if not value:
            return queryset
        val_lower = value.lower()
        if val_lower == "yes":
            return queryset.filter(attendance="Confirmed")
        elif val_lower == "no":
            return queryset.exclude(attendance="Confirmed")
        return queryset

    def filter_edition(self, queryset, name, value):
        if not value:
            return queryset
        val_str = str(value).strip()
        if not val_str:
            return queryset
        try:
            year = int(val_str)
            if year < 100:
                year += 2000
            return queryset.filter(edition=year)
        except ValueError:
            return queryset

    class Meta:
        model  = BookDelegate
        fields = [
            "first_name", "last_name", "email", "position", "booking_code",
            "invoice_number", "event_code", "edition", "payment_status", "payment_type",
            "paid_or_free", "ticket_tier", "attendance", "company", "company_name",
            "request_date_from", "request_date_to",
            "invoice_date_from", "invoice_date_to",
            "payment_date_from", "payment_date_to",
            "delegate_count", "discount",
        ]
