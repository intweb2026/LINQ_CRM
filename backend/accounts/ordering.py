"""
accounts/ordering.py
─────────────────────
Stable ordering for paginated list endpoints.

THE BUG THIS FIXES
Every list endpoint sorts by a non-unique column — Bookings by
`-_sort_request_date` (invoice__request_date, heavily tied), Ticket Central by
`-created_at`, Events by `-event_date`. SQL guarantees no particular order
among rows that tie, and Postgres is free to return them differently for each
query. With LIMIT/OFFSET pagination that means consecutive pages can overlap
and, worse, SKIP rows entirely.

Measured on Bookings before this change: page 1 and page 2 shared 10 rows
unfiltered and 2 filtered. Ten shared rows means ten other rows were never
returned at all — a rep scrolling through work would never see them and would
have no way to tell.

THE FIX
Append the primary key as a final tiebreaker to whatever ordering is in effect,
default or user-selected. The visible sort is unchanged; ties simply resolve
deterministically instead of arbitrarily.

Applied by swapping this in for rest_framework.filters.OrderingFilter, so it
covers every list endpoint rather than only the three that prompted it.
"""
from django.db.models import F
from rest_framework.filters import OrderingFilter

_PK_ALIASES = {"pk", "id"}


class StableOrderingFilter(OrderingFilter):
    """OrderingFilter that always ends with a unique tiebreaker."""

    def get_ordering(self, request, queryset, view):
        ordering = list(super().get_ordering(request, queryset, view) or [])

        # Already deterministic if the caller (or the default) sorts by the pk.
        if any(term.lstrip("-") in _PK_ALIASES for term in ordering):
            return ordering

        ordering.append("pk")
        return ordering

    def filter_queryset(self, request, queryset, view):
        """Order the queryset, sending NULLs last on the fields that ask for it.

        THE SECOND BUG THIS FIXES
        Postgres orders NULLs FIRST on a DESC sort. Every date column here is
        nullable — Date Paid, Request Date, Invoice Date, the two submission
        dates — so "newest first" opened with a solid block of empty cells and
        the actual newest rows sat below however many undated rows the table
        held. Paper Review and Proposal Submission shipped that as their
        DEFAULT ordering.

        An undated row has no position on a timeline, so it belongs at the end
        of the list in EITHER direction; that is also what the browser-side
        twin does (frontend/src/components/DataTable.jsx sortLocally).

        Opt-in per view via `nulls_last_ordering_fields` rather than applied to
        everything, because NULLS LAST on a DESC sort does not match a plain
        DESC index and would cost a full sort on columns that are never null
        anyway — `-created_at` on Bookings is served by an index and stays a
        plain string term.
        """
        ordering = self.get_ordering(request, queryset, view)
        if not ordering:
            return queryset

        nulls_last = set(getattr(view, "nulls_last_ordering_fields", None) or ())
        if not nulls_last:
            return queryset.order_by(*ordering)

        terms = []
        for term in ordering:
            field = term[1:] if term.startswith("-") else term
            if field not in nulls_last:
                terms.append(term)
                continue
            expr = F(field)
            terms.append(
                expr.desc(nulls_last=True) if term.startswith("-")
                else expr.asc(nulls_last=True)
            )
        return queryset.order_by(*terms)
