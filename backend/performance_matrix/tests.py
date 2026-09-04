"""
The one check that fails if the matrix arithmetic breaks.

Two editions of one family, AFS 2025 (postponed) and AFS - JS 2026, with
bookings that carry NO edition year, as the live data does, so the sales-window
placement is what puts each booking on its edition.
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase

from book_delegate.models import BookDelegate
from book_event.models import BookEvent
from events.models import Event
from paper_review.models import PaperReview
from ticket_central.models import Ticket

from .management.commands.sync_verdicts_from_sheet import (
    apply_changes, column_index, normalise_status, plan_changes,
)
from .services import BENCHMARK, build_payload, countdown, previous_edition_label

TODAY = date(2026, 1, 12)


def book(n, code, request_date, status="Paid", pof="Paid", paid=None, invoiced=None):
    inv = BookEvent.objects.create(
        invoice_number=f"INV-{n}", event_code=code, request_date=request_date,
        invoice_date=invoiced, payment_date=paid, payment_status=status, paid_or_free=pof,
    )
    BookDelegate.objects.create(invoice=inv, first_name=f"D{n}", email=f"d{n}@x.com")
    return inv


class MatrixTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_user(username="adm", password="x", role="admin")
        cls.prev = Event.objects.create(event_code="AFS", base_code="AFS", year=2025,
                                        event_date=date(2025, 2, 10), end_date=date(2025, 2, 11),
                                        verdict="Postponed", sales_team="Ana Sales")
        cls.cur = Event.objects.create(event_code="AFS - JS", event_date=date(2026, 2, 11),
                                       end_date=date(2026, 2, 12), verdict="Needs a push",
                                       location="Berlin")
        # Previous edition: three live heads, booked 60, 40 and 20 days before it ran.
        for i, back in enumerate((60, 40, 20)):
            book(f"P{i}", "AFS", date(2025, 2, 10) - timedelta(days=back), paid=date(2025, 1, 1))
        # Current edition, all coded with the BASE code and no edition year:
        book("C1", "AFS", TODAY, paid=TODAY)                                         # paid today
        book("C2", "AFS", TODAY - timedelta(days=5), paid=TODAY - timedelta(days=3))  # paid this week
        book("C3", "AFS", TODAY - timedelta(days=10), status="Pending",
             invoiced=TODAY - timedelta(days=20))                                    # pending, overdue
        book("C4", "AFS", TODAY - timedelta(days=16), status="Pending",
             invoiced=TODAY - timedelta(days=3))                                     # pending, expected
        book("C5", "AFS", TODAY - timedelta(days=2), status="Cancelled", paid=TODAY)  # dead
        book("C6", "AFS", TODAY - timedelta(days=2), status="Paid", pof="Free")       # free seat
        book("C7", "ZZZ", TODAY)                                                      # no such family
        # Research pipeline: two unmined tickets and one mined, one paper this week.
        Ticket.objects.create(purpose="AFS", type_of_ticket="Blue - BX", estimate=120)
        Ticket.objects.create(purpose="AFS", type_of_ticket="Blue - BX", estimate=80)
        Ticket.objects.create(purpose="AFS", type_of_ticket="White - WH", estimate=30, actual_number=5)
        PaperReview.objects.create(event_code="AFS - JS", speaker_name="S", email="s@x.com",
                                   paper_submission_date=TODAY - timedelta(days=3))

    def test_matrix(self):
        p = build_payload("all", today=TODAY, user=self.admin)
        rows = {r["event_code"]: r for r in p["rows"]}
        self.assertEqual(set(rows), {"AFS", "AFS - JS"})
        cur, prev = rows["AFS - JS"], rows["AFS"]

        self.assertEqual(cur["base_code"], "AFS")          # derived by save()
        self.assertEqual(cur["year"], 2026)
        self.assertEqual(cur["days_left"], 30)
        self.assertEqual(cur["countdown"], "30d")
        self.assertEqual(cur["location"], "Berlin")
        self.assertEqual(prev["owners"], {"SCA": "Ana Sales"})
        self.assertEqual(cur["prev_status"], "Rescheduled")
        self.assertEqual(prev["prev_status"], "Fresh")
        self.assertTrue(prev["done"])
        self.assertFalse(cur["done"])
        self.assertEqual(cur["verdict"], "Needs a push")

        # C1, C2 paid + C3, C4 pending + C6 paid-free = live. C5 cancelled is out.
        self.assertEqual(cur["live_count"], 5)
        # Payment date AND payable AND not dead: C1, C2. C5 is cancelled, C6 is free.
        self.assertEqual(cur["paid_heads"], 2)
        self.assertEqual(cur["pending"], 1)
        self.assertEqual(cur["expected"], 1)
        self.assertEqual(cur["shortfall"], BENCHMARK - 2)
        self.assertEqual(cur["bk_today"], 1)
        self.assertEqual(cur["bk_d7"], 3)     # C1, C2, C6
        self.assertEqual(cur["bk_d14"], 1)    # C3
        self.assertEqual(cur["bk_d21"], 1)    # C4
        self.assertEqual(cur["bk_d30"], 5)
        self.assertEqual(cur["pay_today"], 1)
        self.assertEqual(cur["pay_d7"], 2)
        # 30 days before the 2025 edition, two of its three heads were booked.
        self.assertEqual(cur["live_prev_year"], 2)
        self.assertEqual(cur["live_delta"], 3)
        self.assertEqual(prev["live_count"], 3)
        self.assertIsNone(prev["live_prev_year"])

        # Tickets sit on the nearest upcoming edition only; the mined one is out.
        self.assertTrue(cur["tk_here"])
        self.assertFalse(prev["tk_here"])
        self.assertEqual(cur["tk_unmined"], 2)
        self.assertEqual(cur["tk_data"], 200)
        self.assertEqual(cur["tk_types"], {"BX": 2})
        self.assertEqual(p["ticket_types"], [{"key": "BX", "label": "BX"}])
        # The paper landed on the current edition by submission date.
        self.assertEqual(cur["pr_total"], 1)
        self.assertEqual(cur["pr_d7"], 1)
        self.assertEqual(cur["pr_today"], 0)
        self.assertEqual(p["years"], [2025, 2026])

    def test_upcoming_hides_past_and_counts_unlinked(self):
        p = build_payload("upcoming", today=TODAY, user=self.admin)
        self.assertEqual([r["event_code"] for r in p["rows"]], ["AFS - JS"])
        self.assertEqual(p["totals"]["below_benchmark"], 1)
        self.assertEqual(p["totals"]["tk_unmined"], 2)

    def test_previous_edition_label_reads_the_verdict(self):
        prior = Event(event_code="X", verdict="Postponed")
        self.assertEqual(previous_edition_label(prior), "Rescheduled")
        prior.verdict = "Cancelled"
        self.assertEqual(previous_edition_label(prior), "Relaunch")
        prior.verdict = "Going Ahead"
        self.assertEqual(previous_edition_label(prior), "Repeat")
        prior.verdict = ""
        self.assertEqual(previous_edition_label(prior), "Repeat")   # no verdict recorded: it ran
        self.assertEqual(previous_edition_label(None), "Fresh")

    def test_countdown(self):
        self.assertEqual(countdown(TODAY, TODAY), "Today")
        self.assertEqual(countdown(TODAY, date(2027, 9, 13)), "1y 8mo 1d")
        self.assertEqual(countdown(TODAY, date(2026, 1, 9)), "3d ago")


class VerdictSheetSyncTests(TestCase):
    """The sheet to verdict copy, without Google: rows in, plan out, then written."""

    @classmethod
    def setUpTestData(cls):
        cls.a = Event.objects.create(event_code="HFE - RS", event_date=date(2026, 2, 2))
        cls.b = Event.objects.create(event_code="FCM - JS", event_date=date(2026, 2, 11), verdict="Postponed")
        cls.c = Event.objects.create(event_code="BIU/GS - PM", event_date=date(2026, 2, 9))

    def test_columns_and_aliases(self):
        self.assertEqual(column_index("B"), 1)
        self.assertEqual(column_index("BJ"), 61)
        self.assertEqual(normalise_status("  going   ahead "), "Going Ahead")
        self.assertEqual(normalise_status("Full efforts required"), "Full Efforts Req.")
        self.assertIsNone(normalise_status(""))
        self.assertIsNone(normalise_status("Maybe"))

    def test_plan_then_apply(self):
        pad = [""] * 58   # columns D to BI, so the status lands in BJ
        rows = [
            ["Events 2026-27", "Events 2026-27", "SE", *pad, "Event Status"],   # header
            ["Total", "", "", *pad, ""],                                           # totals
            ["Feb", "hfe - rs", "Terry", *pad, "Going Ahead"],                    # case differs, changes
            ["Feb", "FCM - JS", "Terry", *pad, "postponed"],                      # already correct
            ["Feb", "BIU/GS - PM", "Terry", *pad, "Maybe"],                       # unknown status
            ["Feb", "ZZZ - QQ", "Terry", *pad, "Going Ahead"],                    # no such event
            ["Feb", "HFE - RS", "Terry", *pad, "Cancelled"],                      # duplicate code, first wins
        ]
        plan = plan_changes(rows, 1, 61)
        self.assertEqual([(e.event_code, v) for e, v in plan["changes"]], [("HFE - RS", "Going Ahead")])
        self.assertEqual([e.event_code for e in plan["unchanged"]], ["FCM - JS"])
        self.assertEqual(plan["unknown"], [("BIU/GS - PM", "Maybe")])
        self.assertEqual(plan["unmatched"], ["Events 2026-27", "ZZZ - QQ"])
        self.assertEqual(plan["blank"], 0)
        self.assertEqual(apply_changes(plan["changes"]), 1)
        self.a.refresh_from_db()
        self.assertEqual(self.a.verdict, "Going Ahead")
        self.c.refresh_from_db()
        self.assertEqual(self.c.verdict, "")
