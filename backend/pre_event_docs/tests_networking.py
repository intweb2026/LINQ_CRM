"""
pre_event_docs/tests_networking.py
───────────────────────────────────
The seating optimiser. SimpleTestCase, because networking.py touches no
database and asking for one would only slow the suite down.

WHAT THESE ASSERT, AND WHAT THEY DELIBERATELY DO NOT
Not an exact repeat count, which is random. They assert the two things that
matter, that every draw is a VALID seating, and that it reaches the arithmetic
floor for its table plan, which is the strongest claim available about a
heuristic on an NP hard problem.
"""
from itertools import combinations

from django.test import SimpleTestCase

from .networking import (
    build_plan, floor_repeats, suggested_table_count, table_sizes,
)


def roster(n, firms=None):
    firms = firms or n
    return [
        {"id": i, "name": f"Person {i:03d}", "company": f"Firm {i % firms}"}
        for i in range(1, n + 1)
    ]


def audit(plan):
    """Recount the draw from scratch, independently of the optimiser's own score."""
    seen, repeats, clashes = set(), 0, 0
    per_round = []
    for r in range(plan["rounds"]):
        tables = {}
        for entry in plan["assignment"]:
            tables.setdefault(entry["tables"][r], []).append(entry)
        per_round.append(tables)
        for people in tables.values():
            ids = sorted(p["delegate_id"] for p in people)
            for pair in combinations(ids, 2):
                if pair in seen:
                    repeats += 1
                else:
                    seen.add(pair)
            firms = [p["company"] for p in people]
            clashes += len(firms) - len(set(firms))
    return per_round, repeats, clashes


