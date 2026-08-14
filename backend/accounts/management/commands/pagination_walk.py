"""
manage.py pagination_walk — exhaustive proof that paginated lists neither
duplicate nor omit rows.

WHY THIS EXISTS SEPARATELY FROM live_smoke
live_smoke's pagination check is deliberately shallow (a few pages) so it stays
fast enough to gate a deploy. Shallow walks cannot prove the thing that matters:
OFFSET drift is worst on DEEP pages, because a non-unique sort lets Postgres
reshuffle tied rows and the window slides over rows it already returned. This
command walks EVERY page of a surface and compares the union of ids against the
true row count, which catches both halves of the bug at once:

    len(all_ids)      < total  →  rows were OMITTED (the dangerous half)
    len(set(all_ids)) < len(all_ids)  →  rows were DUPLICATED

Omission is what a shallow walk misses and what a user never notices: they
scroll what looks like the whole list and simply never see certain rows.

    python manage.py pagination_walk                    # the full matrix
    python manage.py pagination_walk --only delegates-status
    python manage.py pagination_walk --page-size 50

Read-only: the only view mapping it ever binds is {"get": "list"}. It issues no
POST, PATCH or DELETE and opens no transaction.

Exit status is 1 if any walk mismatches, so it can gate a deploy script.
"""
import json
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from rest_framework.test import APIRequestFactory, force_authenticate

from book_delegate.views import BookDelegateViewSet
from events.views import EventViewSet
from ticket_central.views import TicketViewSet

User = get_user_model()

PAID_SPEC = {"match": "all", "criteria": [
    {"field": "payment_status", "op": "is", "value": "Paid"}]}

# (key, label, viewset, path, ordering, filter_spec, expected)
#
# `ordering` of None means "exercise the endpoint's own default". The named
# orderings are columns the UI actually offers as clickable headers, chosen for
# being heavily tied — _sort_status is invoice__payment_status, roughly five
# distinct values spread over every delegate row.
WALKS = [
    ("delegates",        "Bookings, default ordering",
     BookDelegateViewSet, "/api/delegates/", None, None, 13264),
    ("tickets",          "Ticket Central, default ordering",
     TicketViewSet, "/api/tickets/", None, None, 35690),
    ("events",           "Events, default ordering",
     EventViewSet, "/api/events/", None, None, 142),
    ("delegates-paid",   "Bookings, filter_spec payment_status is Paid",
     BookDelegateViewSet, "/api/delegates/", None, PAID_SPEC, 8489),
    ("delegates-status", "Bookings, ordering=_sort_status (UI 'Status' header)",
     BookDelegateViewSet, "/api/delegates/", "_sort_status", None, 13264),
    ("delegates-status-desc", "Bookings, ordering=-_sort_status (same header, desc)",
     BookDelegateViewSet, "/api/delegates/", "-_sort_status", None, 13264),
    ("tickets-status",   "Ticket Central, ordering=status (UI 'Status' header)",
     TicketViewSet, "/api/tickets/", "status", None, 35690),
]


