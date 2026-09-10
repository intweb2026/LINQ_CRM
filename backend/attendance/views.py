"""
attendance/views.py
────────────────────
GET  /api/attendance/           the arrival log, paginated
GET  /api/attendance/events/    the events this caller may work
GET  /api/attendance/roster/    the confirmed roster for one event, with tokens
GET  /api/attendance/summary/   expected against arrived
POST /api/attendance/scan/      validate a badge and check somebody in
POST /api/attendance/qr_token/  mint one printable badge token

A ReadOnlyModelViewSet, deliberately. The log is append-only-BY-SCAN: there is
no create, update or destroy route on it at all, so POST, PATCH and DELETE
against the collection answer 405 and the only way a row appears is scan/.

GATING. crm_permission("attendance") throughout, and its HTTP-method fallback is
the right answer for every action here without adding any of these names to the
tables in accounts/crm_permissions.py; the GETs need view, the two POSTs need
create. So "may read the log" and "may work the door" are two cells of one
module, which is what lets a supervisor watch arrivals without being able to
record them.

A SCAN MARKS PRESENT IMMEDIATELY. There is no confirmation step on the camera
path, because a queue at a door does not wait for anybody to press Yes. The one
confirmation in this module is on the MANUAL path, in the frontend, and the
server re-verifies everything either way.

`roster` is both this module's data helper and the name of an action below.
There is no clash at runtime: a method body resolves `roster` through the module
globals, and class attributes are not on that lookup chain.
"""
import logging

from django.db import IntegrityError, transaction
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.crm_permissions import crm_permission
from book_delegate.models import BookDelegate

from . import access, qr, roster
from .models import AttendanceRecord
from .serializers import AttendanceRecordSerializer

logger = logging.getLogger(__name__)

MODULE = "attendance"


def _outcome(code, detail, http_status, **extra):
    return Response({"result": code, "detail": detail, **extra}, status=http_status)


class AttendanceViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [crm_permission(MODULE)]
    serializer_class = AttendanceRecordSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter,
                       filters.OrderingFilter]
    filterset_fields = ["event_code", "edition", "attendee_type", "status", "source"]
    search_fields = ["attendee_name", "attendee_email", "company_name"]
    ordering_fields = ["checked_in_at", "attendee_name", "company_name", "event_code"]
    ordering = ["-checked_in_at"]

    def get_queryset(self):
        """
        THE SCOPE GOES HERE, on the base queryset, before any filter backend
        runs. A hand-written ?event_code= naming an event this caller may not
        work then INTERSECTS with the scope and matches nothing, rather than
        being applied instead of it.
        """
        return access.scope_records(
            AttendanceRecord.objects.select_related("checked_in_by"),
            self.request.user,
        )

    # ── the caller's own scope, as a list ────────────────────────────────────

    @action(detail=False, methods=["get"])
    def events(self, request):
        """
        The events this caller may work, in the shape the Pre-Event Docs event
        picker already reads.

        An EMPTY list for a non-admin is an assignment problem rather than an
        empty database, and the frontend says so in those words.
        """
        return Response(access.permitted_events(request.user, roster.events()))

    # ── one event ────────────────────────────────────────────────────────────

    def _event(self, request):
        """
        The (event_code, edition) this request is about, or a refusal Response.

        A KNOWN event and a PERMITTED one are two different refusals and carry
        two different codes, because only the second is something an operator
        can do anything about.
        """
        params = request.query_params if request.method == "GET" else request.data
        event_code = (params.get("event_code") or "").strip()
        raw_edition = params.get("edition")
        edition = None
        if raw_edition not in (None, "", "null"):
            try:
                edition = int(raw_edition)
            except (TypeError, ValueError):
                return None, None, _outcome(
                    "invalid_event", "Edition must be a whole number.",
                    status.HTTP_400_BAD_REQUEST)

        if not event_code or not roster.exists(event_code, edition):
            return None, None, _outcome(
                "invalid_event", "That is not an event in this CRM.",
                status.HTTP_400_BAD_REQUEST)
        if not access.may_work_event(request.user, event_code, edition):
            return None, None, _outcome(
                "forbidden_event", "You are not assigned to that event.",
                status.HTTP_403_FORBIDDEN)
        return event_code, edition, None

    @action(detail=False, methods=["get"])
    def roster(self, request):
        """
        The confirmed roster for one event, each row carrying the badge token
        that checks that person in.

        THE TOKENS ARE WHY THIS IS GATED AS TIGHTLY AS scan/. A roster that
        minted tokens for an event the caller may not work would be an oracle
        for badges they cannot legitimately scan, so the event check runs before
        a single row is read.

        `search` is applied over the projected rows rather than as a queryset
        filter, because the confirmed set is decided in Python by at_desk() and
        a second, SQL-shaped copy of that rule is exactly the drift this module
        exists to avoid. One event is hundreds of rows.
        """
        event_code, edition, refusal = self._event(request)
        if refusal:
            return refusal

        delegates = roster.confirmed(event_code, edition)
        arrived = {
            record.delegate_id: record
            for record in AttendanceRecord.objects.filter(
                delegate_id__in=[d.id for d in delegates])
        }
        rows = [roster.row(d, arrived.get(d.id)) for d in delegates]

        term = (request.query_params.get("search") or "").strip()
        if term:
            rows = [r for r in rows if roster.matches(r, term)]
        rows.sort(key=lambda r: (r["company_name"].casefold(),
                                 r["attendee_name"].casefold()))
        return Response({"event_code": event_code, "edition": edition,
                         "count": len(rows), "results": rows})

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """Expected against arrived, for one event."""
        event_code, edition, refusal = self._event(request)
        if refusal:
            return refusal

        delegates = roster.confirmed(event_code, edition)
        records = AttendanceRecord.objects.filter(
            delegate_id__in=[d.id for d in delegates])
        expected = len(delegates)
        arrived = records.count()
        speakers = sum(1 for d in delegates
                       if roster.attendee_type(d) == "speaker")
        return Response({
            "event_code": event_code,
            "edition": edition,
            "expected": expected,
            "arrived": arrived,
            "outstanding": expected - arrived,
            "expected_speakers": speakers,
            "expected_delegates": expected - speakers,
            "last_arrival": records.order_by("-checked_in_at")
                                   .values_list("checked_in_at", flat=True)
                                   .first(),
        })

    # ── the two writes ───────────────────────────────────────────────────────

    @action(detail=False, methods=["post"], url_path="qr_token")
    def qr_token(self, request):
        """
        Mint one printable badge token.

        BOTH halves are checked, and that is the whole of this endpoint: an
        event the caller may work, AND an attendee genuinely on that event's
        confirmed roster. Without the second it is an oracle, fed ids and read
        back for which of them exist.
        """
        event_code, edition, refusal = self._event(request)
        if refusal:
            return refusal

        delegate = roster.confirmed_delegate(request.data.get("delegate_id"))
        if delegate is None or not _same_event(delegate, event_code, edition):
            return _outcome("invalid_qr", "Nobody on that event has that id.",
                            status.HTTP_400_BAD_REQUEST)
        return Response(roster.row(delegate))

    @action(detail=False, methods=["post"])
    def scan(self, request):
        """
        Validate a badge and check somebody in.

        THE ORDER OF THE CHECKS IS THE FEATURE, and it is this:

            1  payload readable                invalid_qr          400
            2  the device's event is real      invalid_event       400
            3  the caller may scan it          forbidden_event     403
            4  the badge names a real,
               confirmed attendee              invalid_qr          400
            5  that attendee is on THIS event  wrong_event         409
            6  already recorded                already_checked_in  200
            7  record it                       checked_in          201

        STEP 3 BEFORE STEP 4, deliberately. Reversed, an out-of-scope probe
        could tell an existing id from a missing one by which refusal came back,
        so the identity lookup happens only for somebody already entitled to
        that door.

        STEP 5 COMPARES THE ROSTER ROW'S EVENT, not the code inside the QR. A
        printed payload can be stale, because a transfer moves somebody between
        events and the badge in their pocket does not change; the roster is the
        record.

        already_checked_in IS 200 AND NOT AN ERROR. Scanning the same badge
        twice at a busy door is normal, so it answers with the EXISTING row and
        its original arrival time. The desk needs to see when they came in, not
        a red screen that trains staff to turn people away.
        """
        # 1 ── is there anything readable in the payload at all
        badge = qr.read(request.data.get("payload") or request.data.get("token"))
        if badge is None:
            return _outcome("invalid_qr", "That code could not be read.",
                            status.HTTP_400_BAD_REQUEST)

        # 2, 3 ── the device's own event, and whether this caller may work it
        event_code, edition, refusal = self._event(request)
        if refusal:
            return refusal

        # 4 ── does the badge name a real person on a confirmed roster
        delegate = roster.confirmed_delegate(badge["id"])
        if delegate is None:
            return _outcome(
                "invalid_qr",
                "That badge does not match anybody on a confirmed roster.",
                status.HTTP_400_BAD_REQUEST)

        # 5 ── is that person on THIS event
        if not _same_event(delegate, event_code, edition):
            return _outcome("wrong_event",
                            _wrong_event_detail(request.user, delegate),
                            status.HTTP_409_CONFLICT)

        # 6, 7 ── record it, and let the DATABASE settle the duplicate
        record, created = _check_in(delegate, request)
        serialized = AttendanceRecordSerializer(record).data
        if not created:
            return _outcome("already_checked_in",
                            f"{record.attendee_name} is already checked in.",
                            status.HTTP_200_OK, record=serialized)
        logger.info("attendance check-in %s (%s %s) by %s",
                    record.attendee_name, event_code, edition, request.user)
        return _outcome("checked_in", f"{record.attendee_name} is checked in.",
                        status.HTTP_201_CREATED, record=serialized)


