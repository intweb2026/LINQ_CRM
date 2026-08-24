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
BOOKING_IMPORT_FIELDS = (
    ("invoice_number", "Invoice Number", ("Invoice No", "Invoice #")),
    ("event_code", "Event Code", ()),
    ("event_name", "Event Name", ()),
    ("booking_code", "Booking Code", ()),
    ("edition", "Edition", ("Year",)),
    ("company_name", "Company", ("Company Name", "Organisation")),
    ("contact_name", "Delegate Name", ("Name", "Attendee", "Full Name")),
    ("position", "Job Title / Position", ("Designation", "Job Title")),
    ("accounts_contact_email", "Accounts Email", ("Accounts Contact Email",)),
    ("contact_email", "Email", ("Email Address",)),
    ("contact_phone", "Direct Line", ("Phone", "Phone Number", "Mobile")),
    ("request_date", "Request Date", ()),
    ("invoice_date", "Invoice Date", ()),
    ("payment_date", "Payment Date", ()),
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
    ("attendance", "Attendance", ("Attended", "Confirmed")),
    ("add_ons", "Add-Ons", ("Addons",)),
    ("reference", "Reference", ("Payment Reference",)),
    ("notes", "Notes", ("Comments", "Remarks")),
    ("sales_executive", "Sales Executive (username/email)", ("Sales Exec", "Sales Rep", "Sales Team")),
    ("created_at", "Added Time", ("Created At", "Created Time")),
)