class TableMaths(SimpleTestCase):
    def test_the_suggestion_matches_the_workbook_formula(self):
        """
        Report_SpeedNetworking!I2 is MAX(2, ROUND(n/6, 0)), so an untouched draw
        lands where the sheet would have put it. This is only ever a starting
        value for the field; see test_the_count_given_is_the_count_used.
        """
        self.assertEqual(suggested_table_count(0), 0)
        self.assertEqual(suggested_table_count(1), 2)
        self.assertEqual(suggested_table_count(55), 9)   # the sheet's own example
        self.assertEqual(suggested_table_count(40), 7)

    def test_sizes_are_balanced_and_sum_exactly(self):
        self.assertEqual(table_sizes(40, 7), [6, 6, 6, 6, 6, 5, 5])
        for n in range(2, 200):
            sizes = table_sizes(n, suggested_table_count(n))
            self.assertEqual(sum(sizes), n)
            self.assertLessEqual(max(sizes) - min(sizes), 1)

    def test_the_pigeonhole_bound_is_what_counting_alone_misses(self):
        """
        Three tables of six cannot be repeat free, because six people drawn from
        three earlier tables must include two who already met. The counting
        bound says zero here, which is why both bounds exist.
        """
        sizes = table_sizes(18, 3)
        slots = 3 * sum(s * (s - 1) // 2 for s in sizes)
        self.assertLess(slots, 18 * 17 // 2, "counting alone sees no problem")
        self.assertEqual(floor_repeats(sizes, 3, 18), 18)

    def test_thirteen_people_over_two_tables_forces_thirty_repeats(self):
        self.assertEqual(floor_repeats(table_sizes(13, 2), 3, 13), 30)


class Draws(SimpleTestCase):
    # (attendees, companies, tables). The table count is always given, because
    # that is what a room has.
    SIZES = [(40, 40, 7), (40, 8, 7), (42, 14, 7), (60, 6, 10), (120, 30, 20),
             (250, 60, 42), (13, 4, 2), (9, 3, 2), (7, 7, 2)]

    def test_the_count_given_is_the_count_used(self):
        """
        THE BUG THIS PINS, in the place it was introduced. Asking for six used
        to mean six PEOPLE PER TABLE, which on a 170 person list produced 28
        tables. A room has the tables it has.
        """
        for tables in (2, 3, 6, 9, 28):
            with self.subTest(tables=tables):
                plan = build_plan(roster(170, 40), tables=tables)
                self.assertEqual(plan["tables"], tables)
                for r in range(plan["rounds"]):
                    used = {e["tables"][r] for e in plan["assignment"]}
                    self.assertEqual(used, set(range(1, tables + 1)))

    def test_people_per_table_drives_the_count_when_no_count_is_given(self):
        """
        The field the user asked for. A venue quotes chairs per table more
        readily than it quotes tables, so the count follows from it.
        """
        # Rounded UP, not to nearest. The workbook's suggestion rounds, and on
        # this list that gives 28 tables whose largest holds seven, which is one
        # more than the six chairs asked for. A ceiling has to be a ceiling.
        for seats, expected in ((10, 17), (6, 29), (20, 9), (170, 2)):
            with self.subTest(seats=seats):
                plan = build_plan(roster(170, 40), per_table=seats)
                self.assertEqual(plan["tables"], expected)
                # A ceiling, so no table may exceed it. It is not a target
                # either: 170 over 17 tables is 10 exactly, but 170 over 9 is
                # 19 and 18, not eight tables of 20 and one of 10.
                self.assertLessEqual(plan["largest_table"], seats)

    def test_a_count_given_alongside_the_seats_still_wins(self):
        """
        Both numbers, and they do not conflict. Forty-five people over six
        tables sit 8 and 7 whatever ten chairs would allow, because spreading a
        room across the tables it HAS is what makes people meet.
        """
        plan = build_plan(roster(45, 12), tables=6, per_table=10)
        self.assertEqual(plan["tables"], 6)
        self.assertEqual(plan["largest_table"], 8)

    def test_a_room_that_cannot_hold_the_list_is_refused_with_both_ways_out(self):
        """
        Six tables of ten seat sixty, and seventy people do not fit. Raised
        rather than resolved, because adding the seventh table and squeezing
        twelve onto a table of ten are both somebody else's decision.
        """
        with self.assertRaises(ValueError) as caught:
            build_plan(roster(70, 20), tables=6, per_table=10)
        message = str(caught.exception)
        self.assertIn("7 tables", message, message)
        self.assertIn("12 per table", message, message)

    def test_the_two_fields_agree_wherever_the_page_puts_them(self):
        """
        The page keeps the pair in step by recomputing one from the other off
        the head count, so every pair it can send is a pair that draws. This is
        that arithmetic, checked against the optimiser rather than restated.
        """
        for people in (7, 13, 45, 170, 250):
            for tables in (2, 6, 9):
                seats = -(-people // tables)          # what the page would show
                with self.subTest(people=people, tables=tables):
                    plan = build_plan(roster(people, 8), tables=tables,
                                      per_table=seats)
                    self.assertLessEqual(plan["largest_table"], seats)

    def test_a_big_room_of_few_tables_still_answers_quickly(self):
        """
        The work budgets. Six tables of sixty-seven offers millions of candidate
        swaps for repeats the room forces anyway, and unbudgeted this ran for
        nine seconds; see SEAT_BUDGET and REPAIR_BUDGET.
        """
        import time
        start = time.time()
        plan = build_plan(roster(400, 100), tables=6)
        self.assertLess(time.time() - start, 6.0)
        self.assertEqual(plan["tables"], 6)
        self.assertEqual(plan["largest_table"], 67)

    def test_every_draw_seats_everybody_exactly_once_per_round(self):
        for n, firms, tables in self.SIZES:
            with self.subTest(n=n, firms=firms, tables=tables):
                plan = build_plan(roster(n, firms), tables=tables)
                self.assertEqual(len(plan["assignment"]), n)
                per_round, _, _ = audit(plan)
                for tables in per_round:
                    self.assertEqual(sum(len(p) for p in tables.values()), n)
                    self.assertEqual(set(tables), set(range(1, plan["tables"] + 1)))
                    counts = [len(p) for p in tables.values()]
                    self.assertLessEqual(max(counts) - min(counts), 1)

    def test_the_reported_score_matches_an_independent_recount(self):
        for n, firms, tables in self.SIZES:
            with self.subTest(n=n, firms=firms, tables=tables):
                plan = build_plan(roster(n, firms), tables=tables)
                _, repeats, _ = audit(plan)
                self.assertEqual(repeats, plan["repeat_pairs"])
                self.assertEqual(plan["optimal"],
                                 repeats <= plan["floor_repeats"])

    def test_every_realistic_room_reaches_its_arithmetic_floor(self):
        """
        The requirement, as a number. Where the floor is zero this means nobody
        meets the same person twice across all three rounds.
        """
        for n, firms, tables in self.SIZES:
            with self.subTest(n=n, firms=firms, tables=tables):
                plan = build_plan(roster(n, firms), tables=tables)
                self.assertTrue(
                    plan["optimal"],
                    f"{n} people left {plan['repeat_pairs']} repeats "
                    f"where {plan['floor_repeats']} is achievable",
                )

    def test_colleagues_are_spread_apart_when_there_is_room_to_do_it(self):
        # 60 people over 6 firms, 10 tables of 6. Ten per firm and ten tables,
        # so a perfect draw seats one colleague per table.
        _, _, clashes = audit(build_plan(roster(60, 6), tables=10))
        self.assertLess(clashes, 60, "colleagues were left clustered")

    def test_the_same_seed_reproduces_the_same_draw(self):
        """
        Random, and still reproducible, which is what lets a draw be stored and
        printed. A previous version returned the winning attempt's seed instead
        of the search's, and feeding it back produced a different draw.
        """
        people = roster(40, 10)
        first = build_plan(people, tables=7)
        again = build_plan(people, tables=7, seed=first["seed"])
        self.assertEqual(first["assignment"], again["assignment"])
        self.assertEqual(first["repeat_pairs"], again["repeat_pairs"])

    def test_two_unseeded_draws_of_the_same_room_differ(self):
        people = roster(40, 10)
        draws = {tuple(tuple(a["tables"]) for a in build_plan(people, tables=7)["assignment"])
                 for _ in range(5)}
        self.assertGreater(len(draws), 1, "the draw is supposed to be random")

    def test_a_forced_floor_is_reported_rather_than_argued_with(self):
        """
        Six tables of twenty-eight force hundreds of repeats. There is nothing to
        be done about that except run fewer rounds or find more tables, so the
        floor is reported and the room is left alone. An earlier version offered
        a smaller table size, which is advice nobody in a room can take.
        """
        plan = build_plan(roster(170, 40), tables=6)
        self.assertEqual(plan["tables"], 6)
        self.assertGreater(plan["floor_repeats"], 0)
        self.assertNotIn("suggest_per_table", plan)

    def test_fewer_rounds_is_the_lever_when_tables_are_fixed(self):
        forced = build_plan(roster(60, 12), tables=3, rounds=3)
        easier = build_plan(roster(60, 12), tables=3, rounds=1)
        self.assertGreater(forced["floor_repeats"], easier["floor_repeats"])
        self.assertEqual(easier["floor_repeats"], 0)

    def test_empty_and_single_person_rooms_do_not_raise(self):
        empty = build_plan([])
        self.assertEqual(empty["assignment"], [])
        self.assertTrue(empty["optimal"])

        solo = build_plan(roster(1), tables=6)
        self.assertEqual(len(solo["assignment"]), 1)
        self.assertEqual(solo["tables"], 1, "one person cannot fill six tables")

    def test_rounds_other_than_three(self):
        for rounds in (1, 2, 4):
            with self.subTest(rounds=rounds):
                plan = build_plan(roster(120, 30), tables=20, rounds=rounds)
                self.assertEqual(plan["rounds"], rounds)
                self.assertTrue(all(len(a["tables"]) == rounds
                                    for a in plan["assignment"]))