class Command(BaseCommand):
    help = "Walk every page of each list surface and prove no row repeats or goes missing."

    def add_arguments(self, parser):
        parser.add_argument("--page-size", type=int, default=50,
                            help="Rows per page (default 50, matching the UI).")
        parser.add_argument("--only", default=None,
                            help="Run a single walk by key. Default: all of them.")
        parser.add_argument("--user", default=None,
                            help="Username to act as. Defaults to HP, then any "
                                 "all-access user.")
        parser.add_argument("--max-pages", type=int, default=5000,
                            help="Runaway guard (default 5000).")
        parser.add_argument("--no-tiebreaker", action="store_true",
                            help="NEGATIVE CONTROL. Monkeypatch the pk tiebreaker "
                                 "off for this process only, so the walk runs "
                                 "against the pre-fix ordering. A walk that cannot "
                                 "fail proves nothing; this is how we show it can. "
                                 "Still read-only, and touches no file on disk.")

    def handle(self, *args, **opts):
        self.factory = APIRequestFactory()

        if opts["no_tiebreaker"]:
            # Process-local only: undone when this process exits, and no source
            # file is modified. Reverts get_ordering to the stock DRF behaviour.
            from rest_framework.filters import OrderingFilter

            from accounts.ordering import StableOrderingFilter
            StableOrderingFilter.get_ordering = OrderingFilter.get_ordering
            self.stdout.write(self.style.WARNING(
                "NEGATIVE CONTROL: pk tiebreaker disabled for this process. "
                "Mismatches below are the EXPECTED, pre-fix behaviour.\n"))

        # Paginated responses build absolute next/previous links from the Host
        # header, and APIRequestFactory sends "testserver". Process-local only.
        if "testserver" not in settings.ALLOWED_HOSTS:
            settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ["testserver"]

        self.user = self._resolve_user(opts["user"])
        self.stdout.write(f"acting as: {self.user.username}")
        self.stdout.write(f"page_size: {opts['page_size']}\n")

        walks = WALKS
        if opts["only"]:
            walks = [w for w in WALKS if w[0] == opts["only"]]
            if not walks:
                raise CommandError(f"no walk named {opts['only']!r}; "
                                   f"try one of {[w[0] for w in WALKS]}")

        failures = []
        for key, label, viewset, path, ordering, spec, expected in walks:
            ok = self._walk(key, label, viewset, path, ordering, spec, expected,
                            opts["page_size"], opts["max_pages"])
            if not ok:
                failures.append(key)
                # 2f: stop on the first mismatch rather than papering over it
                # with the remaining walks' output.
                self.stdout.write(self.style.ERROR(
                    "\nSTOPPING: a walk mismatched. Remaining walks not run."))
                break

        self.stdout.write("\n" + "=" * 70)
        if failures:
            raise CommandError(f"pagination mismatch in: {', '.join(failures)}")
        self.stdout.write(self.style.SUCCESS(
            f"all {len(walks)} walks exhaustive and exact"))

    def _resolve_user(self, username):
        if username:
            user = User.objects.filter(username=username).first()
            if user is None:
                raise CommandError(f"no such user: {username}")
            return user
        user = (User.objects.filter(username="HP").first()
                or User.objects.filter(team__is_all_access=True).first())
        if user is None:
            raise CommandError("no all-access user to act as; pass --user")
        return user

    def _build_url(self, path, page, page_size, ordering, spec):
        url = f"{path}?page={page}&page_size={page_size}"
        if ordering:
            url += f"&ordering={quote(ordering)}"
        if spec:
            url += f"&filter_spec={quote(json.dumps(spec))}"
        return url

    def _effective_order_by(self, viewset, path, ordering, spec, page_size):
        """
        Ask the view what ORDER BY it actually put on the SQL, so the tiebreaker
        is evidenced from the emitted query rather than from reading the class.
        """
        view = viewset(action="list", request=None, format_kwarg=None)
        req = self.factory.get(self._build_url(path, 1, page_size, ordering, spec))
        force_authenticate(req, user=self.user)
        from rest_framework.request import Request
        drf_req = Request(req)
        drf_req.user = self.user
        view.request = drf_req
        view.kwargs = {}
        qs = view.filter_queryset(view.get_queryset())
        return list(qs.query.order_by)

    def _walk(self, key, label, viewset, path, ordering, spec, expected,
              page_size, max_pages):
        self.stdout.write(self.style.HTTP_INFO(f"\n── {key}: {label}"))

        try:
            order_by = self._effective_order_by(viewset, path, ordering, spec, page_size)
            self.stdout.write(f"   effective SQL ORDER BY: {order_by}")
            tail_is_pk = bool(order_by) and order_by[-1].lstrip("-") in ("pk", "id")
            self.stdout.write(
                f"   tiebreaker is final term: {tail_is_pk}"
                if tail_is_pk else
                self.style.ERROR(f"   tiebreaker is final term: {tail_is_pk}"))
        except Exception as exc:                      # noqa: BLE001 - evidence, not control flow
            self.stdout.write(self.style.WARNING(f"   could not read ORDER BY: {exc!r}"))

        view = viewset.as_view({"get": "list"})
        all_ids, total, pages = [], None, 0

        for page in range(1, max_pages + 1):
            req = self.factory.get(self._build_url(path, page, page_size, ordering, spec))
            force_authenticate(req, user=self.user)
            resp = view(req)
            resp.render()
            if resp.status_code == 404:
                break                                  # walked past the last page
            if resp.status_code != 200:
                self.stdout.write(self.style.ERROR(
                    f"   page {page} returned HTTP {resp.status_code}: "
                    f"{resp.content[:300]!r}"))
                return False
            body = json.loads(resp.content)
            total = body["count"]
            all_ids.extend(r["id"] for r in body["results"])
            pages += 1
            if not body.get("next"):
                break
        else:
            self.stdout.write(self.style.ERROR(
                f"   hit --max-pages {max_pages} without exhausting the surface"))
            return False

        uniq = len(set(all_ids))
        self.stdout.write(f"   reported count      : {total}")
        self.stdout.write(f"   total pages walked  : {pages}")
        self.stdout.write(f"   len(all_ids)        : {len(all_ids)}")
        self.stdout.write(f"   len(set(all_ids))   : {uniq}")
        self.stdout.write(f"   expected            : {expected}")

        dupes   = len(all_ids) - uniq
        missing = (total - uniq) if total is not None else None
        self.stdout.write(f"   duplicates          : {dupes}")
        self.stdout.write(f"   omissions vs count  : {missing}")

        ok = (total == expected and len(all_ids) == expected and uniq == expected)
        if ok:
            self.stdout.write(self.style.SUCCESS(
                f"   OK  all three == {expected}, zero duplicates, zero omissions"))
        else:
            self.stdout.write(self.style.ERROR(
                f"   FAIL  count={total} len={len(all_ids)} uniq={uniq} "
                f"expected={expected} ordering={ordering!r}"))
        return ok
