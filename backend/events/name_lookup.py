"""
events/name_lookup.py
──────────────────────
One query for every event name a serialised page needs, instead of one per row.

THE PROBLEM THIS REPLACES
Paper Review and Proposal Submission both store the event as a CharField code,
not an FK, so select_related cannot reach the catalogue. Both serialisers
therefore resolved the display name with a SerializerMethodField that ran

    Event.objects.filter(event_code__iexact=obj.event_code).first()

once PER ROW. Measured on this database, a single 500 row page of
/api/paper-reviews/ issued 503 queries, 500 of them that lookup, and the
frontend walks every page, so a full load of 7,080 reviews cost 7,080 of them.
The database time was negligible; the cost was 7,080 Python round trips.

WHY A WHOLE MAP RATHER THAN A BETTER PER ROW LOOKUP
The catalogue is small and bounded, one row per event ever run, so fetching
every code and name once is cheaper than any indexed per row query could be.
It also sidesteps the fact that __iexact compiles to UPPER(event_code) = UPPER(%s),
which the plain event_code index cannot serve, so each of those lookups was a
sequential scan.

SHARED, NOT COPIED
Both apps had the identical three line method; a fix to one had to be found in
the other. This module is the single copy, following the same reasoning that
moved _coerce_number into accounts/bulk_update.py.
"""
from .models import Event


class EventNameMixin:
    """
    Adds `event_name`, resolved from a stored event_code STRING, for serialisers
    that declare `event_name = serializers.SerializerMethodField()`.

    SCOPE OF THE CACHE
    The map is built lazily and held on the serialiser INSTANCE, so it lives
    exactly as long as one response. `many=True` builds one child serialiser that
    renders every row, which is what makes a single build cover a whole page; a
    create or update response builds it for its one row and discards it. Nothing
    is cached process wide, so an event renamed in the admin shows its new name
    on the very next request rather than whenever a shared cache happened to
    expire.

    MATCHING IS UNCHANGED
    Keys are upper cased on both sides, which is what __iexact did, so a row
    whose stored code differs from the catalogue only in case still resolves.
    Where two catalogue rows collide case insensitively the first in Event's own
    Meta ordering wins, which is the row .first() returned before.
    """

    def _event_name_map(self):
        cache = getattr(self, "_event_name_cache", None)
        if cache is None:
            cache = {}
            # values_list on the default manager keeps Event.Meta.ordering, so
            # setdefault preserves the row .first() used to pick on a collision.
            for code, name in Event.objects.values_list("event_code", "name"):
                cache.setdefault((code or "").upper(), name)
            self._event_name_cache = cache
        return cache

    def get_event_name(self, obj):
        return self._event_name_map().get((obj.event_code or "").upper(), "")