def _same_event(delegate, event_code, edition):
    """
    Case-insensitive on the code, exact on the edition.

    Whole-string and never a substring test: the two sides of this comparison
    disagree about case ("Feb2027_BIZ-PM" against "FEB2027_BIZ-PM"), and a
    substring test would match "SFU - AD" inside "BSFU - AD", a different event.
    """
    return ((delegate.event_code or "").strip().casefold()
            == (event_code or "").strip().casefold()
            and delegate.edition == edition)


def _wrong_event_detail(user, delegate):
    """
    Name the attendee's real event ONLY if this caller is entitled to it.

    Otherwise the refusal tells somebody working one door which other event a
    stranger is booked onto, which is a fact about a person they have no claim
    on.
    """
    if access.may_work_event(user, delegate.event_code, delegate.edition):
        where = " ".join(str(part) for part in
                         (delegate.event_code, delegate.edition) if part)
        return f"That badge belongs to {where}, not this event."
    return "That badge belongs to a different event."


def _check_in(delegate, request):
    """
    Write the arrival, and tick the person IN on the check-in sheet.

    THE UNIQUE CONSTRAINT IS WHAT PREVENTS THE DUPLICATE, not the SELECT inside
    get_or_create. Two turnstiles scanning one badge in the same instant both
    read "not seen" and both insert; one wins, the other raises IntegrityError,
    and the loser must report already_checked_in rather than a 500.

    transaction.atomic() IS REQUIRED, not decorative. On PostgreSQL a failed
    INSERT poisons the whole transaction, so without the savepoint the recovery
    read below would fail too. The block is INNER so that rolling back to the
    savepoint leaves the connection usable.

    THE SECOND WRITE is a deliberate departure from the original brief, which
    said a scan must touch nothing outside its own table. The instruction since
    given is that marking somebody present has to tick the IN? column on the
    Pre-Event Docs check-in sheet and the Attendance column on Bookings. Those
    are ONE field, book_delegates.attendance, so this is one write and not two,
    and it is the same write PATCH /api/delegates/{id}/update_attendance/
    performs, down to the update_fields, so the CRM still has one way to record
    the fact rather than two.
    """
    fields = roster.row(delegate)
    try:
        with transaction.atomic():
            record, created = AttendanceRecord.objects.get_or_create(
                delegate=delegate,
                defaults={
                    "event_code": delegate.event_code,
                    "edition": delegate.edition,
                    "attendee_type": fields["attendee_type"],
                    "attendee_name": fields["attendee_name"],
                    "attendee_email": fields["attendee_email"],
                    "company_name": fields["company_name"],
                    "status": AttendanceRecord.CHECKED_IN,
                    "checked_in_by": request.user,
                    "source": (AttendanceRecord.Source.MANUAL
                               if request.data.get("source") == "manual"
                               else AttendanceRecord.Source.QR_SCAN),
                },
            )
    except IntegrityError:
        record, created = AttendanceRecord.objects.get(delegate=delegate), False

    # Idempotent, and outside the created / not-created branch on purpose: an
    # arrival row that exists while the tick does not is a state the desk has to
    # be able to heal by simply scanning again.
    if delegate.attendance != BookDelegate.Attendance.CONFIRMED:
        delegate.attendance = BookDelegate.Attendance.CONFIRMED
        delegate.save(update_fields=["attendance", "updated_at"])
    return record, created
