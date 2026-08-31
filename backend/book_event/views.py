"""
book_event/views.py
────────────────────
Invoice CRUD + payment update + website intake.
"""
import logging
from datetime import datetime
from django.db import transaction, IntegrityError
from django.db.models import Count, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from rest_framework import viewsets, status
from rest_framework.authentication import TokenAuthentication, SessionAuthentication
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.audit import log_module_wipe, reclaim_after_wipe
from accounts.permissions import RBACMixin, IsSalesOrAdmin, IsAdminRole, IsHPAccount
from accounts.crm_permissions import crm_permission
from .authentication import ApiKeyAuthentication, HasApiKey
from .models import BookEvent, WebhookLog
from .serializers import (
    BookEventListSerializer, BookEventDetailSerializer,
    PaymentUpdateSerializer, WebsiteBookingSerializer,
)
from .filters import BookEventFilter
from webhooks.utils import unwrap_payload
from teams.models import Team

logger = logging.getLogger(__name__)


# ── Smart Import: the columns bulk_import accepts ────────────────────────────
#
# (key, label, aliases). `key` is the row key bulk_import reads; `label` is what
# the mapping step shows; `aliases` are extra header spellings that should
# auto-map onto the field.
#
# THE BUG THIS CLOSES
# This list lived in frontend/src/api/import.js as a hand-written array of 17
# entries, while bulk_import reads 28 keys. The eleven missing ones — currency,
# paid_or_free, payment_date, discount_code, add_ons, delegate_count, edition,
# sales_executive, position, notes, created_at — had nowhere to map to, so a
# spreadsheet carrying them was silently imported without them. A skipped column
# looks identical to a column that was not in the file.
#
# NOT LISTED, ON PURPOSE — bulk_import does not read these, and offering a field
# the importer ignores is worse than omitting it, because the wizard would report
# the column as mapped:
#   total_amount, pre_tax_amount, tax_amount, add_ons_total_amount
#                          money columns; the importer writes none of them
#   source, form_name, form_url, packages, payment_due_date, parent_code
#                          website-intake provenance, set by the webhook path
#   team_leader, updated_by, import_batch_id
#                          system/audit fields
#   invoice_number         IS accepted and IS listed — omitted from this note
#                          only to say plainly that a blank one is generated
#                          ("IMP-…"), it is not required
#
# Order is specific-before-generic, because ImportWizard's autoMap falls back to
# a substring scan that takes the first hit in this order: accounts_contact_email
# before contact_email (else "Accounts Email" matches contact_email first),
# discount_code before discount, invoice_date before invoice_number.
#
# THE ALIASES OUR OWN EXPORTS USE
# "Delegate Company" and "Delegate Email" are the spellings the Master Data sheet
# carries, and neither resolved. autoMap matches on key, label or alias and then
# falls back to a symmetric substring scan, which compares "delegatecompany"
# against "companyname" and fails in both directions — so both columns mapped to
# nothing and were skipped, and a skipped column is indistinguishable in the
# wizard from a column the file never contained. Delegate Email is the delegate
# IDENTITY key: without it the importer deduplicates on invoice number plus an
# empty string, so every second row on an invoice collides and is given a
# dup-xxxxxxxx@import.local placeholder. "Date Paid" and "Ref" failed the same
# way. "Attendance - IN?" only ever mapped by luck, because "attendancein"
# happens to contain "attendance"; it is declared now rather than left to the
# substring scan.
#
# Any header that still fails to map is REPORTED BY NAME on the review step
# before a single row is written — see ImportWizard.jsx. A column that maps to
# nothing can no longer look like a clean import.
BOOKING_IMPORT_FIELDS = (
    ("invoice_number", "Invoice Number", ("Invoice No", "Invoice #")),
    ("event_code", "Event Code", ()),
    ("event_name", "Event Name", ()),
    ("booking_code", "Booking Code", ()),
    ("edition", "Edition", ("Year",)),
    ("company_name", "Company", ("Company Name", "Organisation", "Delegate Company")),
    ("contact_name", "Delegate Name", ("Name", "Attendee", "Full Name")),
    ("position", "Job Title / Position", ("Designation", "Job Title")),
    ("accounts_contact_email", "Accounts Email", ("Accounts Contact Email",)),
    ("contact_email", "Email", ("Email Address", "Delegate Email")),
    ("contact_phone", "Direct Line", ("Phone", "Phone Number", "Mobile")),
    ("request_date", "Request Date", ()),
    ("invoice_date", "Invoice Date", ()),
    ("payment_date", "Payment Date", ("Date Paid",)),
    ("payment_status", "Payment Status", ("Status",)),
    # "Paid / Free" is the OLD label, kept as an alias. autoMap resolves a
    # spreadsheet header by key, label or alias, and the loose fallback compares
    # against the key ("paidorfree"), which a "Paid/Free" column does not contain
    # — dropping the old spelling would leave every existing export unmapped.
    ("paid_or_free", "Payable / Free", ("Paid or Free", "Paid / Free")),
    ("payment_type", "Payment Type", ("Payment Method",)),
    ("ticket_tier", "Ticket Tier", ("Tier",)),
    ("currency", "Currency", ()),
    ("discount_code", "Discount Code", ()),
    ("discount", "Discount", ()),
    ("delegate_count", "Delegate Count", ("No of Delegates",)),
    ("attendance", "Attendance", ("Attended", "Confirmed", "Attendance - IN?", "Attendance IN")),
    ("add_ons", "Add-Ons", ("Addons",)),
    ("reference", "Reference", ("Payment Reference", "Ref")),
    ("notes", "Notes", ("Comments", "Remarks")),
    ("sales_executive", "Sales Executive (username/email)", ("Sales Exec", "Sales Rep", "Sales Team")),
    ("created_at", "Added Time", ("Created At", "Created Time")),
)


