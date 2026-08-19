import hashlib

from django.core.cache import cache
from django.core.paginator import Paginator
from django.utils.functional import cached_property
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class CachedCountPaginator(Paginator):
    """
    Paginator whose COUNT(*) is memoised per query signature.

    WHY
    DRF reads paginator.count on EVERY page of a listing, so scrolling Ticket
    Central to row 5,000 pays a hundred identical COUNT(*) over ~50,000 rows,
    and every background poll pays another. The number cannot differ between
    pages of the same filtered query; recomputing it per page is pure waste and
    on measured workloads is roughly half the database time of a scroll.

    KEYING
    The key is the queryset's compiled SQL with ORDER BY stripped (ordering
    cannot change a count but does change the SQL text, and the same filter
    under two sorts must share one entry), plus its params (two different
    filter_specs must never share an entry). RBAC scoping is inside the SQL,
    so two users with different scopes key differently by construction.

    STALENESS
    A stale count is visible only as the footer reading e.g. "Showing 60 of 58"
    for at most CACHE_SECONDS. Row correctness is untouched; only the total is
    cached. 30s matches the fastest poll interval in the frontend.

    FALLBACK
    Any queryset that cannot render its own SQL (values querysets with unusual
    expressions, unions) falls back to stock counting rather than failing the
    request. Small results skip the cache entirely — below MIN_ROWS_TO_CACHE
    the COUNT is cheaper than it is worth remembering.
    """
    CACHE_SECONDS = 30
    MIN_ROWS_TO_CACHE = 2000

    @cached_property
    def count(self):
        try:
            sql, params = self.object_list.order_by().query.sql_with_params()
        except Exception:
            return super().count

        key = "pgcount:" + hashlib.sha1(
            (sql + "|" + repr(params)).encode()
        ).hexdigest()

        hit = cache.get(key)
        if hit is not None:
            return hit

        value = self.object_list.count()
        if value >= self.MIN_ROWS_TO_CACHE:
            cache.set(key, value, self.CACHE_SECONDS)
        return value


class StandardPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 500
    django_paginator_class = CachedCountPaginator

    def get_paginated_response(self, data):
        return Response({
            "count":       self.page.paginator.count,
            "total_pages": self.page.paginator.num_pages,
            "page":        self.page.number,
            "page_size":   self.get_page_size(self.request),
            "next":        self.get_next_link(),
            "previous":    self.get_previous_link(),
            "results":     data,
        })
