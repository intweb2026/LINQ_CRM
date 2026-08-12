import logging

from django.db import transaction
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.bulk_update import BulkUpdateMixin
from accounts.filter_spec import FilterSpecMixin, build_filter_spec_fields
from accounts.ordering import StableOrderingFilter
from accounts.permissions import RBACMixin, IsAdminRole
from accounts.crm_permissions import crm_permission
from book_event.models import BookEvent
from .models import BookDelegate
from .serializers import (
    BookDelegateListSerializer, BookDelegateDetailSerializer, BookDelegateWriteSerializer,
)
from .filters import BookDelegateFilter

logger = logging.getLogger(__name__)


from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters

class BookDelegateViewSet(FilterSpecMixin, BulkUpdateMixin, RBACMixin, viewsets.ModelViewSet):
    permission_classes = [crm_permission("bookings")]

    # ── Compound filter spec ──────────────────────────────────────────────────
    # The five person-level fields are RESOLVED: the table shows the delegate
    # override when set, else the invoice's value, so filtering the raw override
    # column would miss every inheriting row — currently all of them, since
    # delegate_payment_status is NULL on every row in the database.
    _RESOLVED = {
        "payment_status": ("delegate_payment_status", "invoice__payment_status",
                           "choice", "Payment Status", list(BookEvent.PaymentStatus.values)),
        "payment_type":   ("delegate_payment_type", "invoice__payment_type",
                           "choice", "Payment Type", list(BookEvent.PaymentType.values)),
        "ticket_tier":    ("delegate_ticket_tier", "invoice__ticket_tier",
                           "choice", "Ticket Tier", list(BookEvent.TicketTier.values)),
        "paid_or_free":   ("delegate_paid_or_free", "invoice__paid_or_free",
                           "choice", "Paid / Free", list(BookEvent.PaidOrFree.values)),
        "payment_date":   ("delegate_payment_date", "invoice__payment_date",
                           "date", "Payment Date", None),
    }

    filter_spec_fields = {
        **build_filter_spec_fields(
            BookDelegate,
            # invoice is the FK object itself; its columns are exposed by name below
            exclude={"invoice", "delegate_number"},
            labels={"company_name_raw": "Company (raw)", "event_code": "Event Code"},
        ),
        # Person-level resolved fields
        **{
            key: {
                "type": ftype, "label": label,
                **({"choices": choices} if choices else {}),
                "resolved": {"override": override, "invoice": invoice},
            }
            for key, (override, invoice, ftype, label, choices) in _RESOLVED.items()
        },
        # Invoice-sourced scalars the table shows alongside each delegate
        "invoice_number": {"type": "text", "label": "Invoice Number",
                           "source": "invoice__invoice_number"},
        "booking_code":   {"type": "text", "label": "Booking Code",
                           "source": "invoice__booking_code"},
        "company_name":   {"type": "text", "label": "Company",
                           "source": "invoice__company_name"},
        "event_name":     {"type": "text", "label": "Event Name",
                           "source": "invoice__event_name"},
        "request_date":   {"type": "date", "label": "Request Date",
                           "source": "invoice__request_date", "nullable": True},
        "invoice_date":   {"type": "date", "label": "Invoice Date",
                           "source": "invoice__invoice_date", "nullable": True},
        "total_amount":   {"type": "number", "label": "Total Amount",
                           "source": "invoice__total_amount", "nullable": True},
        "currency":       {"type": "choice", "label": "Currency",
                           "source": "invoice__currency",
                           "choices": list(BookEvent.Currency.values)},
        "source":         {"type": "choice", "label": "Source",
                           "source": "invoice__source",
                           "choices": list(BookEvent.Source.values)},
    }

    # ── Mass update ───────────────────────────────────────────────────────────
    # Every row-group key is a delegate_* OVERRIDE field, never the bare name.
    # payment_status / payment_date / invoice_number are read-only @property on
    # BookDelegate (models.py:101-111) and would raise on write, and the 21
    # invoice-sourced fields on BookDelegateListSerializer (serializers.py:48-76)
    # are read_only — DRF discards those silently. A wrong name here fails quietly.
    #
    # The delegate_* overrides are bare CharFields with no choices of their own
    # (models.py:73-77), so every choices list below is sourced from the
    # corresponding BookEvent enum — the invoice value each override shadows.
    # attendance is the exception: it has its own choices on BookDelegate:44.
    bulk_update_label       = "delegates"
    bulk_update_parent_path = "invoice"
    bulk_update_fields = {
        # ── Row group: per-delegate overrides ─────────────────────────────────
        # nullable mirrors null=True on the model (models.py:73-77). Clearing an
        # override makes the delegate inherit from the invoice again, which is a
        # real thing a rep needs to undo a mistaken override.
        "delegate_payment_status": {
            "group": "row", "type": "choice", "label": "Payment Status",
            "choices": list(BookEvent.PaymentStatus.values), "nullable": True,
        },
        "delegate_payment_type": {
            "group": "row", "type": "choice", "label": "Payment Type",
            "choices": list(BookEvent.PaymentType.values), "nullable": True,
        },
        "delegate_ticket_tier": {
            "group": "row", "type": "choice", "label": "Ticket Tier",
            "choices": list(BookEvent.TicketTier.values), "nullable": True,
        },
        "delegate_paid_or_free": {
            "group": "row", "type": "choice", "label": "Paid / Free",
            "choices": list(BookEvent.PaidOrFree.values), "nullable": True,
        },
        "delegate_payment_date": {
            "group": "row", "type": "date", "label": "Payment Date",
            "nullable": True,
        },
        # attendance is NOT nullable — CharField(default=Pending) at models.py:44
        "attendance": {
            "group": "row", "type": "choice", "label": "Attendance",
            "choices": list(BookDelegate.Attendance.values),
        },
        # ── Parent group: written on the shared invoice ───────────────────────
        # currency is NOT nullable — CharField(default=USD) at book_event:94
        "invoice.currency": {
            "group": "parent", "type": "choice", "label": "Currency",
            "choices": list(BookEvent.Currency.values),
        },
    }
    bulk_update_side_effects = {
        ("delegate_payment_status", "Cancelled"): "also sets delegate_count → 0",
    }
    # StableOrderingFilter, not the stock one: the default sort
    # (-_sort_request_date) is heavily tied, and without a pk tiebreaker
    # pagination duplicates and skips rows. See accounts/ordering.py.
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, StableOrderingFilter]
    filterset_class = BookDelegateFilter
    search_fields   = [
        "first_name", "last_name", "email", "position",
        "invoice__invoice_number", "event_code", "company_name_raw",
    ]
    ordering_fields = [
        "id",   # was silently dropped as an unknown field before
        "_sort_invoice", "_sort_status", "_sort_date", "_sort_name", "_sort_request_date",
        "first_name", "last_name", "email", "event_code", "attendance", "created_at",
        "position", "company_name_raw",
        # Resolved person-level ordering. These are what the table must use for the
        # five override-backed columns — _sort_status orders by the invoice value and
        # therefore disagrees with the displayed cell. DRF silently DROPS an ordering
        # term that is not listed here, which is why they are named explicitly.
        "_sort_effective_payment_status", "_sort_effective_payment_type",
        "_sort_effective_paid_or_free", "_sort_effective_ticket_tier",
        "_sort_effective_payment_date",
    ]
    ordering        = ["-_sort_request_date"]

    def get_queryset(self):
        from django.db.models import F, Value
        from django.db.models.functions import Coalesce, Concat, NullIf
        qs = BookDelegate.objects.select_related("invoice__sales_executive", "company")
        qs = qs.annotate(
            _sort_invoice=F("invoice__invoice_number"),
            # NOTE: _sort_status orders by the INVOICE column, which is NOT what the
            # Payment Status cell displays. Kept because existing callers pass it,
            # but the table should use _sort_effective_payment_status below.
            _sort_status=F("invoice__payment_status"),
            _sort_date=F("invoice__invoice_date"),
            _sort_request_date=F("invoice__request_date"),
            _sort_name=Concat(F("first_name"), Value(" "), F("last_name")),
            # ── Ordering over the RESOLVED person-level values ────────────────
            # Same expression as accounts/filter_spec.py _resolved_expression and
            # the serializer's effective_* fields:
            #   COALESCE(NULLIF(<override>, ''), <invoice column>)
            # Without these, sorting Payment Status ordered by the invoice value
            # while the cell showed the resolved one, so the header claimed an order
            # the rows did not have as soon as any delegate carried an override.
            # See accounts/tests_resolved_ordering.py for the reproduction.
            _sort_effective_payment_status=Coalesce(
                NullIf("delegate_payment_status", Value("")), "invoice__payment_status"),
            _sort_effective_payment_type=Coalesce(
                NullIf("delegate_payment_type", Value("")), "invoice__payment_type"),
            _sort_effective_paid_or_free=Coalesce(
                NullIf("delegate_paid_or_free", Value("")), "invoice__paid_or_free"),
            _sort_effective_ticket_tier=Coalesce(
                NullIf("delegate_ticket_tier", Value("")), "invoice__ticket_tier"),
            # A DateField cannot hold '', so NULLIF would be a type error here.
            _sort_effective_payment_date=Coalesce(
                "delegate_payment_date", "invoice__payment_date"),
        )
        return self.rbac_filter_invoice(qs)

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return BookDelegateWriteSerializer
        if self.action == "retrieve":
            return BookDelegateDetailSerializer
        return BookDelegateListSerializer

    @action(detail=False, methods=["get"], url_path=r"by_invoice/(?P<invoice_number>[^/.]+)")
    def by_invoice(self, request, invoice_number=None):
        """GET /api/delegates/by_invoice/{invoice_number}/"""
        qs = self.get_queryset().filter(invoice__invoice_number=invoice_number)
        return Response(BookDelegateListSerializer(qs, many=True).data)

    @action(detail=False, methods=["post"], url_path="bulk_delete",
            permission_classes=[IsAdminRole])
    def bulk_delete(self, request):
        """
        Delete up to 1000 delegate records by ID, RBAC-SCOPED.

        Previously this ran `BookDelegate.objects.filter(id__in=ids)` — the default
        manager, not the scoped queryset — so any caller who passed the IsAdminRole
        gate could delete ANY delegate row by guessing its id, regardless of event
        assignment. IsAdminRole admits HP, any `is_admin` user, and any custom role
        with is_all_access, so that was wider than the role's read access.

        Resolving through self.get_queryset() routes the deletion through
        rbac_filter_invoice(), so a caller can only ever delete rows already inside
        their scope. IDs outside it are silently skipped rather than 403ing the
        whole batch — the response reports requested vs permitted so a partial
        delete is visible rather than looking like a success.

        Two-step (ids first, then a clean manager) because get_queryset() carries
        annotations and select_related, and .delete() on an annotated queryset is
        not reliable. permitted_ids is already scoped, so scoping is preserved.
        """
        from accounts.models import ActionLog
        ids = request.data.get("ids", [])
        if not isinstance(ids, list) or not ids:
            return Response({"detail": "ids list required"}, status=400)
        if len(ids) > 1000:
            return Response({"detail": "Maximum 1000 IDs per request"}, status=400)

        permitted_ids = list(
            self.get_queryset().filter(id__in=ids).values_list("id", flat=True)
        )
        skipped = len(set(ids)) - len(permitted_ids)
        if skipped:
            logger.warning(
                "bulk_delete: %s requested %d delegate ids, %d were out of scope",
                request.user.username, len(set(ids)), skipped,
            )
        if not permitted_ids:
            return Response(
                {"detail": "None of the requested records are in your scope.",
                 "deleted": 0, "requested": len(ids), "permitted": 0},
                status=403,
            )

        with transaction.atomic():
            qs = BookDelegate.objects.filter(id__in=permitted_ids)
            count = qs.count()
            ActionLog.objects.create(
                user    = request.user,
                action  = f"Bulk deleted {count} booking delegates",
                # Full permitted list, not ids[:50]: the audit trail should record
                # what was actually deleted, not a truncated sample of what was asked.
                details = (
                    f"requested={len(ids)} permitted={count} out_of_scope={skipped}\n"
                    f"ids={sorted(permitted_ids)}"
                ),
            )
            qs.delete()
        return Response({
            "deleted": count, "requested": len(ids), "permitted": count,
            "out_of_scope": skipped,
        })

    @action(detail=True, methods=["patch"], url_path="update_attendance")
    def update_attendance(self, request, pk=None):
        """PATCH /api/delegates/{id}/update_attendance/"""
        delegate   = self.get_object()
        attendance = request.data.get("attendance")
        choices    = dict(BookDelegate.Attendance.choices)
        if attendance not in choices:
            return Response({"detail": f"Invalid attendance: {attendance}"}, status=400)
        delegate.attendance = attendance
        delegate.save(update_fields=["attendance", "updated_at"])
        return Response({"id": delegate.id, "attendance": delegate.attendance})
