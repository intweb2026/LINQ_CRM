"""
pre_event_docs/views.py
────────────────────────
GET    /api/pre-event-docs/events/            the event picker
GET    /api/pre-event-docs/run/<run_id>/      one frozen snapshot, row by row
GET    /api/pre-event-docs/docs/?event_code=&edition=
                                              THE WHOLE PAGE, one request
POST   /api/pre-event-docs/badge-run/         log a badge run
DELETE /api/pre-event-docs/badge-run/<run_id>/  undo one
POST   /api/pre-event-docs/draw/              draw a networking plan

WHY ONE READ ENDPOINT AND NOT SIX
All five tabs read the same filtered rows. Six endpoints would run the same
query six times per page load and give five chances for the tabs to disagree
about who is attending, which is precisely the failure the workbook had when a
formula on one tab was edited and the others were not.

The networking plan is NOT part of that payload. It is stored rather than
derived, so it is read with the page but written only when somebody asks for a
draw; see models.NetworkingPlan for why a random draw has to be frozen.
"""
import logging

from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.crm_permissions import crm_permission

from . import services
from .models import BadgeIssue, NetworkingPlan
from .networking import DEFAULT_ROUNDS, build_plan, suggested_table_count
from .serializers import NetworkingPlanSerializer

logger = logging.getLogger(__name__)

MODULE = "pre_event_docs"


