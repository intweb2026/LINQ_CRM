"""
event_performance/active_edition_service.py

CurrentActiveEditionResolver  — groups Event records by master code; finds active edition
EventPerformanceInsightEngine — generates textual insights from edition metrics progression
"""
import re
from datetime import date
from typing import Any, Dict, List, Optional

from events.models import Event


def normalize_master_code(event_code: str) -> str:
    """
    Returns the canonical master code: first 3 alphabetic characters (uppercase).

    'ACU25'         → 'ACU'
    'ACU - RS26'    → 'ACU'
    'MMU/GS - JS26' → 'MMU'
    'DDU - PT'      → 'DDU'
    'WSE - EU'      → 'WSE'
    'DDU'           → 'DDU'
    """
    if not event_code:
        return ""
    alpha = re.sub(r"[^A-Za-z]", "", event_code)
    mc = alpha[:3].upper()
    return mc


def extract_year_from_code(event_code: str) -> Optional[int]:
    """
    Returns the 4-digit year from the trailing 2 digits of an event code.

    'ACU25'         → 2025
    'ACU - RS26'    → 2026
    'MMU/GS - JS26' → 2026
    'DDU'           → None
    """
    if not event_code:
        return None
    m = re.search(r"(\d{2})\s*$", event_code.strip())
    return int("20" + m.group(1)) if m else None


class CurrentActiveEditionResolver:
    """
    Groups all Event records by master code and identifies the current active
    edition (the one with the latest event_date) for each master group.

    The latest edition always becomes the active event shown in the main table.
    All other editions in the group are historical and shown only in the drawer.
    """

    def resolve(self, filters: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """
        Returns a list of master event dicts, sorted by current edition date desc.

        Each dict:
            master_code:   str
            current:       Event  — latest event_date in the group
            all_events:    [Event, ...]  — sorted newest first
            edition_count: int
        """
        from django.db.models import Q

        qs = Event.objects.select_related("sales_executive").order_by("-event_date")

        if filters:
            if filters.get("status"):
                qs = qs.filter(status=filters["status"])
            search = (filters.get("search") or "").strip()
            if search:
                qs = qs.filter(
                    Q(name__icontains=search) | Q(event_code__icontains=search)
                )

        groups: Dict[str, Dict] = {}
        for event in qs:
            mc = normalize_master_code(event.event_code)
            if not mc:
                continue
            if mc not in groups:
                groups[mc] = {
                    "master_code":   mc,
                    "current":       event,  # first = latest (sorted desc)
                    "all_events":    [],
                    "edition_count": 0,
                }
            groups[mc]["all_events"].append(event)
            groups[mc]["edition_count"] += 1

        return sorted(
            groups.values(),
            key=lambda g: (g["current"].event_date or date.min),
            reverse=True,
        )

    def events_for_master(self, master_code: str) -> List[Event]:
        """Returns all Events belonging to master_code, sorted newest first."""
        mc = master_code.strip().upper()
        return [
            e for e in
            Event.objects.select_related("sales_executive").order_by("-event_date")
            if normalize_master_code(e.event_code) == mc
        ]


class EventPerformanceInsightEngine:
    """
    Generates textual performance insights from per-edition metrics data.
    Compares consecutive editions to surface growth / decline signals.
    """

    def generate(self, events: List[Event], metrics: Dict[str, Dict]) -> List[str]:
        """
        events:  Event records sorted newest first
        metrics: {event_code: metrics_dict from bulk_event_metrics}
        Returns up to 5 insight strings.
        """
        if not events:
            return []
        if len(events) == 1:
            return ["Only one edition tracked — no historical comparison available yet."]

        insights: List[str] = []
        editions_asc = sorted(events, key=lambda e: e.event_date or date.min)

        prev: Optional[Event] = None
        for event in editions_asc:
            m = metrics.get(event.event_code, {})
            if prev:
                prev_m    = metrics.get(prev.event_code, {})
                prev_paid = prev_m.get("paid_count", 0) or 0
                curr_paid = m.get("paid_count", 0) or 0
                prev_rev  = prev_m.get("total_revenue", 0) or 0
                curr_rev  = m.get("total_revenue", 0) or 0
                prev_yr   = prev.event_date.year if prev.event_date else "?"

                if prev_paid > 0:
                    pg = round((curr_paid - prev_paid) / prev_paid * 100, 1)
                    if pg >= 15:
                        insights.append(f"Bookings growing: +{pg}% vs {prev_yr} edition")
                    elif pg <= -15:
                        insights.append(f"Bookings declining: {pg}% vs {prev_yr} edition")

                if prev_rev > 0:
                    rg = round((curr_rev - prev_rev) / prev_rev * 100, 1)
                    if abs(rg) >= 10:
                        direction = "upward" if rg > 0 else "downward"
                        insights.append(f"Revenue trending {direction}: {rg:+.1f}% vs previous edition")

            prev = event

        current = editions_asc[-1]
        curr_m  = metrics.get(current.event_code, {})
        bm      = curr_m.get("benchmark", 0) or 0
        if bm >= 75:
            insights.append(f"Current edition exceeding capacity benchmark ({bm}%)")
        elif 0 < bm < 25:
            insights.append(f"Current edition below benchmark at {bm}%")

        total_paid_all = sum(
            (metrics.get(e.event_code, {}).get("paid_count", 0) or 0)
            for e in events
        )
        if total_paid_all > 0:
            insights.append(
                f"{len(events)} editions tracked · {total_paid_all:,} total paid delegates across all years"
            )

        return (insights or ["No significant trends detected."])[:5]
