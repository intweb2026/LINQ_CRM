"""
historical_event_registry/edition_service.py

HistoricalEditionDataService  — per-year operational metrics for a given event code
HistoricalMetricsAggregator   — validates and retries cross-year aggregation accuracy
"""
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from django.utils import timezone

from .models import HistoricalEventReference
from .utils import normalize_event_code, booking_code_regex

logger = logging.getLogger(__name__)

# Payment statuses that count as "paid" (all others = unpaid/pending)
PAID_STATUSES = {"Paid", "Paid (Transferred)", "Credit Transferred", "Free"}

MAX_VALIDATION_ITERATIONS = 3


class HistoricalEditionDataService:
    """
    For an event identified by event_code, finds all historical editions
    (via HistoricalEventReference) and aggregates live operational metrics
    from BookEvent and BookDelegate for each year.
    """

    def __init__(self, event_code: str):
        # Normalize so "HDU - VV" → "HDU" to match stored refs and bookings
        self.event_code = normalize_event_code(event_code)

    def get_editions(self) -> List[Dict[str, Any]]:
        """
        Returns edition dicts sorted descending by year.
        Each dict contains: year, location, event_code, references, metrics.
        """
        refs = (
            HistoricalEventReference.objects
            .filter(normalized_event_code=self.event_code)
            .select_related("event")
            .order_by("-event_year", "event_month")
        )

        # Group references by year, keeping the best location per year
        years_data: Dict[int, Dict] = {}
        for ref in refs:
            yr = ref.event_year
            if yr not in years_data:
                years_data[yr] = {"references": [], "location": ""}
            years_data[yr]["references"].append(ref)
            if not years_data[yr]["location"] and ref.event_location:
                years_data[yr]["location"] = ref.event_location

        editions = []
        for yr in sorted(years_data.keys(), reverse=True):
            data = years_data[yr]
            editions.append({
                "year": yr,
                "location": data["location"],
                "event_code": self.event_code,
                "references": [self._serialize_ref(r) for r in data["references"]],
                "metrics": self._compute_metrics(yr),
            })

        return editions

    def _compute_metrics(self, year: int) -> Dict[str, Any]:
        from book_event.models import BookEvent
        from book_delegate.models import BookDelegate

        code_pattern = booking_code_regex(self.event_code, year=year)
        bookings_qs = BookEvent.objects.filter(
            event_code__iregex=code_pattern,
        )

        total_bookings = bookings_qs.count()
        paid_entries   = bookings_qs.filter(payment_status__in=PAID_STATUSES).count()
        unpaid_entries = total_bookings - paid_entries

        delegates_qs = BookDelegate.objects.filter(
            event_code__iregex=code_pattern,
        )
        total_delegates = delegates_qs.count()

        from django.db.models import Max
        last_booking_dt  = bookings_qs.aggregate(v=Max("created_at"))["v"]
        last_payment_date = bookings_qs.filter(
            payment_status__in=PAID_STATUSES
        ).aggregate(v=Max("payment_date"))["v"]

        # Activity windows: relative to the last booking created_at
        ref_date = (
            last_booking_dt.date() if last_booking_dt and hasattr(last_booking_dt, "date")
            else timezone.localdate()
        )

        paid_qs = bookings_qs.filter(payment_status__in=PAID_STATUSES)

        booking_activity_7  = bookings_qs.filter(created_at__date__gte=ref_date - timedelta(days=7)).count()
        booking_activity_15 = bookings_qs.filter(created_at__date__gte=ref_date - timedelta(days=15)).count()
        booking_activity_30 = bookings_qs.filter(created_at__date__gte=ref_date - timedelta(days=30)).count()

        payment_activity_7  = paid_qs.filter(payment_date__gte=ref_date - timedelta(days=7)).count()
        payment_activity_15 = paid_qs.filter(payment_date__gte=ref_date - timedelta(days=15)).count()
        payment_activity_30 = paid_qs.filter(payment_date__gte=ref_date - timedelta(days=30)).count()

        latest_bookings = list(
            bookings_qs.order_by("-created_at").values(
                "invoice_number", "company_name", "contact_name",
                "payment_status", "total_amount", "currency",
                "delegate_count", "created_at",
            )[:10]
        )
        latest_delegates = list(
            delegates_qs.order_by("-created_at").values(
                "first_name", "last_name", "email", "company_name_raw",
                "attendance", "ticket_package", "created_at",
            )[:10]
        )

        # Serialize datetime/date objects to ISO strings for JSON safety
        for b in latest_bookings:
            if b.get("created_at") and hasattr(b["created_at"], "isoformat"):
                b["created_at"] = b["created_at"].isoformat()
            if b.get("total_amount") is not None:
                b["total_amount"] = float(b["total_amount"])
        for d in latest_delegates:
            if d.get("created_at") and hasattr(d["created_at"], "isoformat"):
                d["created_at"] = d["created_at"].isoformat()

        return {
            "total_bookings":        total_bookings,
            "paid_entries":          paid_entries,
            "unpaid_entries":        unpaid_entries,
            "total_delegates":       total_delegates,
            "last_booking_date":     last_booking_dt.isoformat() if last_booking_dt else None,
            "last_payment_date":     last_payment_date.isoformat() if last_payment_date else None,
            "booking_activity_7_days":  booking_activity_7,
            "booking_activity_15_days": booking_activity_15,
            "booking_activity_30_days": booking_activity_30,
            "payment_activity_7_days":  payment_activity_7,
            "payment_activity_15_days": payment_activity_15,
            "payment_activity_30_days": payment_activity_30,
            "latest_bookings":   latest_bookings,
            "latest_delegates":  latest_delegates,
        }

    @staticmethod
    def _serialize_ref(ref: HistoricalEventReference) -> Dict[str, Any]:
        return {
            "id":                    ref.id,
            "original_event_code":   ref.original_event_code,
            "normalized_event_code": ref.normalized_event_code,
            "event_year":            ref.event_year,
            "event_month":           ref.event_month,
            "event_location":        ref.event_location,
            "source_pdf":            ref.source_pdf,
            "source_page":           ref.source_page,
            "verification_status":   ref.verification_status,
            "matched_confidence":    ref.matched_confidence,
        }


