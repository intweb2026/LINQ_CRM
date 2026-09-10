"""
credit_control/views.py
────────────────────────
GET   /api/credit-control/leads/?bucket=active&mine=1   the queue
PATCH /api/credit-control/leads/{invoice number}/       a caller's edit
PATCH /api/credit-control/leads/{invoice number}/invoice-type/   human verdict
GET   /api/credit-control/dashboard/                    the aggregate
GET   /api/credit-control/dispositions/                 the dropdown
POST  /api/credit-control/refresh/                      run the routing pass

ROW SCOPE. `credit_control` is in SCOPED_MODULES, so a caller without the `all`
cell sees only the leads assigned to them; the team lead and anyone with `all`
sees every row. The scope is applied in get_queryset, once, so no action can
forget it.

NO HUBSPOT OR ANTHROPIC CALL HAPPENS IN THIS FILE. Phones and call counts are
read from the cache tables; classification and briefs run as commands. A page
that waits on a third party is a page that hangs when the third party does.
"""
import logging

from django.db.models import Count
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from accounts.crm_permissions import crm_permission
from accounts.permissions import is_super_admin
from book_delegate.models import BookDelegate
from hubspot.models import HubSpotContact

from . import dashboard, engine
from .models import CreditControlDisposition, CreditControlLead, CreditControlTouch
from .serializers import (
    DispositionSerializer, InvoiceTypeOverrideSerializer, LeadSerializer,
    LeadWriteSerializer,
)

logger = logging.getLogger(__name__)

MODULE = "credit_control"


