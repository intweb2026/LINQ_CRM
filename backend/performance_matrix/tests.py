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
from .services import (
    ATT_BENCHMARK, ATT_CURVE, BENCHMARK, PAY_CURVE, build_payload, countdown,
    curve_due, curve_projection, previous_edition_label, projection,
)

TODAY = date(2026, 1, 12)


def book(n, code, request_date, status="Paid", pof="Paid", paid=None, invoiced=None, bc="", company=""):
    inv = BookEvent.objects.create(
        invoice_number=f"INV-{n}", event_code=code, request_date=request_date,
        invoice_date=invoiced, payment_date=paid, payment_status=status, paid_or_free=pof,
        booking_code=bc, company_name=company,
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
        book("C8", "AFS", TODAY - timedelta(days=1), status="Pending")                # pending, never invoiced
        # A third family, SPK 2026, carries the booking-code driven cases, speakers,
        # sponsors, group passes, a free seat and a paid-then-cancelled seat, so the
        # AFS arithmetic above stays exactly as it is.
        Event.objects.create(event_code="SPK", event_date=date(2026, 3, 15), website="spk.example.com")
        book("S1", "SPK", TODAY - timedelta(days=40), bc="Speaker", paid=TODAY - timedelta(days=30))
        book("S2", "SPK", TODAY - timedelta(days=9), bc="SPP", status="Pending")
        book("S3", "SPK", TODAY - timedelta(days=8), bc="Speaker / Group Pass", pof="Free", company="Globex")
        book("X1", "SPK", TODAY - timedelta(days=12), bc="SLV SpEx", company="Acme", paid=TODAY - timedelta(days=12))
        book("X2", "SPK", TODAY - timedelta(days=6), bc="Upgraded to GLD SpEx", company="Acme", status="Pending",
             invoiced=TODAY - timedelta(days=6))
        book("X3", "SPK", TODAY - timedelta(days=5), bc="Speaker Table", company="Initech", paid=TODAY - timedelta(days=5))
        book("G1", "SPK", TODAY - timedelta(days=3), bc="Group Pass", company="Umbrella", status="Pending")
        book("K1", "SPK", TODAY - timedelta(days=2), status="Cancelled", paid=TODAY - timedelta(days=2))
        # Research pipeline: two unmined tickets and one mined, one paper this week.
        Ticket.objects.create(purpose="AFS", type_of_ticket="Blue - BX", estimate=120)
        Ticket.objects.create(purpose="AFS", type_of_ticket="Blue - BX", estimate=80)
        Ticket.objects.create(purpose="AFS", type_of_ticket="White - WH", estimate=30, actual_number=5)
        PaperReview.objects.create(event_code="AFS - JS", speaker_name="S", email="s@x.com",
                                   paper_submission_date=TODAY - timedelta(days=3))

    def test_matrix(self):
        p = build_payload("all", today=TODAY, user=self.admin)
        rows = {r["event_code"]: r for r in p["rows"]}
        self.assertEqual(set(rows), {"AFS", "AFS - JS", "SPK"})
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

        # C1, C2 paid + C3, C4, C8 pending + C6 paid-free = 6 live of its own. C5
        # cancelled is out. The 2025 edition was Postponed, so its three paid heads
        # are carried onto this one and the row is the total, 9, with the old
        # share stated beside it.
        self.assertEqual(cur["live_count"], 9)
        # Payment date AND payable AND not refunded: C1, C2, C5 even though it was
        # cancelled after paying, plus the three carried.
        self.assertEqual(cur["paid_heads"], 6)
        self.assertEqual(cur["carried"], {"live": 3, "paid": 3, "pending": 0, "expected": 0,
                                          "not_invoiced": 0, "free": 0, "cancelled": 0, "group_pass": 0})
        self.assertIsNone(prev["carried"])
        # Pending payable, split on the invoice date: C3 invoiced 20 days ago, C4
        # three days ago, C8 never.
        self.assertEqual(cur["pending"], 1)
        self.assertEqual(cur["expected"], 1)
        self.assertEqual(cur["not_invoiced"], 1)
        self.assertEqual(cur["shortfall"], BENCHMARK - 3)   # off the payments projection, 3 / 0.9 rounds to 3
        self.assertEqual((cur["bk_last"], cur["pay_last"]), (TODAY.isoformat(), TODAY.isoformat()))
        self.assertEqual((cur["free"], cur["cancelled"], cur["group_pass"], cur["website"]), (1, 1, 0, ""))
        self.assertEqual(cur["bk_today"], 1)
        self.assertEqual(cur["bk_d7"], 4)     # C1, C2, C6, C8
        self.assertEqual(cur["bk_d14"], 1)    # C3
        self.assertEqual(cur["bk_d21"], 1)    # C4
        self.assertEqual(cur["bk_d30"], 6)
        self.assertEqual(cur["pay_today"], 1)
        self.assertEqual(cur["pay_d7"], 2)
        # 30 days before the 2025 edition, two of its three heads were booked.
        self.assertEqual(cur["live_prev_year"], 2)
        self.assertEqual(cur["live_delta"], 4)
        self.assertEqual(prev["live_count"], 3)
        self.assertIsNone(prev["live_prev_year"])

        # 30 days out is 4 whole weeks: six live heads against the 85.5% the
        # attendance curve expects by then project to 7, three paid against 90% to
        # 3. The 33% curve opened sales on 11 Aug 2025 and is one day into its
        # last month: 6 / (0.66 + 0.34 / 31) rounds to 9. The three carried heads
        # are NOT in any of these; projections read this edition's own pace.
        self.assertEqual(cur["proj"], 9)
        self.assertEqual(cur["att_proj"], 7)
        self.assertEqual(cur["paid_proj"], 3)
        # 30 days out is 4 whole weeks, where 85.5% of the 65 attendees and 90%
        # of the 40 paid heads should already be in, against this edition's own
        # 6 live and 3 paid.
        self.assertEqual((cur["due_att"], cur["gap_att"]), (56, -50))
        self.assertEqual((cur["due_pay"], cur["gap_pay"]), (36, -33))
        self.assertEqual(p["att_benchmark"], ATT_BENCHMARK)
        # A finished edition's projection is its count.
        self.assertEqual((prev["proj"], prev["att_proj"], prev["paid_proj"]), (3, 3, 3))

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

        # SPK: codes are read as substrings, so "Speaker / Group Pass" is a speaker
        # AND a group pass, "Speaker Table" a sponsor and not a speaker, and a
        # company is counted once per SpEx column however many seats it holds. A
        # group pass counts only once paid, so Umbrella's pending G1 is out.
        spk = rows["SPK"]
        self.assertEqual(spk["website"], "spk.example.com")
        self.assertEqual((spk["live_count"], spk["paid_heads"]), (7, 4))    # K1 paid, then cancelled: still a paid head
        self.assertEqual((spk["pending"], spk["expected"], spk["not_invoiced"]), (0, 1, 2))
        self.assertEqual((spk["free"], spk["cancelled"], spk["group_pass"]), (1, 1, 1))
        self.assertEqual(spk["bk_last"], (TODAY - timedelta(days=2)).isoformat())    # K1: cancelled, still a booking
        self.assertEqual(spk["pay_last"], (TODAY - timedelta(days=5)).isoformat())   # X3; K1 is Cancelled, its date is out
        self.assertEqual(spk["sp_first"], (TODAY - timedelta(days=40)).isoformat())
        self.assertEqual((spk["sp_total"], spk["sp_booked"], spk["sp_paid"], spk["sp_free"]), (3, 1, 1, 1))
        self.assertEqual((spk["spex_all_total"], spk["spex_all_slv"], spk["spex_all_gld"], spk["spex_all_table"],
                          spk["spex_all_upgraded"], spk["spex_all_ptn"]), (2, 1, 1, 1, 1, 0))
        self.assertEqual((spk["spex_paid_total"], spk["spex_paid_slv"], spk["spex_paid_table"], spk["spex_paid_gld"]),
                         (2, 1, 1, 0))
        self.assertEqual((spk["spex_pending_total"], spk["spex_pending_gld"], spk["spex_pending_upgraded"],
                          spk["spex_pending_slv"]), (1, 1, 1, 0))
        # 62 days out is 8 weeks: four paid over the 76.9% expected project to 5, so 35 short of 40.
        self.assertEqual((spk["paid_proj"], spk["shortfall"]), (5, BENCHMARK - 5))

    def test_upcoming_hides_past_and_counts_unlinked(self):
        p = build_payload("upcoming", today=TODAY, user=self.admin)
        self.assertEqual([r["event_code"] for r in p["rows"]], ["AFS - JS", "SPK"])
        self.assertEqual(p["totals"]["below_benchmark"], 2)
        self.assertEqual(p["totals"]["tk_unmined"], 2)

    def test_twice_postponed_edition_carries_the_whole_chain(self):
        # Q 2024 postponed with one head, Q 25 postponed with one head, Q - X 2026
        # is the date that finally runs; it carries both, and the group pass
        # company booked at both postponed dates is counted once.
        Event.objects.create(event_code="Q 24", base_code="Q", year=2024, event_date=date(2024, 5, 1), verdict="Postponed")
        Event.objects.create(event_code="Q 25", base_code="Q", year=2025, event_date=date(2025, 5, 1), verdict="Postponed")
        # A bare twin of Q 25 on the same day, as the 8 Sep 2026 loads left behind;
        # it is folded into Q 25 and gets no row, and its bookings read off Q 25.
        Event.objects.create(event_code="Q", base_code="Q", year=2025, event_date=date(2025, 5, 1))
        Event.objects.create(event_code="Q - X", base_code="Q", year=2026, event_date=date(2026, 5, 1))
        book("Q1", "Q", date(2024, 3, 1), paid=date(2024, 3, 1), bc="Group Pass", company="Wayne")
        book("Q2", "Q", date(2025, 3, 1), paid=date(2025, 3, 1), bc="Group Pass", company="Wayne")
        book("Q3", "Q", date(2026, 1, 5), status="Pending")
        rows = {r["event_code"]: r for r in build_payload("all", today=TODAY, user=self.admin)["rows"]}
        self.assertNotIn("Q", rows)
        cur = rows["Q - X"]
        self.assertEqual((cur["live_count"], cur["paid_heads"], cur["group_pass"]), (3, 2, 1))
        self.assertEqual((cur["carried"]["live"], cur["carried"]["paid"], cur["carried"]["group_pass"]), (2, 2, 1))
        self.assertEqual(rows["Q 25"]["carried"]["live"], 1)     # itself rescheduled from Q 24
        self.assertEqual(rows["Q 25"]["live_count"], 2)
        # Projections stay on its own single head: 15 weeks out the attendance
        # curve expects 41.3%, and 1 / 0.413 rounds to 2, not the 7 the total would give.
        self.assertEqual(cur["att_proj"], 2)

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

    def test_projections(self):
        ev = date(2026, 8, 11)                                          # sales open 11 Feb 2026
        self.assertIsNone(projection(date(2026, 2, 11), ev, 4))         # nothing elapsed: Pending
        self.assertEqual(projection(date(2026, 5, 11), ev, 33), 100)    # a third of the curve, a third of the heads
        self.assertEqual(projection(date(2026, 7, 11), ev, 33), 50)
        self.assertEqual(projection(date(2026, 9, 1), ev, 70), 70)      # over: the count is the finish

        # More than 21 weeks out the weekly curves expect nothing yet.
        self.assertIsNone(curve_projection(date(2026, 1, 12), ev, 5, ATT_CURVE))
        # 20 weeks out attendance expects 22.5% and payments 20%: 15 live project
        # to 67, 14 to 62; 14 paid to 70.
        d = ev - timedelta(weeks=20)
        self.assertEqual(curve_projection(d, ev, 15, ATT_CURVE), 67)
        self.assertEqual(curve_projection(d, ev, 14, ATT_CURVE), 62)
        self.assertEqual(curve_projection(d, ev, 14, PAY_CURVE), 70)
        # From the event week the curve is complete and the count is the finish.
        self.assertEqual(curve_projection(ev, ev, 40, PAY_CURVE), 40)
        self.assertEqual(curve_projection(ev + timedelta(days=9), ev, 64, ATT_CURVE), 64)

    def test_pace(self):
        ev = date(2026, 8, 11)
        # Before the curve starts nothing is due yet and the window is open ended.
        far = ev - timedelta(weeks=22)
        self.assertIsNone(curve_due(far, ev, ATT_BENCHMARK, ATT_CURVE))
        # 20 weeks out the curves expect 22.5% of the 65 attendees and 20% of the
        # 40 paid heads banked.
        d = ev - timedelta(weeks=20)
        self.assertEqual(curve_due(d, ev, ATT_BENCHMARK, ATT_CURVE), 15)
        self.assertEqual(curve_due(d, ev, BENCHMARK, PAY_CURVE), 8)
        # From the event week the curve is complete, so the whole target is due.
        self.assertEqual(curve_due(ev, ev, BENCHMARK, PAY_CURVE), BENCHMARK)

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