class BookEventViewSet(RBACMixin, viewsets.ModelViewSet):
    permission_classes = [crm_permission("bookings")]
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
            payment_status = payment_status_map.get(incoming_ps, BookEvent.PaymentStatus.PENDING)

            tier_map     = {v.lower(): v for v in BookEvent.TicketTier.values}
            pof_map      = {v.lower(): v for v in BookEvent.PaidOrFree.values}
            ticket_tier  = tier_map.get(d.get("TicketTier", "").strip().lower(), "")
            paid_or_free = pof_map.get(d.get("PaidOrFree",  "").strip().lower(), "")

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
                    currency               = d.get("Currency", "USD"),
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
                    d_tier = tier_map.get(dp.get("TicketTier", "").strip().lower(), "") or None
                    d_pof  = pof_map.get(dp.get("PaidOrFree",  "").strip().lower(), "") or None
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
        Body: { rows: [...], duplicate_strategy: "skip"|"upsert", batch_number: int }

        Accepted columns are BOOKING_IMPORT_FIELDS, published by import_schema
        above. A test asserts the two agree — see tests_import_schema.py.
        """
        rows               = request.data.get("rows", [])
        strategy           = request.data.get("duplicate_strategy", "skip")
        batch_number       = request.data.get("batch_number", 1)

        if not rows:
            return Response({"success": False, "detail": "No rows provided."}, status=400)

        # ── helpers ───────────────────────────────────────────────────────────
        DATE_FMTS = ["%d %b %Y", "%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y",
                     "%d-%b-%Y", "%d %B %Y"]

        def _parse_date(val):
            if not val:
                return None
            s = str(val).strip()
            for fmt in DATE_FMTS:
                try:
                    return datetime.strptime(s, fmt).date()
                except ValueError:
                    continue
            return None

        def _clean(d, key, default=""):
            return str(d.get(key) or default).strip()

        def _delegate_fields(row, ev_code):
            name_raw = _clean(row, "contact_name")
            parts    = name_raw.split(" ", 1) if name_raw else []
            
            try:
                d_discount = Decimal(str(row.get("discount") or "0.00").strip() or "0.00")
            except Exception:
                d_discount = Decimal("0.00")

            attendance_raw = _clean(row, "attendance")
            attendance_normalized = "Pending"
            if attendance_raw.lower() in ("yes", "true", "1", "confirmed"):
                attendance_normalized = "Confirmed"
            elif attendance_raw.lower() in ("no-show", "noshow"):
                attendance_normalized = "No-show"
            elif attendance_raw.lower() == "cancelled":
                attendance_normalized = "Cancelled"

            return dict(
                event_code=ev_code,
                first_name=parts[0] if parts else "",
                last_name=parts[1] if len(parts) > 1 else "",
                email=_clean(row, "contact_email").lower(),
                phone_number=_clean(row, "contact_phone"),
                company_name_raw=_clean(row, "company_name"),
                position=_clean(row, "position"),
                notes=_clean(row, "notes"),
                discount=d_discount,
                add_ons=_clean(row, "add_ons"),
                reference=_clean(row, "reference"),
                attendance=attendance_normalized,
            )

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

        def _save_delegate(book_event, row, ev_code, nth=1):
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
            fields = _delegate_fields(row, ev_code)
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
                for k, v in fields.items():
                    if v:
                        setattr(delegate, k, v)
                with transaction.atomic():
                    delegate.save()
            else:
                _safe_create_delegate(book_event, fields)

        ps_map   = {v.lower(): v for v in BookEvent.PaymentStatus.values}
        tier_map = {v.lower(): v for v in BookEvent.TicketTier.values}
        pof_map  = {v.lower(): v for v in BookEvent.PaidOrFree.values}
        pt_map   = {v.lower(): v for v in BookEvent.PaymentType.values}
        cur_map  = {v.lower(): v for v in BookEvent.Currency.values}

        inserted       = 0
        skipped        = 0
        errors         = []
        skipped_rows   = []
        auto_inv_rows  = []
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
                    from decimal import Decimal
                    from django.utils import timezone

                    # Parse new fields for the row.
                    #
                    # edition goes through parse_edition, which BOUNDS it to a
                    # plausible year. Previously this was a bare int(), and since
                    # `edition` is an IntegerField an Excel serial like 45678
                    # raised nothing and was stored verbatim as a 45,678th
                    # edition. Out-of-range now ERRORS the row rather than
                    # writing nonsense — same rule the loader applies.
                    from accounts.import_common import parse_edition

                    edition_val, _edition_err = parse_edition(row.get("edition"))
                    if _edition_err:
                        raise ValueError(f"edition: {_edition_err}")

                    try:
                        discount_val = Decimal(str(row.get("discount") or "0.00").strip() or "0.00")
                    except Exception:
                        discount_val = Decimal("0.00")

                    attendance_raw = _clean(row, "attendance")
                    attendance_normalized = "Pending"
                    if attendance_raw.lower() in ("yes", "true", "1", "confirmed"):
                        attendance_normalized = "Confirmed"
                    elif attendance_raw.lower() in ("no-show", "noshow"):
                        attendance_normalized = "No-show"
                    elif attendance_raw.lower() == "cancelled":
                        attendance_normalized = "Cancelled"

                    created_at_val = None
                    if row.get("created_at"):
                        parsed_dt = _parse_date(row.get("created_at"))
                        if parsed_dt:
                            created_at_val = timezone.make_aware(datetime.combine(parsed_dt, datetime.min.time()))

                    existing = BookEvent.objects.filter(invoice_number=inv_no).first()

                    if existing:
                        # Upsert: update BookEvent fields when strategy requests it
                        if strategy == "upsert":
                            existing.event_code             = _clean(row, "event_code") or existing.event_code
                            existing.event_name             = _clean(row, "event_name") or existing.event_name
                            existing.booking_code           = _clean(row, "booking_code") or existing.booking_code
                            existing.company_name           = _clean(row, "company_name") or existing.company_name
                            existing.contact_name           = _clean(row, "contact_name") or existing.contact_name
                            existing.contact_email          = _clean(row, "contact_email").lower() or existing.contact_email
                            existing.contact_phone          = _clean(row, "contact_phone") or existing.contact_phone
                            existing.accounts_contact_email = _clean(row, "accounts_contact_email") or existing.accounts_contact_email
                            existing.payment_status         = ps_map.get(_clean(row, "payment_status").lower(), existing.payment_status)
                            existing.paid_or_free           = pof_map.get(_clean(row, "paid_or_free").lower(), existing.paid_or_free)
                            existing.payment_type           = pt_map.get(_clean(row, "payment_type").lower(), existing.payment_type)
                            existing.ticket_tier            = tier_map.get(_clean(row, "ticket_tier").lower(), existing.ticket_tier)
                            existing.discount_code          = _clean(row, "discount_code") or existing.discount_code
                            existing.add_ons                = _clean(row, "add_ons") or existing.add_ons
                            existing.reference              = _clean(row, "reference") or existing.reference
                            existing.edition                = edition_val or existing.edition
                            existing.discount               = discount_val or existing.discount
                            existing.attendance             = attendance_normalized or existing.attendance
                            if created_at_val:
                                existing.created_at = created_at_val
                            pd = _parse_date(row.get("payment_date"))
                            if pd: existing.payment_date = pd
                            rd = _parse_date(row.get("request_date"))
                            if rd: existing.request_date = rd
                            id_ = _parse_date(row.get("invoice_date"))
                            if id_: existing.invoice_date = id_
                            existing.save()

                        # Always save delegate — never skip regardless of strategy
                        _save_delegate(existing, row, event_code_val, nth=_nth)

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
                        try:
                            dc = max(1, int(row.get("delegate_count") or 1))
                        except (ValueError, TypeError):
                            dc = 1

                        book_event = BookEvent.objects.create(
                            invoice_number         = inv_no,
                            event_code             = event_code_val,
                            event_name             = _clean(row, "event_name"),
                            booking_code           = _clean(row, "booking_code"),
                            request_date           = _parse_date(row.get("request_date")),
                            invoice_date           = _parse_date(row.get("invoice_date")),
                            company_name           = _clean(row, "company_name"),
                            contact_name           = _clean(row, "contact_name"),
                            contact_email          = _clean(row, "contact_email").lower(),
                            contact_phone          = _clean(row, "contact_phone"),
                            accounts_contact_email = _clean(row, "accounts_contact_email"),
                            payment_status         = ps_map.get(_clean(row, "payment_status").lower(), BookEvent.PaymentStatus.PENDING),
                            paid_or_free           = pof_map.get(_clean(row, "paid_or_free").lower(), ""),
                            payment_date           = _parse_date(row.get("payment_date")),
                            payment_type           = pt_map.get(_clean(row, "payment_type").lower(), ""),
                            ticket_tier            = tier_map.get(_clean(row, "ticket_tier").lower(), ""),
                            currency               = cur_map.get(_clean(row, "currency").lower(), BookEvent.Currency.USD),
                            discount_code          = _clean(row, "discount_code"),
                            add_ons                = _clean(row, "add_ons"),
                            reference              = _clean(row, "reference"),
                            delegate_count         = dc,
                            sales_executive        = sales_exec,
                            source                 = BookEvent.Source.MANUAL,
                            edition                = edition_val,
                            discount               = discount_val,
                            attendance             = attendance_normalized,
                            **(dict(created_at=created_at_val) if created_at_val else {}),
                        )
                        _save_delegate(book_event, row, event_code_val, nth=_nth)

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