class CreditControlViewSet(ViewSet):
    """
    One viewset for the whole module. The buckets are query parameters rather
    than separate endpoints because they are one table filtered four ways, and
    the frontend switches between them on one page.
    """
    permission_classes = [crm_permission(MODULE)]
    # The pk IS the invoice number, which carries hyphens and may carry dots.
    # The router's default lookup regex excludes dots, which would 404 a real
    # invoice rather than fail loudly, so it is widened to anything but a slash.
    lookup_value_regex = "[^/]+"

    # ── who sees and who edits ──────────────────────────────────────────────
    #
    # THREE SEPARATE QUESTIONS, and conflating any two of them is how a queue
    # becomes editable by the wrong person:
    #
    #   WHAT ROWS CAN I READ?   Everyone's, or only mine. The `all` grid cell.
    #   WHAT ROWS CAN I WRITE?  ONLY MINE, whatever I can read. Admins aside.
    #   WHAT LISTS EXIST FOR ME? Not Invoiced and Sponsors are admin-only.
    #
    # The read and write scopes are deliberately different, which is the whole
    # of the team lead's position: they hold `all`, so they see the team's
    # queues and the full dashboard, and they can still only type into the leads
    # assigned to them. An exec holds no `all`, so they see and edit exactly
    # their own. Neither can touch another caller's remarks.

    # Lists that are not part of anybody's calling day. Not Invoiced is a
    # registry of bookings waiting on an invoice number, and Sponsors is handled
    # by another team entirely, so neither belongs in a caller's rail.
    ADMIN_ONLY_BUCKETS = (
        CreditControlLead.Bucket.NOT_INVOICED,
        CreditControlLead.Bucket.SPEX,
    )

    def _may_see_all(self, request) -> bool:
        if is_super_admin(request.user):
            return True
        resolved = request.user.effective_permissions().get(MODULE) or {}
        return bool(resolved.get("all"))

    def _may_edit(self, request, lead) -> bool:
        """
        Can this caller write to this lead?

        OWNERSHIP, not read scope. A team lead reading the whole team's work is
        the point of `all`; a team lead silently overwriting an exec's remark is
        not, and before this the two were the same test. Admins are exempt
        because somebody has to be able to fix a queue.
        """
        if is_super_admin(request.user):
            return True
        return lead.assigned_to_id == request.user.pk

    def _may_see_bucket(self, request, bucket) -> bool:
        return bucket not in self.ADMIN_ONLY_BUCKETS or is_super_admin(request.user)

    def _queryset(self, request):
        rows = CreditControlLead.objects.select_related("invoice", "assigned_to")
        if not self._may_see_all(request):
            rows = rows.filter(assigned_to=request.user)
        return rows

    # ── read ───────────────────────────────────────────────────────────────

    def list(self, request):
        bucket = request.query_params.get("bucket") or CreditControlLead.Bucket.ACTIVE
        if bucket not in CreditControlLead.Bucket.values:
            return Response(
                {"detail": f"bucket must be one of {', '.join(CreditControlLead.Bucket.values)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not self._may_see_bucket(request, bucket):
            raise PermissionDenied(
                "That list is restricted to administrators."
            )
        rows = self._queryset(request).filter(bucket=bucket)
        if request.query_params.get("mine") == "1":
            rows = rows.filter(assigned_to=request.user)

        # Callbacks that have come due sort to the top, then the oldest debt.
        # This is the whole of the "what do I do next" logic; a notification
        # would be a second answer to a question the sort already answers.
        rows = rows.order_by("callback_date", "invoice__invoice_date")
        leads = list(rows[:2000])
        self._attach(leads)

        return Response({
            "bucket": bucket,
            "count": len(leads),
            "results": LeadSerializer(
                leads, many=True, context=self._context(request, leads),
            ).data,
        })

    def retrieve(self, request, pk=None):
        lead = get_object_or_404(self._queryset(request), pk=pk)
        self._attach([lead])
        self._attach_history([lead])
        return Response(LeadSerializer(lead, context=self._context(request, [lead])).data)

    def _attach(self, leads):
        """
        Hang the delegates onto each lead, in two queries rather than 2N.

        One invoice is one call, so the queue is a list of invoices; the
        delegates are what a row opens to show. Fetching them per row is how
        this endpoint would quietly become slow as the queue grows.
        """
        if not leads:
            return
        keys = [lead.invoice_id for lead in leads]
        grouped = {}
        rows = (
            BookDelegate.objects
            .filter(invoice_id__in=keys)
            .select_related("invoice")
            .order_by("delegate_number", "id")
        )
        for delegate in rows:
            grouped.setdefault(delegate.invoice_id, []).append(delegate)
        for lead in leads:
            found = grouped.get(lead.invoice_id, [])
            lead._delegates = found
            lead._delegate_count = len(found)
            lead._first_delegate = found[0] if found else None

    def _attach_history(self, leads):
        """
        Hang the touch log onto each lead, for the detail views only.

        DELIBERATELY NOT ON THE LIST. The queue renders two hundred rows and
        none of them show a history, so fetching it there would be a second
        query per row for something nobody reads. It is attached where the case
        panel is actually opened.
        """
        if not leads:
            return
        from .models import CreditControlTouch

        grouped = {}
        rows = (
            CreditControlTouch.objects
            .filter(lead__in=[lead.pk for lead in leads])
            .select_related("user")
            .order_by("-created_at")
        )
        for touch in rows:
            grouped.setdefault(touch.lead_id, []).append(touch)
        for lead in leads:
            lead._history = grouped.get(lead.pk, [])[:20]

    def _context(self, request, leads):
        """
        Everything the serializer needs that would otherwise be a per-row query:
        the disposition group map, the HubSpot phone cache, and the viewer's own
        call count per lead.
        """
        emails = set()
        for lead in leads:
            invoice = lead.invoice
            best = (
                invoice.accounts_contact_email or invoice.contact_email or ""
            ).strip().lower()
            if best:
                emails.add(best)
            first = getattr(lead, "_first_delegate", None)
            if first and first.email:
                emails.add(first.email.strip().lower())
        contacts = {
            row.email: row
            for row in HubSpotContact.objects.filter(email__in=emails)
        }
        # HOW MANY TIMES THE VIEWER HAS CALLED EACH OF THESE LEADS. One
        # grouped query for the whole page rather than a count per row, which
        # is the same reason the phone cache above is built here.
        #
        # Scoped to request.user and not to the lead's owner, because the
        # question the caller is asking is "how many times have I rung THIS
        # company", and a handed-over lead carries the previous caller's
        # attempts, which are not theirs.
        my_calls = dict(
            CreditControlTouch.objects
            .filter(lead__in=[lead.pk for lead in leads], user=request.user)
            .values_list("lead_id")
            .annotate(n=Count("id"))
        )
        return {
            "status_groups": engine.status_group_map(),
            "contacts": contacts,
            "my_calls": my_calls,
            # Told to the frontend per row, so a lead somebody else owns renders
            # as text rather than as an input the server would refuse anyway.
            # An affordance that lies is worse than a missing one.
            "may_edit_all": is_super_admin(request.user),
            "viewer_id": request.user.pk,
        }

    # ── write ──────────────────────────────────────────────────────────────

    def partial_update(self, request, pk=None):
        """
        A caller's edit, saved on its own.

        Each field is durable the moment it is typed, which is what makes the
        whole class of "my remark vanished on refresh" bug impossible here: the
        routing pass reads the database, and the database already has the edit.
        """
        lead = get_object_or_404(self._queryset(request), pk=pk)
        if not self._may_edit(request, lead):
            raise PermissionDenied(
                "This lead is assigned to somebody else. You can read it, but "
                "only its owner can record work on it."
            )
        form = LeadWriteSerializer(data=request.data, partial=True)
        form.is_valid(raise_exception=True)
        engine.record_touch(lead, request.user, form.validated_data)
        self._attach([lead])
        self._attach_history([lead])
        return Response(LeadSerializer(lead, context=self._context(request, [lead])).data)

    @action(detail=True, methods=["patch"], url_path="invoice-type")
    def invoice_type(self, request, pk=None):
        """
        The human verdict on Blind versus Requested.

        Its own action because it is a different question from a caller's daily
        edit: it overrules the classifier permanently, and nothing automated may
        ever write this column. Requires the module's update right, which the
        permission class already checked.
        """
        lead = get_object_or_404(self._queryset(request), pk=pk)
        if not self._may_edit(request, lead):
            raise PermissionDenied(
                "Only this lead's owner can change its invoice type."
            )
        form = InvoiceTypeOverrideSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        value = form.validated_data["invoice_type_override"]
        CreditControlLead.objects.filter(pk=lead.pk).update(
            invoice_type_override=value,
            invoice_type_source=(
                CreditControlLead.TypeSource.MANUAL if value else lead.invoice_type_source
            ),
        )
        lead.refresh_from_db()
        self._attach([lead])
        return Response(LeadSerializer(lead, context=self._context(request, [lead])).data)

    # ── aggregates and admin ───────────────────────────────────────────────

    @action(detail=False, methods=["get"])
    def dashboard(self, request):
        return Response(dashboard.build(scoped_to=None if self._may_see_all(request) else request.user))

    @action(detail=False, methods=["get"])
    def dispositions(self, request):
        rows = CreditControlDisposition.objects.filter(active=True)
        return Response({"results": DispositionSerializer(rows, many=True).data})

    # The jobs the page may run by hand, and the command each one is.
    #
    # A WHITELIST, not a command name off the request. `call_command` with a
    # caller-supplied string is remote code execution wearing a management
    # command's clothes, and there are only four things anybody wants to press.
    RUNNABLE_JOBS = {
        "routing": ("refresh_credit_control", ["--skip-hubspot"]),
        "hubspot": ("sync_hubspot_calls", []),
        "contacts": ("refresh_credit_control", []),
        "classifier": ("classify_invoice_types", []),
        # No mailable entry: that job feeds the Mining Matrix's column and is
        # run from there (mining_matrix/views.py sync_mailable).
    }

    @action(detail=False, methods=["post"], url_path="run/(?P<job>[a-z_]+)")
    def run_job(self, request, job=None):
        """
        Run one scheduled job now, from the dashboard.

        ADMINS ONLY. Cron owns the cadence; this is the button for when
        somebody has just fixed a verdict, an invoice date or a HubSpot record
        and wants to see it land rather than wait. Every one of them is
        idempotent, so a double press is harmless.

        The classifier and the HubSpot jobs talk to third parties and can take
        minutes, so this runs them INLINE and answers when they finish. That is
        acceptable for a button an admin presses deliberately and would not be
        for anything on a caller's path; if one ever grows past a request
        timeout, the answer is a job runner rather than a longer timeout.
        """
        if not is_super_admin(request.user):
            raise PermissionDenied(
                "Running a scheduled job by hand is restricted to administrators."
            )
        entry = self.RUNNABLE_JOBS.get(job or "")
        if not entry:
            return Response(
                {"detail": f"Unknown job. Choose one of: "
                           f"{', '.join(sorted(self.RUNNABLE_JOBS))}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from io import StringIO

        from django.core.management import call_command

        command, args = entry
        out = StringIO()
        try:
            call_command(command, *args, stdout=out, stderr=out)
        except Exception as exc:  # noqa: BLE001
            logger.exception("credit_control: manual %s failed", job)
            return Response(
                {"job": job, "ok": False, "output": f"{type(exc).__name__}: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        logger.info("credit_control: manual %s by %s", job, request.user)
        return Response({"job": job, "ok": True, "output": out.getvalue().strip()})

    @action(detail=False, methods=["post"])
    def refresh(self, request):
        """
        Run the routing pass now.

        Cron owns the normal cadence; this is the button, for when somebody has
        just fixed a verdict or an invoice date and wants to see it land. The
        pass is idempotent, so pressing it twice is harmless.
        """
        summary = engine.refresh()
        logger.info("credit_control: manual refresh by %s: %s", request.user, summary)
        return Response(summary)