class HistoricalMetricsAggregator:
    """
    Validates per-year metrics for an event code and re-aggregates if issues are found.
    Implements the retry loop from the spec (max 3 iterations).
    """

    def __init__(self, event_code: str):
        self.event_code = event_code.strip().upper()
        self.iterations = 0
        self.issues: List[Dict[str, Any]] = []

    def validate_and_aggregate(self) -> Dict[str, Any]:
        service  = HistoricalEditionDataService(self.event_code)
        editions = service.get_editions()

        while self.iterations < MAX_VALIDATION_ITERATIONS:
            self.iterations += 1
            edition_issues: List[Dict] = []

            for edition in editions:
                year    = edition["year"]
                metrics = edition["metrics"]
                found   = self._validate_edition(year, metrics, len(edition["references"]))
                if found:
                    edition_issues.extend(found)
                    # Re-compute metrics for this year and restart the loop
                    edition["metrics"] = service._compute_metrics(year)

            self.issues = edition_issues
            if not edition_issues:
                break  # All editions pass validation

        return {
            "event_code":        self.event_code,
            "editions":          editions,
            "validation_passed": len(self.issues) == 0,
            "iterations":        self.iterations,
            "issues":            self.issues,
        }

    def _validate_edition(self, year: int, metrics: Dict, ref_count: int) -> List[Dict]:
        issues = []
        tb   = metrics.get("total_bookings", 0)
        paid = metrics.get("paid_entries", 0)
        unpaid = metrics.get("unpaid_entries", 0)

        # paid + unpaid must equal total (always true with our formula, but validate defensively)
        if paid + unpaid != tb:
            issues.append({
                "year": year,
                "event_code": self.event_code,
                "type": "booking_count_mismatch",
                "metric": "paid_entries + unpaid_entries",
                "detail": (
                    f"paid({paid}) + unpaid({unpaid}) = {paid + unpaid} "
                    f"but total_bookings = {tb}"
                ),
                "aggregation_source": "BookEvent.payment_status",
                "suggested_fix": (
                    "Check BookEvent.payment_status values — "
                    "unknown statuses are counted in unpaid_entries."
                ),
            })

        # Historical references exist but no bookings → data gap
        if ref_count > 0 and tb == 0:
            issues.append({
                "year": year,
                "event_code": self.event_code,
                "type": "no_booking_data",
                "metric": "total_bookings",
                "detail": (
                    f"{ref_count} historical reference(s) found for {year} "
                    f"but 0 BookEvent records match event_code+year."
                ),
                "aggregation_source": "BookEvent.event_code (year suffix)",
                "suggested_fix": (
                    f"Verify BookEvent records exist with event_code matching '{self.event_code}' "
                    f"and ending in '{str(year)[-2:]}' or '{year}' (e.g., '{self.event_code}{str(year)[-2:]}')."
                ),
            })

        # Delegates should be >= bookings (each booking has at least 1 delegate)
        delegates = metrics.get("total_delegates", 0)
        if tb > 0 and delegates < tb:
            issues.append({
                "year": year,
                "event_code": self.event_code,
                "type": "delegate_undercount",
                "metric": "total_delegates",
                "detail": (
                    f"total_delegates({delegates}) < total_bookings({tb}). "
                    f"Each booking should have at least 1 delegate."
                ),
                "aggregation_source": "BookDelegate.event_code (year suffix)",
                "suggested_fix": (
                    "Check that BookDelegate records are linked to BookEvent invoices "
                    f"with the correct event_code suffix for {year}."
                ),
            })

        return issues
