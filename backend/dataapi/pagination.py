"""
dataapi/pagination.py
──────────────────────
Keyset/cursor pagination, not page-number pagination.

A Google Apps Script run is capped at six minutes. Page-number pagination would
force the client to restart from page 1 on every continuation, and OFFSET grows
more expensive the deeper the sync gets. A cursor is opaque and absolute: the
client saves the `next` URL, the continuation trigger resumes from exactly that
row, and no row is duplicated or skipped if writes land mid-sync.
"""
from rest_framework.pagination import CursorPagination


class DataApiCursorPagination(CursorPagination):
    page_size = 200
    page_size_query_param = "page_size"
    max_page_size = 500
    # pk is the only ordering guaranteed unique and monotonic on every resource
    # here, which is what keyset pagination requires.
    ordering = "pk"