class PreEventDocsViewSet(viewsets.ViewSet):
    """
    Every action is gated on the `pre_event_docs` module.

    None of these four action names is in crm_permission's action tables, so all
    four resolve through its HTTP METHOD fallback, which is the correct answer
    for every one of them. GET reads, so `docs` and `events` need view. POST
    writes, so `badge_run` and `draw` need create. DELETE removes, so
    `undo_badge_run` needs delete. Adding them to the tables in
    accounts/crm_permissions.py would restate what the method already says; the
    tables exist for actions whose NAME disagrees with their method, as
    bulk_update does by being a POST that updates.
    """
    permission_classes = [crm_permission(MODULE)]

    def _scope(self, request):
        """The event this request is about, plus the edition when one is given."""
        event_code = (request.query_params.get("event_code")
                      or request.data.get("event_code") or "").strip()
        raw_edition = (request.query_params.get("edition")
                       or request.data.get("edition") or "")
        edition = None
        if str(raw_edition).strip():
            try:
                edition = int(raw_edition)
            except (TypeError, ValueError):
                edition = None
        return event_code, edition

    @action(detail=False, methods=["get"])
    def events(self, request):
        return Response(services.pickable_events())

    @action(detail=False, methods=["get"])
    def docs(self, request):
        event_code, edition = self._scope(request)
        if not event_code:
            return Response({"detail": "event_code is required."},
                            status=status.HTTP_400_BAD_REQUEST)

        # THE ONE FETCH, the whole event population. The badge tabs use it as
        # it is; check_in and the take out list narrow it with at_desk().
        rows = list(services.event_queryset(event_code, edition))
        meta = services.event_meta(event_code, edition)
        additional, cancellations = services.badge_changes(
            rows, event_code, edition, event_date=meta["event_date"])
        plan = (NetworkingPlan.objects
                .filter(event_code__iexact=event_code, **({"edition": edition} if edition else {}))
                .first())
        # Counted once off the rows already in memory, and read twice below.
        seated = sum(1 for r in rows if services.at_desk(r))

        return Response({
            "event_code": event_code,
            "edition": edition,
            **meta,
            # The freeze date the change lists are measured against, sent once
            # rather than repeated on every row.
            "change_deadline": services.change_deadline(meta["event_date"]),
            # Seeds the table count field, so an untouched draw matches what the
            # workbook would have produced. NOT the count itself; see build_plan.
            # Seeded off the people who will actually be in the room, not off
            # everybody holding a badge.
            "suggested_tables": suggested_table_count(seated),
            # The same head count, sent as itself. The page keeps the tables and
            # the people-per-table fields in step with each other, and it cannot
            # do that arithmetic without knowing how many people there are before
            # the first draw has been made.
            "networking_attendees": seated,
            # THE FOUR REPORTS, keyed as the spec names them.
            "name_badges": services.name_badges(rows),
            "check_in": services.check_in(rows),
            "additional": additional,
            # Kept APART from `additional`, as the workbook keeps them: one list
            # is badges to print, the other badges to take off the table.
            "cancellations": cancellations,
            "runs": services.badge_runs(event_code, edition),
            "networking": NetworkingPlanSerializer(plan).data if plan else None,
            # The page labels the change lists with it, so it comes from the
            # server rather than being repeated as a constant in the frontend.
            "change_window_days": services.change_window_days(),
        })

    @action(detail=False, methods=["post"], url_path="badge-run")
    def badge_run(self, request):
        """
        Log a badge run.

        The caller sends the delegate ids it just produced badges for, and the
        printed name and company are stored AS SENT rather than re-read here.
        That is deliberate: what went on the badge is what the page was showing,
        and re-reading the delegate would record whatever the row says at the
        moment the button was pressed, which is the thing the diff exists to
        detect a difference from.
        """
        event_code, edition = self._scope(request)
        badges = request.data.get("badges") or []
        if not event_code:
            return Response({"detail": "event_code is required."},
                            status=status.HTTP_400_BAD_REQUEST)
        if not badges:
            return Response({"detail": "No badges to log."},
                            status=status.HTTP_400_BAD_REQUEST)

        run = BadgeIssue(event_code=event_code, edition=edition)
        rows = [
            BadgeIssue(
                delegate_id=b.get("delegate_id"),
                event_code=event_code,
                edition=edition,
                name=(b.get("name") or "").strip()[:255],
                company=(b.get("company") or "").strip()[:255],
                run_id=run.run_id,
                issued_at=run.issued_at,
                issued_by=request.user,
            )
            for b in badges
        ]
        with transaction.atomic():
            BadgeIssue.objects.bulk_create(rows)

        logger.info("pre_event_docs badge run %s, %s badges, %s by %s",
                    run.run_id, len(rows), event_code, request.user)
        return Response({"run_id": str(run.run_id), "badges": len(rows),
                         "issued_at": run.issued_at},
                        status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="run/(?P<run_id>[^/.]+)")
    def run(self, request, run_id=None):
        """
        GET the frozen snapshot of one badge run.

        Exists so a run can be VERIFIED rather than trusted: the rows as frozen,
        beside what the booking says today, with a drift label per row. See
        services.badge_run_detail.
        """
        rows = services.badge_run_detail(run_id)
        if not rows:
            return Response({"detail": "No such badge run."},
                            status=status.HTTP_404_NOT_FOUND)
        return Response({"run_id": run_id, "badges": rows})

    @action(detail=False, methods=["delete"], url_path="badge-run/(?P<run_id>[^/.]+)")
    def undo_badge_run(self, request, run_id=None):
        """Undo one run. The reason run_id exists; see models.BadgeIssue."""
        deleted, _ = BadgeIssue.objects.filter(run_id=run_id).delete()
        if not deleted:
            return Response({"detail": "No such badge run."},
                            status=status.HTTP_404_NOT_FOUND)
        logger.info("pre_event_docs badge run %s undone by %s", run_id, request.user)
        return Response({"deleted": deleted}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"])
    def draw(self, request):
        """
        Draw a networking plan and store it.

        A new row every time rather than an update, so a draw already printed
        stays readable after somebody asks for another.
        """
        event_code, edition = self._scope(request)
        if not event_code:
            return Response({"detail": "event_code is required."},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            tables = int(request.data.get("tables") or 0)
            rounds = int(request.data.get("rounds") or DEFAULT_ROUNDS)
            # How many people can sit at one table. A maximum, and optional;
            # see build_plan for what it does alongside a table count.
            per_table = int(request.data.get("per_table") or 0)
        except (TypeError, ValueError):
            return Response({"detail": "Tables, seats and rounds must be whole numbers."},
                            status=status.HTTP_400_BAD_REQUEST)
        if tables and not 1 <= tables <= 200:
            return Response({"detail": "Tables must be 1 to 200."},
                            status=status.HTTP_400_BAD_REQUEST)
        if per_table and not 1 <= per_table <= 200:
            return Response({"detail": "People per table must be 1 to 200."},
                            status=status.HTTP_400_BAD_REQUEST)
        if not 1 <= rounds <= 6:
            return Response({"detail": "Rounds must be 1 to 6."},
                            status=status.HTTP_400_BAD_REQUEST)

        # SEATED FROM THE DESK POPULATION, not the badge population. The
        # workbook's Input tab is pasted by hand so it settles nothing, and
        # seating somebody whose booking is unpaid or cancelled would hold a
        # chair for a person the door will turn away.
        rows = list(services.event_queryset(event_code, edition))
        people = [
            {"id": d.id,
             "name": f"{d.first_name} {d.last_name}".strip(),
             "company": services.company_of(d)}
            for d in rows if services.at_desk(d) and not services.is_tba(d)
        ]
        if not people:
            return Response({"detail": "Nobody is registered for this event yet."},
                            status=status.HTTP_400_BAD_REQUEST)

        # The room the caller described cannot hold the list. Its own words,
        # because they name the two numbers that would work.
        try:
            drawn = build_plan(people, tables=tables or None,
                               per_table=per_table or None, rounds=rounds)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        plan = NetworkingPlan.objects.create(
            event_code=event_code,
            edition=edition,
            rounds=rounds,
            tables=drawn["tables"],
            repeat_pairs=drawn["repeat_pairs"],
            floor_repeats=drawn["floor_repeats"],
            assignment=drawn["assignment"],
            created_by=request.user,
        )
        return Response(NetworkingPlanSerializer(plan).data,
                        status=status.HTTP_201_CREATED)