class BookEventViewSet(RBACMixin, viewsets.ModelViewSet):
    permission_classes = [crm_permission("bookings")]
    # Whose "all" cell widens these rows to every booking. See RBACMixin.
    rbac_module        = "bookings"
    filterset_class = BookEventFilter
    search_fields   = [
        "invoice_number", "event_code", "contact_name",
        "contact_email", "company_name", "reference",
    ]
    ordering_fields = ["created_at", "payment_status", "event_date", "company_name"]
    ordering        = ["-created_at"]

    def get_queryset(self):
        qs = BookEvent.objects.select_related("sales_executive")
        qs = self.rbac_filter(qs)
        if self.action == "list":
            # WAS: Count("delegates", distinct=True), which LEFT JOINs every
            # delegate row onto every invoice and then GROUP BYs the whole result
            # set before LIMIT can apply. With only select_related("sales_executive")
            # there is no join that can multiply rows, so distinct=True bought
            # nothing and paid for a sort or hash of the joined delegates across the
            # entire table to return 50 invoices. The measured plan was
            #   Sort -> GroupAggregate -> Hash Right Join (1,251 delegate rows)
            # for a 50-row page. A correlated subquery lets the LIMIT apply first,
            # so the subquery runs 50 times instead of the join running 1,251.
            #
            # OuterRef("invoice_number"), NOT OuterRef("pk"): BookDelegate.invoice
            # is a to_field FK on invoice_number, so the attname invoice_id holds a
            # varchar invoice number. Comparing it to the outer pk would compare
            # varchar to integer, which errors rather than silently matching
            # nothing. The filter keyword is invoice_id, the attname — invoice_number
            # is the DB column and Django will not resolve it as a query name.
            #
            # Coalesce to 0 because a correlated subquery returns NULL for an
            # invoice with no delegates, where COUNT returned 0. Without it, every
            # zero-delegate invoice would serialise null instead of 0.
            # Local import, matching this module's existing convention for
            # BookDelegate (see the wipe handler below): book_delegate imports
            # book_event at module level, so a top-level import here is circular.
            from book_delegate.models import BookDelegate
            counts = (BookDelegate.objects
                      .filter(invoice_id=OuterRef("invoice_number"))
                      .order_by().values("invoice_id")
                      .annotate(n=Count("pk")).values("n")[:1])
            qs = qs.annotate(_delegate_count_actual=Coalesce(
                Subquery(counts, output_field=IntegerField()), 0))
        elif self.action in ("retrieve", "update", "partial_update"):
            qs = qs.prefetch_related("delegates__company")
        return qs

    def get_serializer_class(self):
        if self.action in ("create", "retrieve", "update", "partial_update"):
            return BookEventDetailSerializer
        return BookEventListSerializer

    def retrieve(self, request, *args, **kwargs):
        logger.info("RETRIEVE invoice: %s", kwargs.get("pk"))
        try:
            return super().retrieve(request, *args, **kwargs)
        except Exception as e:
            logger.error("RETRIEVE ERROR: %s", str(e), exc_info=True)
            raise

    def partial_update(self, request, *args, **kwargs):
        logger.info("UPDATE invoice: %s | data: %s", kwargs.get("pk"), request.data)
        try:
            return super().partial_update(request, *args, **kwargs)
        except Exception as e:
            logger.error("UPDATE ERROR: %s", str(e), exc_info=True)
            raise

    def perform_create(self, serializer):
        invoice = serializer.save()
        from accounts.models import ActionLog
        ActionLog.objects.create(
            user=self.request.user,
            action=f"Created booking {invoice.invoice_number}",
            details=f"For event {invoice.event_code}"
        )

    def perform_update(self, serializer):
        invoice = serializer.save(updated_by=self.request.user)
        from accounts.models import ActionLog
        ActionLog.objects.create(
            user=self.request.user,
            action=f"Updated booking {invoice.invoice_number}",
            details=f"Payment status: {invoice.payment_status}"
        )

    @action(detail=True, methods=["patch"], url_path="update_payment")
    def update_payment(self, request, pk=None):
        """PATCH /api/invoices/{id}/update_payment/ — payment-only update."""
        invoice = self.get_object()
        ser = PaymentUpdateSerializer(invoice, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save(updated_by=request.user)

        from accounts.models import ActionLog
        ActionLog.objects.create(
            user=request.user,
            action=f"Updated payment for {invoice.invoice_number}",
            details=f"New status: {invoice.payment_status}"
        )
        return Response({
            "invoice_number": invoice.invoice_number,
            "payment_status": invoice.payment_status,
            "payment_type":   invoice.payment_type,
            "payment_date":   str(invoice.payment_date) if invoice.payment_date else None,
            "paid_or_free":   invoice.paid_or_free,
            "ticket_tier":    invoice.ticket_tier,
        })

    @action(detail=False, methods=["delete"], url_path="clear_all",
            permission_classes=[IsHPAccount])
    def clear_all(self, request):
        """
        DELETE /api/invoices/clear_all/ — HP only, see accounts.permissions.IsHPAccount.

        The gate is the permission class, not an inline username test. It was the
        latter, one copy per module, which is how "only HP" drifts.
        """
        from book_delegate.models import BookDelegate
        from historical_event_registry.models import HistoricalEventReference, EventEditionMetrics
        from .models import SyncLog

        try:
            with transaction.atomic():
                deleted = {
                    "delegates": BookDelegate.objects.count(),
                    "invoices": BookEvent.objects.count(),
                }
                BookDelegate.objects.all().delete()
                BookEvent.objects.all().delete()
                WebhookLog.objects.all().delete()
                SyncLog.objects.all().delete()
                HistoricalEventReference.objects.all().delete()
                EventEditionMetrics.objects.all().delete()
                log_module_wipe(request.user, "BOOKINGS", deleted)
            # Outside the atomic block, deliberately: VACUUM cannot run inside a
            # transaction. This is the wipe that produced the 550 MB book_delegates
            # table holding 1,250 rows, and with it the 507 ms dashboard — see
            # accounts/audit.py reclaim_after_wipe.
            reclaim_after_wipe(
                "book_delegates", "book_events", "webhook_events",
                HistoricalEventReference._meta.db_table, EventEditionMetrics._meta.db_table,
            )
            return Response({
                "detail": "Successfully removed all booking module data.",
                "deleted": deleted,
            })
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=["get"])
    def pending(self, request):
        """GET /api/invoices/pending/ — shortcut for pending invoices."""
        qs = self.filter_queryset(self.get_queryset().filter(payment_status="Pending"))
        page = self.paginate_queryset(qs)
        ser = BookEventListSerializer(page if page is not None else qs, many=True)
        return self.get_paginated_response(ser.data) if page else Response(ser.data)

    @action(detail=False, methods=["get"], permission_classes=[IsSalesOrAdmin])
    def stats(self, request):
        """
        GET /api/invoices/stats/?period=today|month|total
        Returns volume stats for Sales, SpEx, and Speaker Sales.
        """
        from django.utils import timezone
        from django.db.models import Q, Count
        from book_delegate.models import BookDelegate

        period = request.query_params.get("period", "total")
        qs = self.filter_queryset(self.get_queryset())

        now = timezone.now()
        today = now.date()
        
        if period == "today":
            qs = qs.filter(invoice_date=today)
        elif period == "yesterday":
            from datetime import timedelta
            qs = qs.filter(invoice_date=today - timedelta(days=1))
        elif period == "last_7_days":
            from datetime import timedelta
            qs = qs.filter(invoice_date__gte=today - timedelta(days=7))
        elif period == "last_30_days":
            from datetime import timedelta
            qs = qs.filter(invoice_date__gte=today - timedelta(days=30))
        elif period == "month":
            qs = qs.filter(invoice_date__year=now.year, invoice_date__month=now.month)
        elif period == "quarter":
            # Optional: handle quarter
            q = (now.month - 1) // 3 + 1
            qs = qs.filter(invoice_date__year=now.year, invoice_date__month__gte=(q-1)*3+1, invoice_date__month__lte=q*3)
        elif period == "year":
            qs = qs.filter(invoice_date__year=now.year)

        del_qs = BookDelegate.objects.filter(invoice__in=qs)

        # Booking-code matchers (applied on BookEvent fields).
        # SpEx  : marker "spex" OR exactly "Add-Ons"
        # Speaker: marker "speaker" OR "spp"
        #          Hybrid codes (e.g. "Speaker / SLV SpEx") match BOTH — intentional,
        #          and preserved: these two predicates deliberately overlap.
        #
        # Matching is now BOUNDARY-ANCHORED and the marker lists live in settings —
        # see book_event/booking_code.py. Previously these were raw `icontains`, so
        # any code merely CONTAINING "spp" ("SUPPLEMENT", a supplier ref) counted as
        # speaker sales and nothing surfaced it. Same rule as the event-code
        # resolver, single-sourced from its boundary_regex.
        from book_event.booking_code import spex_q, speaker_q

        SPEX_Q    = spex_q("booking_code")
        SPEAKER_Q = speaker_q("booking_code")

        # Delegate-level equivalents (prefix with invoice__ for BookDelegate querysets)
        INV_SPEX_Q    = spex_q("invoice__booking_code")
        INV_SPEAKER_Q = speaker_q("invoice__booking_code")

        # For individual KPI cards, scope to the rep's own attributed bookings.
        # SpEx / Speaker Sales attribution comes from the event's team string fields
        # (event.spex_team, and event.sales_team for speaker sales now that the
        # Speaker Sales team is merged into SCA) — NOT the invoice sales_executive FK,
        # because the FK is set to the main sales rep, not the SpEx/Speaker rep.
        # Sales reps continue to use the sales_executive FK.
        # Admin sees the global view with no restriction.
        from events.models import Event as _Event

        def _event_codes_for_field(field, name):
            """Return event codes where the given team field icontains the name."""
            if not name:
                return []
            return list(_Event.objects.filter(
                **{f"{field}__icontains": name}
            ).values_list("event_code", flat=True))

        if not request.user.is_admin:
            u_name = (request.user.get_full_name() or request.user.username).strip()
            u_role = request.user.role

            if u_role == "spex":
                ecodes = _event_codes_for_field("spex_team", u_name)
                rep_inv_qs = qs.filter(SPEX_Q, event_code__in=ecodes)
                rep_del_qs = BookDelegate.objects.filter(invoice__in=rep_inv_qs)

            elif u_role == "speaker_sales":
                # Merged into SCA: the owner name now lives in event.sales_team.
                ecodes = _event_codes_for_field("sales_team", u_name)
                rep_inv_qs = qs.filter(event_code__in=ecodes)
                rep_del_qs = BookDelegate.objects.filter(invoice__in=rep_inv_qs)

            else:
                # Sales / Telemarketing / other — use sales_executive FK
                rep_inv_qs = qs.filter(sales_executive=request.user)
                rep_del_qs = BookDelegate.objects.filter(invoice__in=rep_inv_qs)
        else:
            rep_inv_qs = qs
            rep_del_qs = del_qs

        # 1. Sales / Telemarketing Stats (Standard Delegate Counts)
        sales = rep_del_qs.aggregate(
            total=Count("id"),
            paid=Count("id", filter=Q(invoice__payment_status="Paid")),
            free=Count("id", filter=Q(invoice__paid_or_free="Free")),
        )

        # 2. SpEx Stats — unique companies booked via SpEx codes
        spex_qs     = rep_inv_qs.filter(SPEX_Q)
        spex_booked = spex_qs.values("company_name").distinct().count()
        spex_paid   = spex_qs.filter(payment_status="Paid").values("company_name").distinct().count()

        # 3. Speaker Sales Stats — delegates whose invoice booking_code is Speaker / SPP
        speaker = rep_del_qs.aggregate(
            total=Count("id", filter=INV_SPEAKER_Q),
            confirmed=Count("id", filter=INV_SPEAKER_Q & Q(attendance="Confirmed")),
            paid=Count("id", filter=INV_SPEAKER_Q & Q(invoice__payment_status="Paid")),
        )

        # Team lead productivity calculation
        from teams.models import Team
        from django.db.models import Sum

        is_lead = getattr(request.user, "is_team_lead", False) or Team.objects.filter(team_lead=request.user).exists()
        team_productivity = []

        if is_lead or request.user.role == "admin":
            if request.user.role == "admin":
                teams_led = Team.objects.filter(is_archived=False)
            else:
                teams_led = Team.objects.filter(
                    Q(team_lead=request.user) | Q(members__id=request.user.id, members__is_team_lead=True)
                ).filter(is_archived=False).distinct()

            def _apply_period(qs_in):
                """Apply the selected period filter to a BookDelegate or BookEvent queryset."""
                from datetime import timedelta
                is_delegate = qs_in.model.__name__ == "BookDelegate"
                date_field = "invoice__invoice_date" if is_delegate else "invoice_date"
                if period == "today":
                    return qs_in.filter(**{date_field: today})
                elif period == "yesterday":
                    return qs_in.filter(**{date_field: today - timedelta(days=1)})
                elif period == "last_7_days":
                    return qs_in.filter(**{f"{date_field}__gte": today - timedelta(days=7)})
                elif period == "last_30_days":
                    return qs_in.filter(**{f"{date_field}__gte": today - timedelta(days=30)})
                elif period == "month":
                    return qs_in.filter(**{f"{date_field}__year": now.year, f"{date_field}__month": now.month})
                elif period == "year":
                    return qs_in.filter(**{f"{date_field}__year": now.year})
                return qs_in

            for t in teams_led:
                members_stats = []
                team_leads_count = t.members.filter(status="active", is_team_lead=True).count()
                if team_leads_count > 1 and not request.user.role == "admin":
                    members_qs = t.members.filter(status="active", mapped_lead=request.user)
                else:
                    members_qs = t.members.filter(status="active")

                # Determine team type from dominant member role
                role_counts = {}
                for role_val in members_qs.values_list("role", flat=True):
                    role_counts[role_val] = role_counts.get(role_val, 0) + 1
                dominant_role = max(role_counts, key=role_counts.get) if role_counts else "sales"

                if dominant_role == "spex":
                    team_type = "spex"
                elif dominant_role == "speaker_sales":
                    team_type = "speaker_sales"
                else:
                    team_type = "sales"

                for m in members_qs:
                    m_name = (m.get_full_name() or m.username).strip()

                    if team_type == "spex":
                        # SpEx attribution: event.spex_team matches member name + SPEX_Q booking code
                        m_ecodes = _event_codes_for_field("spex_team", m_name)
                        m_invoices = _apply_period(
                            BookEvent.objects.filter(SPEX_Q, event_code__in=m_ecodes)
                        )
                        booking_count = m_invoices.values("company_name").distinct().count()
                        paid_count    = m_invoices.filter(payment_status="Paid").values("company_name").distinct().count()
                        pending_count = max(0, booking_count - paid_count)

                    elif team_type == "speaker_sales":
                        # Speaker Sales attribution: event.sales_team (SCA) matches member
                        # name + booking_code matches SPEAKER_Q. Hybrid codes count here AND
                        # in SpEx.
                        m_ecodes = _event_codes_for_field("sales_team", m_name)
                        m_bookings = _apply_period(
                            BookDelegate.objects.filter(
                                INV_SPEAKER_Q,
                                invoice__event_code__in=m_ecodes,
                            )
                        )
                        booking_count = m_bookings.count()
                        paid_count    = m_bookings.filter(invoice__payment_status="Paid").count()
                        pending_count = max(0, booking_count - paid_count)

                    else:
                        # Sales / Telemarketing: attributed via sales_executive FK
                        m_bookings = _apply_period(
                            BookDelegate.objects.filter(invoice__sales_executive=m)
                        )
                        booking_count = m_bookings.count()
                        paid_count    = m_bookings.filter(invoice__payment_status="Paid").count()
                        pending_count = max(0, booking_count - paid_count)

                    # Revenue totals: SpEx/Speaker via event team fields, Sales via FK
                    if team_type == "spex":
                        m_ecodes_rev = _event_codes_for_field("spex_team", m_name)
                        member_invoices = _apply_period(
                            BookEvent.objects.filter(SPEX_Q, event_code__in=m_ecodes_rev)
                        )
                    elif team_type == "speaker_sales":
                        m_ecodes_rev = _event_codes_for_field("sales_team", m_name)
                        member_invoices = _apply_period(
                            BookEvent.objects.filter(event_code__in=m_ecodes_rev)
                        )
                    else:
                        member_invoices = _apply_period(
                            BookEvent.objects.filter(sales_executive=m)
                        )
                    total_value   = member_invoices.aggregate(val=Sum("total_amount"))["val"] or 0
                    paid_value    = member_invoices.filter(payment_status="Paid").aggregate(val=Sum("total_amount"))["val"] or 0
                    pending_value = max(0, total_value - paid_value)

                    members_stats.append({
                        "username": m.username,
                        "full_name": m.get_full_name() or m.username,
                        "role": m.role,
                        "bookings": booking_count,
                        "paid_bookings": paid_count,
                        "pending_bookings": pending_count,
                        "total_value": float(total_value),
                        "paid_value": float(paid_value),
                        "pending_value": float(pending_value),
                    })

                team_productivity.append({
                    "team_id": t.id,
                    "team_name": t.name,
                    "team_type": team_type,
                    "members": members_stats
                })

        return Response({
            "sales": {
                "total": sales["total"] or 0,
                "paid": sales["paid"] or 0,
                "pending": max(0, (sales["total"] or 0) - (sales["paid"] or 0)),
                "free": sales["free"] or 0,
            },
            "spex": {
                "booked": spex_booked,
                "paid": spex_paid,
                "pending": max(0, spex_booked - spex_paid),
            },
            "speaker": {
                "total": speaker["total"] or 0,
                "confirmed": speaker["confirmed"] or 0,
                "paid": speaker["paid"] or 0,
                "pending": max(0, (speaker["total"] or 0) - (speaker["paid"] or 0)),
            },
            "team_productivity": team_productivity
        })

    @action(
        detail=False, methods=["post"], url_path="create_from_website",
        authentication_classes=[ApiKeyAuthentication, TokenAuthentication, SessionAuthentication],
        permission_classes=[HasApiKey | IsSalesOrAdmin],
    )
    def create_from_website(self, request):
        """
        POST /api/invoices/create_from_website/
        Accepts X-API-KEY from external websites OR a CRM session/token for manual testing.
        """
        source_ip = (
            request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
            or request.META.get("REMOTE_ADDR", "")
        )
        safe_headers = {
            k: v for k, v in request.META.items()
            if k.startswith("HTTP_") and k != "HTTP_X_API_KEY"
        }

        payload = unwrap_payload(request.data)
        ser = WebsiteBookingSerializer(data=payload)
        if not ser.is_valid():
            log = WebhookLog.objects.create(
                source_ip=source_ip, payload=request.data, headers=safe_headers,
                response={"errors": ser.errors}, status=WebhookLog.Status.FAILED,
                http_status=400, error_message=str(ser.errors),
            )
            return Response({"success": False, "errors": ser.errors}, status=status.HTTP_400_BAD_REQUEST)

        d = ser.validated_data
        invoice_number = d["InvoiceNumber"]
        event_code     = d["Eventcode"]

        # Duplicate check
        if BookEvent.objects.filter(invoice_number=invoice_number).exists():
            resp = {"success": False, "detail": f"Invoice '{invoice_number}' already exists."}
            WebhookLog.objects.create(
                source_ip=source_ip, payload=request.data, headers=safe_headers,
                response=resp, status=WebhookLog.Status.DUPLICATE,
                http_status=409, invoice_number=invoice_number, event_code=event_code,
                error_message="Duplicate invoice number",
            )
            return Response(resp, status=status.HTTP_409_CONFLICT)

        from companies.models import Company
        from book_delegate.models import BookDelegate

        try:
            company, company_created = Company.get_or_create_from_payload(d)
            sales_exec = BookEvent.auto_assign_sales(event_code)

            # Map PaymentStatus string from payload to choices
            payment_status_map = {v.lower(): v for v in BookEvent.PaymentStatus.values}
            incoming_ps = d.get("PaymentStatus", "").strip().lower()
            payment_status = payment_status_map.get(
                incoming_ps, BookEvent.PaymentStatus.PENDING)

            # Through the shared coercion table, so this intake path and the
            # webhook service and the importer read the same payload the same
            # way. An unrecognised value is stored blank rather than guessed at;
            # a website delivery is not rejected over one display field.
            from accounts.booking_coercion import coerce as _coerce_booking

            ticket_tier  = _coerce_booking("ticket_tier", d.get("TicketTier", ""))[0] or ""
            paid_or_free = _coerce_booking("paid_or_free", d.get("PaidOrFree", ""))[0] or ""

            # Omitted from create() when the payload states no currency, so the
            # model's own declared default applies rather than this endpoint
            # asserting USD on the payload's behalf. `d.get("Currency", "USD")`
            # also read an unrecognised spelling as USD.
            from accounts.booking_coercion import UNSET as _UNSET
            _currency, _currency_err = _coerce_booking("currency", d.get("Currency"))
            currency_kwarg = (
                {} if (_currency_err or _currency is _UNSET or not _currency)
                else {"currency": _currency}
            )
            if _currency_err:
                logger.warning("Website intake %s: %s", invoice_number, _currency_err)

            with transaction.atomic():
                invoice = BookEvent.objects.create(
                    invoice_number         = invoice_number,
                    event_code             = event_code,
                    event_name             = d.get("Eventname", ""),
                    event_date             = d.get("Date"),
                    company_name           = d.get("DelegateCompanyName", ""),
                    accounts_contact_email = d.get("AccountsContactEmail", ""),
                    discount               = d["Discount"],
                    discount_code          = d.get("DiscountCode", ""),
                    pre_tax_amount         = d.get("PreTaxAmount"),
                    tax_amount             = d.get("TaxAmount"),
                    total_amount           = d.get("TotalAmount"),
                    add_ons_total_amount   = d.get("AddOnsTotalAmount"),
                    **currency_kwarg,
                    payment_status         = payment_status,
                    ticket_tier            = ticket_tier,
                    paid_or_free           = paid_or_free,
                    sales_executive        = sales_exec,
                    source                 = BookEvent.Source.WEBSITE,
                    form_name              = d.get("FormName", ""),
                    form_url               = d.get("FormURL", ""),
                    packages               = d.get("Packages", []),
                )

                delegates_payload = d.get("Delegates", [])
                created, skipped  = [], 0

                for i, dp in enumerate(delegates_payload):
                    email = dp["Email"].strip().lower()
                    if BookDelegate.objects.filter(invoice=invoice, email=email).exists():
                        skipped += 1
                        continue
                    # `or None` because both are nullable OVERRIDE columns and
                    # null is what "inherit the invoice" means; "" would shadow
                    # the invoice with a blank.
                    d_tier = _coerce_booking("ticket_tier", dp.get("TicketTier", ""))[0] or None
                    d_pof  = _coerce_booking("paid_or_free", dp.get("PaidOrFree", ""))[0] or None
                    delegate = BookDelegate.objects.create(
                        invoice              = invoice,
                        event_code           = event_code,
                        company              = company,
                        company_name_raw     = d.get("DelegateCompanyName", ""),
                        first_name           = dp["FirstName"].strip(),
                        last_name            = dp.get("LastName", "").strip(),
                        email                = email,
                        phone_number         = dp.get("PhoneNumber", "").strip(),
                        position             = dp.get("Position", "").strip(),
                        ticket_package       = dp.get("TicketPackage", "").strip(),
                        sponsorship_level    = dp.get("SponsorshipLevel", "").strip(),
                        delegate_ticket_tier = d_tier,
                        delegate_paid_or_free= d_pof,
                    )
                    created.append(delegate)
                    if i == 0:
                        invoice.contact_name  = delegate.full_name
                        invoice.contact_email = email

                invoice.delegate_count = len(created)
                invoice.save(update_fields=["contact_name", "contact_email", "delegate_count"])

        except Exception as exc:
            err_msg = str(exc)
            logger.error("Website intake error: %s", err_msg, exc_info=True)
            resp = {"success": False, "detail": "Internal server error during intake."}
            WebhookLog.objects.create(
                source_ip=source_ip, payload=request.data, headers=safe_headers,
                response=resp, status=WebhookLog.Status.FAILED,
                http_status=500, invoice_number=invoice_number, event_code=event_code,
                error_message=err_msg,
            )
            return Response(resp, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        logger.info(
            "Website intake: %s | event: %s | delegates: %d | sales: %s",
            invoice_number, event_code, len(created),
            sales_exec.username if sales_exec else "unassigned",
        )

        if sales_exec:
            from accounts.models import ActionLog
            ActionLog.objects.create(
                user=sales_exec,
                action=f"Auto-assigned to new booking {invoice.invoice_number}",
                details=f"Created from website for event {event_code}",
            )

        resp_body = {
            "success":           True,
            "invoice_number":    invoice.invoice_number,
            "booking_id":        invoice.id,
            "event_code":        invoice.event_code,
            "company":           {"id": company.id, "name": company.name} if company else None,
            "company_created":   company_created,
            "delegates_created": len(created),
            "delegates_skipped": skipped,
            "sales_executive":   sales_exec.username if sales_exec else None,
            "payment_status":    invoice.payment_status,
        }
        WebhookLog.objects.create(
            source_ip=source_ip, payload=request.data, headers=safe_headers,
            response=resp_body, status=WebhookLog.Status.SUCCESS,
            http_status=201, invoice_number=invoice_number, event_code=event_code,
        )
        return Response(resp_body, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="import_schema")
    def import_schema(self, request):
        """
        GET /api/invoices/import_schema/ — the columns bulk_import below accepts.

        WHY THIS ENDPOINT EXISTS
        The Smart Import wizard's field list was a hand-written array in
        frontend/src/api/import.js. It offered 17 of the 28 columns bulk_import
        actually reads, and the eleven it left out are invisible: a spreadsheet
        carrying Currency, Position, Sales Executive or Delegate Count has nowhere
        to map them, so the wizard quietly skips those columns and the import looks
        like it worked. Nothing in the UI could say otherwise, because the UI did
        not know the fields existed.

        Published FROM the definition the importer is driven by, so a field added
        to one is offered by the other in the same commit.
        """
        return Response({
            "kind": "bookings",
            "fields": [
                {"key": key, "label": label, "aliases": list(aliases)}
                for key, label, aliases in BOOKING_IMPORT_FIELDS
            ],
        })

    @action(detail=False, methods=["post"], url_path="bulk_import")
    def bulk_import(self, request):
        """
        POST /api/invoices/bulk_import/
        Bulk-insert up to 500 BookEvent rows per call.
        Body: {
            rows: [...],
            duplicate_strategy: "skip"|"upsert",
            batch_number: int,
            dry_run: bool,               # count, report, write NOTHING
            import_batch_id: uuid,       # one value for the whole import run
        }

        Accepted columns are BOOKING_IMPORT_FIELDS, published by import_schema
        above. A test asserts the two agree — see tests_import_schema.py.

        HOW VALUES ARE READ
        Every constrained column goes through accounts/booking_coercion, the one
        typed table shared by all six booking write paths. This endpoint used to
        carry its own five lookups plus a bare `except Exception` per numeric
        field, and coerced a value it did not recognise into a blank, a default,
        or whatever was already stored. That is what lost seven columns of the
        26 August master import without reporting a single error.

        A cell that has content and cannot be read now FAILS ITS ROW, naming the
        column and the value. Nothing partial is written for that row.
        """
        rows               = request.data.get("rows", [])
        strategy           = request.data.get("duplicate_strategy", "skip")
        batch_number       = request.data.get("batch_number", 1)
        dry_run            = bool(request.data.get("dry_run"))

        if not rows:
            return Response({"success": False, "detail": "No rows provided."}, status=400)

        from accounts.booking_coercion import UNSET, coerce_row, column_report

        # ── Dry run ───────────────────────────────────────────────────────────
        # Answered from the rows alone, before any of the machinery below is set
        # up, so a preview cannot write and cannot need a transaction to prove it
        # did not. On the 26 August file this would have read
        #   Payable / Free — 11,210 of 15,180 values not recognised
        # while the import could still be abandoned.
        if dry_run:
            per_row_errors = []
            for i, row in enumerate(rows):
                _, errs = coerce_row(row)
                if errs:
                    per_row_errors.append({
                        "row_index":      i,
                        "invoice_number": str(row.get("invoice_number") or "").strip(),
                        "messages":       errs,
                    })
            return Response({
                "success":      True,
                "dry_run":      True,
                "rows":         len(rows),
                "columns":      column_report(rows),
                "rows_with_errors": len(per_row_errors),
                "errors":       per_row_errors[:50],
            })

        # ── Batch identifier ──────────────────────────────────────────────────
        # WHY: the browser import stamped nothing and wrote no audit record,
        # unlike load_zoho_export which does both. Nothing in the database marked
        # a row as belonging to the 26 August import, and the invoice timestamps
        # could not stand in for it because the importer BACKDATES them from an
        # Added Time column. Scoping the repair was therefore guesswork over a
        # table of 11,288 invoices.
        #
        # The client sends ONE id for the whole run so every chunk of a 20,000-row
        # file shares it; a call that arrives without one gets its own, which is
        # still better than nothing and is returned so the caller can reuse it.
        import uuid as _uuid
        raw_batch_id = str(request.data.get("import_batch_id") or "").strip()
        try:
            batch_id = _uuid.UUID(raw_batch_id) if raw_batch_id else _uuid.uuid4()
        except ValueError:
            return Response(
                {"success": False, "detail": f"import_batch_id {raw_batch_id!r} is not a UUID."},
                status=400,
            )

        # ── helpers ───────────────────────────────────────────────────────────
        # Dates go through accounts/import_common.parse_import_date via the
        # coercion table. The six-format _parse_date that used to live here
        # returned None on failure, so a column of unreadable dates was
        # indistinguishable from a column of blanks.

        def _clean(d, key, default=""):
            return str(d.get(key) or default).strip()

        # The six person-level columns, paired [row key, the column on the
        # delegate that carries it]. Each one is stored BOTH on the invoice and
        # as a per-delegate override that shadows it at read time; every
        # serializer resolves them as `delegate_x or invoice.x`.
        #
        # WHY THIS PAIRING IS THE WHOLE FIX
        # The import file is ONE ROW PER DELEGATE. These six values were written
        # on the INVOICE only, and the importer never wrote the matching
        # per-delegate column even though all six exist on the model. So wherever
        # delegates on one invoice differed, a single row's value was applied to
        # everybody — and which row won depended on the duplicate strategy and on
        # the order the rows sat in the file, which is why the result read as
        # random. On the 26 August file that flattened 903 invoices carrying a mix
        # of Free and Payable, 868 carrying more than one Booking Code (which
        # drives revenue classification), and 1,160 carrying two Payment Types.
        #
        # This mirrors what the booking modal already does in the browser — see
        # OVERRIDE_FIELDS and splitPersonLevel in frontend/src/api/bookings.js.
        # The value goes on the PERSON, and the invoice is kept in step by
        # _reconcile_invoice below whenever every delegate on it agrees.
        PERSON_LEVEL = (
            ("paid_or_free",   "delegate_paid_or_free"),
            ("payment_status", "delegate_payment_status"),
            ("payment_type",   "delegate_payment_type"),
            ("payment_date",   "delegate_payment_date"),
            ("ticket_tier",    "delegate_ticket_tier"),
            ("request_date",   "delegate_request_date"),
            ("invoice_date",   "delegate_invoice_date"),
        )

        def _delegate_fields(row, ev_code, coerced):
            """
            The delegate row to write. `coerced` is this row's already-validated
            values from the shared coercion table, so nothing is re-parsed here
            and the delegate and the invoice cannot read the same cell two
            different ways.
            """
            name_raw = _clean(row, "contact_name")
            parts    = name_raw.split(" ", 1) if name_raw else []

            fields = dict(
                event_code=ev_code,
                first_name=parts[0] if parts else "",
                last_name=parts[1] if len(parts) > 1 else "",
                email=_clean(row, "contact_email").lower(),
                phone_number=_clean(row, "contact_phone"),
                company_name_raw=_clean(row, "company_name"),
                position=_clean(row, "position"),
                notes=_clean(row, "notes"),
                add_ons=_clean(row, "add_ons"),
                reference=_clean(row, "reference"),
                import_batch_id=batch_id,
            )
            for key in ("discount", "attendance", "edition"):
                if key in coerced:
                    fields[key] = coerced[key]
            # booking_code is per delegate on the model for exactly this reason:
            # a Speaker and a Group Pass on one invoice is a real combination and
            # BookEvent has one column to describe all of them.
            if _clean(row, "booking_code"):
                fields["booking_code"] = _clean(row, "booking_code")
            # The file's Delegate Count is the per-person 0/1 flag the Bookings
            # table shows, not the invoice's total; see the note in
            # accounts/booking_coercion.py.
            if "delegate_count" in coerced:
                fields["delegate_count"] = coerced["delegate_count"]
            for row_key, column in PERSON_LEVEL:
                if row_key in coerced and coerced[row_key] not in ("", None):
                    fields[column] = coerced[row_key]
            return fields

        def _safe_create_delegate(book_event, fields):
            """
            Create a BookDelegate inside a savepoint so an IntegrityError
            never aborts the outer PostgreSQL transaction.
            If (invoice, email) already exists, assign a placeholder email
            so the person is saved rather than silently dropped.
            """
            import uuid as _uuid
            from book_delegate.models import BookDelegate
            try:
                with transaction.atomic():   # savepoint — isolates the IntegrityError
                    return BookDelegate.objects.create(invoice=book_event, **fields)
            except IntegrityError:
                fields = dict(fields)
                fields["email"] = f"dup-{_uuid.uuid4().hex[:8]}@import.local"
                with transaction.atomic():
                    return BookDelegate.objects.create(invoice=book_event, **fields)

        def _save_delegate(book_event, row, ev_code, coerced, nth=1):
            """
            Save a delegate row.
            - nth=1 (first time this invoice+email appears in the batch):
                update existing delegate if found, otherwise create.
            - nth>1 (same invoice+email seen again → different person sharing email):
                always create a new delegate with a placeholder email so both
                people are stored (e.g. Oz Ruiz / Austin Ali both with jen@accelhealth.ai,
                or two TBA rows with tba@turboden.com).
            """
            from book_delegate.models import BookDelegate
            fields = _delegate_fields(row, ev_code, coerced)
            if nth > 1:
                # Different person sharing an email — must use placeholder to satisfy
                # the unique_together (invoice, email) constraint.
                f = dict(fields)
                f["email"] = f"dup-{__import__('uuid').uuid4().hex[:8]}@import.local"
                _safe_create_delegate(book_event, f)
                return
            # First occurrence: look up by (invoice, email)
            email    = fields["email"]
            delegate = BookDelegate.objects.filter(invoice=book_event, email=email).first()
            if delegate:
                # `not in ("", None)`, NOT a plain truth test. The old `if v:`
                # dropped every falsey value on the update path, so a Delegate
                # Count of 0 and a discount of 0 could not be imported onto an
                # existing delegate at all — the same class of defect as the
                # max(1, ...) floor, one layer down.
                for k, v in fields.items():
                    if v not in ("", None):
                        setattr(delegate, k, v)
                with transaction.atomic():
                    delegate.save()
            else:
                _safe_create_delegate(book_event, fields)

        def _reconcile_invoice(invoice_number):
            """
            Push the delegates' AGREED person-level values back onto the invoice,
            and set the invoice's delegate_count from the rows that exist.

            WHY BOTH HALVES ARE NEEDED
            The seven columns in PERSON_LEVEL are written on the person, which is
            what the file actually states. But the invoice's own columns are what
            the dashboards, the period window and sync/bookings_sync.py read, so
            leaving them stale would make the same booking read two different ways
            depending on which report you opened. Where every delegate on an
            invoice agrees — the normal case — the value belongs on the invoice
            and the overrides are cleared; an override is then only ever carrying
            a genuine per-delegate difference, which is what makes the 903 mixed
            invoices representable at all.

            Reads EVERY delegate on the invoice from the database rather than only
            this batch's rows, so an invoice whose rows straddle two 500-row
            chunks still settles correctly once the last chunk lands. Idempotent:
            running it again over unchanged rows changes nothing, which is the
            test that the repair and the fix are both real.
            """
            from book_delegate.models import BookDelegate
            invoice = BookEvent.objects.filter(invoice_number=invoice_number).first()
            if invoice is None:
                return
            columns = [c for _, c in PERSON_LEVEL] + ["booking_code", "discount", "attendance"]
            delegates = list(
                BookDelegate.objects.filter(invoice=invoice)
                .only("id", "first_name", "last_name", "email", "phone_number", *columns))
            if not delegates:
                return

            invoice_updates = {}
            clear_on = {}
            for row_key, column in PERSON_LEVEL:
                values = {getattr(d, column) for d in delegates}
                if len(values) == 1:
                    agreed = next(iter(values))
                    if agreed not in ("", None):
                        invoice_updates[row_key] = agreed
                        clear_on[column] = None

            # booking_code, discount and attendance are the three columns the
            # delegate holds DIRECTLY rather than as a nullable override — the
            # delegate's own column is the value every serializer reads, and the
            # invoice carries a copy for the invoice-level reads. So they are
            # pushed up when the delegates agree and NEVER cleared: clearing them
            # would delete the authoritative value rather than an override.
            #
            # Booking Code earns the care. It drives revenue classification,
            # Speaker against Delegate against SpEx (book_event/views.py:195,
            # config/views.py:244), and 868 invoices in the 26 August file carry
            # more than one code across their rows. Under the old invoice-only
            # write, 978 rows had their code replaced by another row's.
            for column in ("booking_code", "discount", "attendance"):
                stated = {getattr(d, column) for d in delegates}
                stated.discard(None)
                stated.discard("")
                if len(stated) == 1:
                    invoice_updates[column] = next(iter(stated))

            # The invoice's delegate_count means "how many delegates are on this
            # invoice" and is derived, never imported; the file's Delegate Count
            # column is the per-person 0/1 flag. This is the same rule the website
            # intake has always applied (webhooks/services.py).
            invoice_updates["delegate_count"] = len(delegates)

            # The invoice's contact summary follows the FIRST delegate, which is
            # what the website intake has always done. The upsert path used to
            # assign it from every row in turn, so on an invoice with four
            # delegates the invoice's Delegate Name was whichever row the file
            # happened to list last — the same row-order dependency as the seven
            # columns above, in the one place a single value is genuinely correct.
            first = min(delegates, key=lambda d: d.id)
            if first.full_name.strip():
                invoice_updates["contact_name"] = first.full_name
            if first.email:
                invoice_updates["contact_email"] = first.email
            if first.phone_number:
                invoice_updates["contact_phone"] = first.phone_number

            if clear_on:
                # updated_at is stamped explicitly: a queryset .update() does not
                # run save(), so an auto_now column does not move on its own, and
                # dataapi's delta sync reads book_delegates.updated_at to decide
                # what changed. Same rule as book_event/serializers.py.
                from django.utils import timezone as _tz
                BookDelegate.objects.filter(invoice=invoice).update(
                    **clear_on, updated_at=_tz.now(),
                )
            for field_name, value in invoice_updates.items():
                setattr(invoice, field_name, value)
            invoice.save(update_fields=list(invoice_updates) + ["updated_at"])

        inserted       = 0
        skipped        = 0
        errors         = []
        skipped_rows   = []
        auto_inv_rows  = []
        # Invoices this call touched, reconciled once at the end rather than per
        # row: an invoice with four delegate rows would otherwise be read back and
        # rewritten four times, and the answer is only correct once its last row
        # has landed anyway.
        touched_invoices = []
        # Tracks how many times each (invoice, email) pair appears in THIS batch.
        # nth > 1 means a different person sharing the same email on the same invoice.
        from collections import defaultdict
        invoice_email_seen = defaultdict(int)

        # Built ONCE for the whole batch, not per row: it loads every user into
        # dictionaries and answers from memory, where the old inline chain issued
        # up to five queries per row.
        from accounts.user_resolution import UserResolver
        _resolver = UserResolver()

        for i, row in enumerate(rows):
            event_code_val = _clean(row, "event_code")   # empty is allowed — never skip

            inv_no = _clean(row, "invoice_number")
            auto_generated_inv = False
            if not inv_no:
                import uuid
                inv_no = f"IMP-{uuid.uuid4().hex[:10].upper()}"
                auto_generated_inv = True

            _email_key = (inv_no, _clean(row, "contact_email").lower())
            invoice_email_seen[_email_key] += 1
            _nth = invoice_email_seen[_email_key]

            # Each row is wrapped in its own savepoint so a failure never
            # aborts the outer PostgreSQL transaction for subsequent rows.
            try:
                with transaction.atomic():
                    from django.utils import timezone

                    # ── Every constrained column, once, through the shared table ──
                    # A cell with content that cannot be read raises here and the
                    # row writes NOTHING. Previously each of these was its own
                    # inline rule with its own silent fallback: a bare int() for
                    # edition, a bare Decimal() in a bare except for discount, a
                    # four-branch if/elif for attendance ending in Pending, and
                    # five .get(..., default) lookups whose default was either a
                    # blank or the stored value. Row order decided the outcome.
                    coerced, row_errors = coerce_row(row)
                    if row_errors:
                        raise ValueError("; ".join(row_errors))

                    created_at_val = None
                    if "created_at" in coerced and coerced["created_at"]:
                        created_at_val = timezone.make_aware(
                            datetime.combine(coerced["created_at"], datetime.min.time()))

                    existing = BookEvent.objects.filter(invoice_number=inv_no).first()

                    if existing:
                        # Upsert: update BookEvent fields when strategy requests it.
                        #
                        # The seven PERSON-LEVEL columns are deliberately ABSENT
                        # from this block now. They are written on the delegate by
                        # _save_delegate and reach the invoice through
                        # _reconcile_invoice, which only puts a value there when
                        # every delegate on the invoice agrees. Writing them here
                        # is what applied one row's value to everybody, and doing
                        # it on the UPSERT path is what made the outcome depend on
                        # whether the invoice already existed.
                        if strategy == "upsert":
                            existing.event_code             = _clean(row, "event_code") or existing.event_code
                            existing.event_name             = _clean(row, "event_name") or existing.event_name
                            existing.company_name           = _clean(row, "company_name") or existing.company_name
                            # contact_name / contact_email / contact_phone are
                            # NOT assigned here. They are the invoice's summary of
                            # its FIRST delegate and _reconcile_invoice sets them
                            # from the rows; assigning them per row meant the last
                            # row in the file won.
                            existing.accounts_contact_email = _clean(row, "accounts_contact_email") or existing.accounts_contact_email
                            existing.discount_code          = _clean(row, "discount_code") or existing.discount_code
                            existing.add_ons                = _clean(row, "add_ons") or existing.add_ons
                            existing.reference              = _clean(row, "reference") or existing.reference
                            if "edition" in coerced:
                                existing.edition = coerced["edition"]
                            if created_at_val:
                                existing.created_at = created_at_val
                            existing.import_batch_id = batch_id
                            existing.save()

                        # Always save delegate — never skip regardless of strategy
                        _save_delegate(existing, row, event_code_val, coerced, nth=_nth)
                        touched_invoices.append(inv_no)

                    else:
                        # New BookEvent
                        # EXACT resolution only — email, then username (including
                        # the "first.last" convention), then exact full name.
                        #
                        # The previous chain ended in first_name__icontains +
                        # last_name__icontains and took .first(). Substring name
                        # matching attributes a booking to the wrong person ("Ana"
                        # matches "Anastasia") and, where several users matched,
                        # the winner was whichever row the database happened to
                        # return. Misses are now COUNTED on the resolver and
                        # reported in the response, not left as a silent NULL.
                        se_name = _clean(row, "sales_executive")
                        sales_exec, _ = _resolver.resolve(se_name)

                        # The person-level columns are NOT passed here, for the
                        # reason given on the upsert path above; they are written
                        # on the delegate and reach the invoice through
                        # _reconcile_invoice. Nor is delegate_count: the file's
                        # column is a per-person 0/1 flag and the invoice's is a
                        # total, so the invoice's is derived from the rows. The old
                        # max(1, int(...)) rewrote 4,636 zeros as ones.
                        #
                        # currency is omitted when the file did not state it, so
                        # the model's own declared default applies rather than
                        # this endpoint asserting USD on the file's behalf; a
                        # currency the file DID state and we cannot read now
                        # errors the row instead of being read as USD.
                        book_event = BookEvent.objects.create(
                            invoice_number         = inv_no,
                            event_code             = event_code_val,
                            event_name             = _clean(row, "event_name"),
                            company_name           = _clean(row, "company_name"),
                            contact_name           = _clean(row, "contact_name"),
                            contact_email          = _clean(row, "contact_email").lower(),
                            contact_phone          = _clean(row, "contact_phone"),
                            accounts_contact_email = _clean(row, "accounts_contact_email"),
                            discount_code          = _clean(row, "discount_code"),
                            add_ons                = _clean(row, "add_ons"),
                            reference              = _clean(row, "reference"),
                            sales_executive        = sales_exec,
                            source                 = BookEvent.Source.MANUAL,
                            import_batch_id        = batch_id,
                            **({"currency": coerced["currency"]} if "currency" in coerced else {}),
                            **({"edition": coerced["edition"]} if "edition" in coerced else {}),
                            **(dict(created_at=created_at_val) if created_at_val else {}),
                        )
                        _save_delegate(book_event, row, event_code_val, coerced, nth=_nth)
                        touched_invoices.append(inv_no)

                        if auto_generated_inv:
                            se_display = _clean(row, "sales_executive") or (
                                f"{sales_exec.get_full_name() or sales_exec.username}" if sales_exec else "Unknown"
                            )
                            auto_inv_rows.append({
                                "invoice_number": inv_no,
                                "event_code":     event_code_val,
                                "sales_executive": se_display,
                                "contact_name":   _clean(row, "contact_name"),
                            })

                inserted += 1

            except Exception as exc:
                errors.append({"row_index": i, "invoice_number": inv_no, "message": str(exc)})

        # ── Keep every touched invoice in step with its delegates ──────────────
        # dict.fromkeys rather than set(): an invoice is reconciled once, in the
        # order it first appeared, so a failure is reported against a predictable
        # row rather than whichever one the hash order happened to reach first.
        for _inv in dict.fromkeys(touched_invoices):
            try:
                with transaction.atomic():
                    _reconcile_invoice(_inv)
            except Exception as exc:
                errors.append({
                    "row_index": None, "invoice_number": _inv,
                    "message": f"delegates saved, but the invoice could not be "
                               f"brought into step with them: {exc}",
                })

        # ── One audit record per call ─────────────────────────────────────────
        # WHY: this endpoint wrote none, so an import that lost seven columns left
        # nothing behind saying who ran it, when, or over how many rows. The Zoho
        # loader has always written one. Best-effort by design — a failure to log
        # must not fail rows that are already committed — but the batch id is
        # returned either way, so the rows remain findable.
        try:
            from accounts.audit import log_import_batch
            log_import_batch(
                user=request.user, module_label="BOOKINGS", batch_id=batch_id,
                counts={
                    "rows": len(rows), "inserted": inserted,
                    "skipped_duplicates": skipped, "errors": len(errors),
                },
                detail=f"batch {batch_number}, duplicate strategy {strategy}",
            )
        except Exception:
            logger.warning("bulk_import: audit record failed for batch %s", batch_id, exc_info=True)

        # Send alert email for any auto-generated invoice numbers.
        #
        # Gated on IMPORT_ALERT_EMAILS_ENABLED, which defaults False. This fires
        # once per CALL, and the browser chunks an import at 500 rows per call, so
        # a large load would otherwise deliver one message per chunk containing a
        # row with no invoice number. The settings read is deliberately inside the
        # branch and via django.conf.settings so the flag can be toggled without a
        # restart. See config/settings.py:IMPORT_ALERT_EMAILS_ENABLED.
        from django.conf import settings as django_settings
        if auto_inv_rows and django_settings.IMPORT_ALERT_EMAILS_ENABLED:
            try:
                from django.core.mail import send_mail
                recipient = getattr(django_settings, "IMPORT_ALERT_EMAIL", "harrison.peck@iq-hub.com")
                lines = []
                for entry in auto_inv_rows:
                    lines.append(
                        f"  • Auto-Invoice: {entry['invoice_number']}"
                        f"  |  Event Code: {entry['event_code']}"
                        f"  |  Added by: @{entry['sales_executive']}"
                        + (f"  |  Contact: {entry['contact_name']}" if entry['contact_name'] else "")
                    )
                body = (
                    f"Hi Harrison,\n\n"
                    f"{len(auto_inv_rows)} new booking entr{'y was' if len(auto_inv_rows) == 1 else 'ies were'} "
                    f"imported without an Invoice Number — auto-generated IDs assigned:\n\n"
                    + "\n".join(lines)
                    + "\n\nThese entries were created via the Smart Import tool and may need manual invoice numbers assigned.\n\n"
                    f"— Linq CRM"
                )
                send_mail(
                    subject=f"[Linq CRM] {len(auto_inv_rows)} Import{'s' if len(auto_inv_rows) != 1 else ''} Without Invoice Number",
                    message=body,
                    from_email=django_settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[recipient],
                    fail_silently=True,
                )
            except Exception:
                pass  # never block the response over an email failure

        return Response({
            "success":            len(errors) == 0,
            "batch_number":       batch_number,
            # Returned so the caller can reuse it for the remaining chunks, and so
            # every row this import wrote can be listed from the id alone:
            #   BookEvent.objects.filter(import_batch_id=...)
            #   BookDelegate.objects.filter(import_batch_id=...)
            "import_batch_id":    str(batch_id),
            "inserted":           inserted,
            "skipped_duplicates": skipped,
            # Every name on a booking is expected to correspond to a real
            # account, so a rate materially below 1.0 is a defect to chase, not
            # missing data. Surfaced rather than left as silent NULLs.
            "sales_executive_resolution": _resolver.report(limit=25),
            "errors":             errors[:20],
            "skipped_rows":       skipped_rows,
        })

    @action(detail=False, methods=["get"], url_path="webhook_logs",
            permission_classes=[IsAdminRole])
    def webhook_logs(self, request):
        """GET /api/invoices/webhook_logs/ — paginated webhook activity (admin only)."""
        from .models import WebhookLog as WL
        qs = WL.objects.all()
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        page = self.paginate_queryset(qs)
        data = [
            {
                "id":             log.id,
                "status":         log.status,
                "http_status":    log.http_status,
                "invoice_number": log.invoice_number,
                "event_code":     log.event_code,
                "source_ip":      log.source_ip,
                "error_message":  log.error_message,
                "payload":        log.payload,
                "response":       log.response,
                "created_at":     log.created_at,
            }
            for log in (page if page is not None else qs)
        ]
        return self.get_paginated_response(data) if page is not None else Response(data)
