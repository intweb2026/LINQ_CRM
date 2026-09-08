"""
pre_event_docs/networking.py
─────────────────────────
Seat an attendee list across three rounds so that everybody meets as many NEW
people as possible.

WHAT THE WORKBOOK DID, AND WHY IT IS NOT ENOUGH
Report_SpeedNetworking assigned tables with a rotating offset per round, one
offset for the whole room. A single offset ROTATES THE ROOM, so every table
moves together and the same six people arrive at the next table as a block; the
attendee sees a different table number and the same faces. The Input tab existed
to soften that by spreading same company runs apart before the offset was
applied, which improves round one and does nothing for rounds two and three.

WHAT THIS DOES INSTEAD
A randomised best of N draw. Each attempt seats every round by walking the
attendees in a fresh random order and putting each person at whichever table
currently costs them least, where cost counts

    every person at that table they have ALREADY met, in an earlier round
    every person at that table from their OWN company

The met penalty dominates, so the optimiser will seat two colleagues together
before it will repeat a pairing; that is the right trade, because a repeat wastes
a whole round for two people while a colleague clash wastes one conversation.

The best of N attempts then goes through a swap repair pass, because the greedy
fill leaves a forced tail behind that no number of extra attempts removes; see
_repair for why. Measured on a 40 person list, the repair is the difference
between two repeat pairings and none.

HOW "BEST POSSIBLE" IS DECIDED, RATHER THAN CLAIMED
Three rounds of tables hand out a fixed number of pair slots, and a room only
contains so many distinct pairs, so below a certain size repeats are forced by
arithmetic. floor_repeats() computes that bound, the search stops the moment it
is reached, and the result carries an `optimal` flag saying whether the bound was
hit. Thirteen people over two tables forces thirty repeats, and a draw with
exactly thirty is the best matchup that room allows rather than a poor one. The
page reads `optimal`, never repeat_pairs being zero, because with a fixed table
count zero is frequently not available.

WHAT THIS DOES NOT DO IS ARGUE WITH THE ROOM
The table count is given and is honoured exactly. An earlier version derived it
from a target table size and offered to change it, which is advice nobody can
take: a room has the tables it has. Where the count forces repeats, the figure
is reported and left alone.

WHY RANDOM AT ALL, GIVEN A DETERMINISTIC RULE WOULD ALSO WORK
Because it was asked for, and because it is defensible. A fixed rule seats the
same people together at every event with a similar list; a fresh draw does not.
The seed is returned so a draw stays reproducible despite being random, which is
what lets it be stored, printed and audited.

ponytail: greedy fill, best of N, then a first improvement swap repair. No exact
solver, because this is the social golfer problem and it is NP hard. On every
size measured, up to 250 people, this reaches the arithmetic floor. If a real
event ever misses it, raise DEFAULT_ATTEMPTS before reaching for anything
cleverer, and only then consider simulated annealing over the swap pass.
"""
import random
from itertools import combinations

# Cost weights. The ratio is what matters, not the absolute values.
#
# A repeat costs more than three colleague clashes, so the optimiser never
# creates a repeat to avoid a clash. Both are integers so equal cost tables
# compare equal and the random walk order breaks the tie, which is what keeps
# successive attempts genuinely different from one another.
MET_PENALTY     = 10
COMPANY_PENALTY = 3

DEFAULT_PER_TABLE = 6
DEFAULT_ROUNDS    = 3
DEFAULT_ATTEMPTS  = 60

# THE TWO WORK BUDGETS, both charged in pair operations rather than in attempts
# or in swaps, because the cost of one of either scales with how big the tables
# are and the whole point is to bound the wall clock.
#
# Why they exist at all. The useful work is very unevenly distributed. A room of
# small tables reaches its floor in a few hundred operations. Six tables of
# twenty-eight can absorb millions and still sit above its floor, because there
# the repeats are forced by the room rather than by the seating, so no amount of
# swapping removes them. Without budgets a 400 person draw over 6 tables ran for
# nine seconds; with them it is under two, and the small rooms are unaffected
# because they finish long before the budget binds.
#
# A draw that spends its budget is still a valid, fully scored draw. It simply
# carries `optimal: false`, and the page states how far above the floor it is.
# MEASURED, not guessed. Repeat pairings above the floor, and wall clock, for a
# 170 person room over 6 tables and a 400 person room over 6 tables:
#
#   seat / repair        170 over 6 tables      400 over 6 tables
#   1.5M / 0.4M          +18.5%    0.2s         +18.6%    0.3s
#   4.0M / 2.0M           +6.6%    1.0s         +17.7%    1.1s   <- chosen
#   8.0M / 6.0M           +4.9%    2.9s         +17.0%    3.4s
#    20M /  20M           +1.4%    9.8s          +7.7%   11.2s
#
# The knee is at the second row. Tripling the budget past it buys about one
# percent, and a draw is a button somebody presses and waits on, so a second is
# the right price and ten is not. Rooms whose tables are small never reach these
# limits at all; they hit their floor in single-digit milliseconds and stop.
#
# ponytail: a big room of few tables settles about 7% above its floor rather
# than on it, because the budget stops the search. Raise both budgets, or move
# the draw to a background task and remove them, if a real event ever cares
# about those last few repeat pairings.
SEAT_BUDGET   = 4_000_000
REPAIR_BUDGET = 2_000_000

# Two tables minimum, carried forward from the workbook. One table is not a
# networking session, it is a meeting, and the round numbers would be identical.
MIN_TABLES = 2


def suggested_table_count(attendees, per_table=DEFAULT_PER_TABLE):
    """
    A STARTING SUGGESTION for how many tables, never the answer.

    THE MISTAKE THIS FUNCTION USED TO BE. It was called table_count and its
    result was used as the table count, on the theory that the caller wanted a
    target table SIZE. A room does not work that way. A room has the tables it
    has, and asking for six meant six people per table, which on a 170 person
    event produced 28 tables that nobody can put in a room. The count is now an
    argument to build_plan and this only seeds the field on screen.

    The formula is the workbook's own, MAX(2, ROUND(n/6, 0)), read out of
    Report_SpeedNetworking!I2, so an untouched draw matches what the sheet would
    have produced.
    """
    if attendees <= 0:
        return 0
    return max(MIN_TABLES, round(attendees / per_table))


def table_sizes(attendees, tables):
    """
    Balanced capacities, largest tables first, summing to exactly `attendees`.

    Capacity is fixed BEFORE seating rather than left to the fill, so no table
    ends up with two people while another has eleven. 40 people over 7 tables
    gives 6, 6, 6, 6, 6, 5, 5.
    """
    if tables <= 0:
        return []
    base, extra = divmod(attendees, tables)
    return [base + (1 if i < extra else 0) for i in range(tables)]


def _seat_one_round(people, sizes, counts, rng):
    """
    One round. Returns a list of tables, each a list of people.

    TWO THINGS ARE RANDOMISED, AND BOTH MATTER
    The walk order, so two attempts seat people in a different sequence. And the
    order the tables are CONSIDERED in, which is less obvious and was worth a
    measurement: scanning tables 0 upward and taking the first zero cost one
    biases every draw toward the low numbered tables, and that bias is shared by
    every attempt, so sixty attempts explore far less than sixty should. On a
    40 person list over 8 companies it was the difference between one repeat
    pairing and none.
    """
    room = [[] for _ in sizes]
    order = list(people)
    rng.shuffle(order)
    indices = list(range(len(sizes)))

    for person in order:
        pid = person["id"]
        rng.shuffle(indices)
        best, best_cost = None, None
        for index in indices:
            table = room[index]
            if len(table) >= sizes[index]:
                continue
            cost = 0
            for other in table:
                if counts.get(_key(pid, other["id"]), 0):
                    cost += MET_PENALTY
                if person["_ck"] and other["_ck"] == person["_ck"]:
                    cost += COMPANY_PENALTY
            if best_cost is None or cost < best_cost:
                best, best_cost = index, cost
                if cost == 0:
                    # Nothing beats a table that costs nothing, and because the
                    # scan order is random this is not the same table twice.
                    break
        # best is never None: table_sizes sums to len(people), so while anybody
        # is unseated at least one table still has room.
        room[best].append(person)

    return room


def _key(a, b):
    return (a, b) if a < b else (b, a)


def _repeats(counts):
    """
    Repeat pairings across the whole draw.

    A pair meeting three times counts twice, because it wasted two rounds and
    not one. This is the number the page shows and the number the search
    minimises.
    """
    return sum(c - 1 for c in counts.values() if c > 1)


def _shift(counts, pairs, sign):
    """
    Add or remove `pairs` from `counts`, returning the change in repeats.

    Incremental, because the repair pass below evaluates thousands of candidate
    swaps and rescoring the whole draw for each would make it too slow to run on
    every draw. A pair's contribution to the score is max(0, count - 1), so
    adding one only costs a repeat if the pair already existed, and removing one
    only saves a repeat if it existed twice.
    """
    delta = 0
    for pair in pairs:
        count = counts.get(pair, 0)
        if sign > 0:
            if count >= 1:
                delta += 1
            counts[pair] = count + 1
        else:
            if count >= 2:
                delta -= 1
            if count <= 1:
                counts.pop(pair, None)
            else:
                counts[pair] = count - 1
    return delta


def _in_repeat(person, table, counts):
    """Is this person sharing this table with somebody they already met."""
    pid = person["id"]
    return any(
        counts.get(_key(pid, other["id"]), 0) > 1
        for other in table if other is not person
    )


def _try_swap(room, table_a, person_a, table_b, person_b, counts):
    """
    Swap two people if it reduces repeats, otherwise leave everything untouched.

    Returns the change in score, zero when the swap was rejected. Only the pairs
    at the two tables involved change, which is why this can be evaluated in the
    size of a table rather than the size of the draw.
    """
    rest_a = [p for p in room[table_a] if p is not person_a]
    rest_b = [p for p in room[table_b] if p is not person_b]
    remove = ([_key(person_a["id"], o["id"]) for o in rest_a]
              + [_key(person_b["id"], o["id"]) for o in rest_b])
    add = ([_key(person_a["id"], o["id"]) for o in rest_b]
           + [_key(person_b["id"], o["id"]) for o in rest_a])

    delta = _shift(counts, remove, -1) + _shift(counts, add, +1)
    if delta < 0:
        room[table_a] = rest_a + [person_b]
        room[table_b] = rest_b + [person_a]
        return delta
    # Exact inverse, so a rejected swap leaves no trace in counts.
    _shift(counts, add, -1)
    _shift(counts, remove, +1)
    return 0


def _repair(seated, counts, floor, budget=REPAIR_BUDGET, max_passes=60):
    """
    Swap people between tables while it reduces repeat pairings.

    WHY THE GREEDY FILL NEEDS THIS
    Seating a round one person at a time means the last few attendees have only
    one table with room left, so their seats are forced whatever the cost. On a
    40 person list that tail reliably leaves one or two repeats behind, and no
    number of extra random attempts removes them, because every attempt has the
    same forced tail. Swapping afterwards does, because by then every seat is
    filled and a swap is always available.

    Only swaps involving somebody currently IN a repeat are considered, which
    keeps the candidate set at a handful of people rather than the whole room.
    A swap is accepted only when it strictly reduces the score, so the pass
    cannot loop, and it stops the moment the arithmetic floor is reached.

    Company clashes are deliberately not part of the accept test. MET_PENALTY is
    more than three times COMPANY_PENALTY for a reason, a repeat wastes a whole
    round for two people while sitting near a colleague wastes one conversation,
    and a repair that refused to separate two colleagues would be honouring the
    lesser of the two.
    """
    total = _repeats(counts)
    spent = 0
    for _ in range(max_passes):
        if total <= floor or spent >= budget:
            break
        taken = 0
        for room in seated:
            if spent >= budget:
                break
            for table_a in range(len(room)):
                # Snapshot, because an accepted swap mutates room[table_a]
                # underneath this loop.
                for person_a in list(room[table_a]):
                    if person_a not in room[table_a]:
                        continue
                    if not _in_repeat(person_a, room[table_a], counts):
                        continue
                    for table_b in range(len(room)):
                        if table_b == table_a:
                            continue
                        moved = False
                        for person_b in list(room[table_b]):
                            if spent >= budget:
                                break
                            # Charged by table size: this is what _try_swap
                            # walks, and the reason a flat charge per swap
                            # under-budgeted exactly the rooms with big tables.
                            spent += len(room[table_a]) + len(room[table_b])
                            delta = _try_swap(
                                room, table_a, person_a, table_b, person_b, counts,
                            )
                            if delta < 0:
                                total += delta
                                taken += 1
                                moved = True
                                break
                        if moved:
                            break
        if not taken:
            # A full pass with nothing to gain is a local minimum, and further
            # passes would repeat it exactly.
            break
    return total, spent


def _pigeonhole_repeats(size, groups):
    """
    The fewest already met pairs at ONE table of `size`, refilled from `groups`.

    THE CONSTRAINT THIS CAPTURES, WHICH COUNTING MISSES ENTIRELY
    After round one the room is partitioned into `groups` tables. To seat a table
    of `size` in round two with nobody meeting again, every one of those `size`
    people has to come from a DIFFERENT round one table. That needs at least as
    many tables as there are seats at a table. Three tables of six cannot do it,
    because six people drawn from three groups must include two from the same
    group whatever anyone does.

    Spreading the draw as evenly as possible over the groups is what minimises
    it, so the floor is the balanced split, q or q+1 from each group, summed as
    C(taken, 2).
    """
    if groups <= 0:
        return 0
    q, r = divmod(size, groups)
    return (r * ((q + 1) * q // 2)) + ((groups - r) * (q * (q - 1) // 2))


def floor_repeats(sizes, rounds, attendees):
    """
    The fewest repeat pairings this room CAN have, however clever the seating.

    TWO INDEPENDENT LOWER BOUNDS, AND THE LARGER ONE WINS

    The counting bound. Each round hands out a fixed number of pair slots and
    the room only holds so many distinct pairs, so when the slots outnumber the
    pairs the surplus is forced. Thirteen people over two tables spends 108 pair
    slots on a room containing 78 pairs, forcing 30 repeats.

    The pigeonhole bound. Every round after the first has to refill each table
    from the previous round's partition, and _pigeonhole_repeats above says what
    that costs when the tables are too few to go round. Eighteen people over
    three tables of six forces 18 repeats by this bound and none at all by the
    counting one, which is why counting alone was not good enough; the optimiser
    was hitting a wall the number said was not there, and the page would have
    reported a perfectly good draw as a failure forever.

    Both are LOWER BOUNDS rather than achievable targets, so a draw sitting above
    the larger of them is not proof of a bad draw. It is only the equality that
    proves anything, which is why `optimal` is set from `repeats <= floor` and
    the wording it drives says best possible rather than merely best found.
    """
    slots = rounds * sum(s * (s - 1) // 2 for s in sizes)
    pairs = attendees * (attendees - 1) // 2
    counting = max(0, slots - pairs)

    groups = len(sizes)
    pigeonhole = max(0, rounds - 1) * sum(
        _pigeonhole_repeats(size, groups) for size in sizes
    )
    return max(counting, pigeonhole)


def _one_draw(people, sizes, rounds, rng):
    """
    One complete draw. Returns the seating and the pair counts behind it.

    `counts` maps a pair to the number of rounds it shares a table, and it is the
    ONE structure the whole module reads. An earlier version also carried a `met`
    adjacency dict, which answered the same question in a second shape and had to
    be kept in step with this one by hand.

    It is updated AFTER each round rather than during, so the cost function never
    penalises a person for the table it is currently deciding about.
    """
    counts = {}
    seated = []
    for _ in range(rounds):
        room = _seat_one_round(people, sizes, counts, rng)
        for table in room:
            for a, b in combinations(table, 2):
                key = _key(a["id"], b["id"])
                counts[key] = counts.get(key, 0) + 1
        seated.append(room)
    return seated, counts


def build_plan(
    people,
    tables=None,
    per_table=None,
    rounds=DEFAULT_ROUNDS,
    attempts=DEFAULT_ATTEMPTS,
    seed=None,
):
    """
    Draw the plan.

    `people` is an iterable of dicts carrying at least id, name and company.
    `tables` is HOW MANY TABLES THE ROOM HAS, and it is honoured exactly.
    `per_table` is HOW MANY PEOPLE CAN SIT AT ONE, a maximum. Leave both None to
    fall back on the workbook's own suggestion. Returns a dict ready to store on
    NetworkingPlan, plus the figures the page shows. Leave `seed` as None for a
    fresh draw.

    THE TWO NUMBERS A VENUE ACTUALLY QUOTES, AND WHY BOTH ARE HERE
    A room is described as "six tables, ten chairs each", so both halves are
    real inputs and neither is derivable from the other without the head count.
    They are separate arguments rather than one number BECAUSE one number is
    exactly the bug that was reported here: the field used to be per_table
    alone, the count was derived from it, and asking for six produced 28 tables
    on a 170 person event. Named separately, neither can be mistaken for the
    other.

    What each does:

    - `tables` alone, the count is used and the table size follows from it.
    - `per_table` alone, the count follows, `ceil(attendees / per_table)`,
      never fewer than MIN_TABLES. This is the field for a caller who knows the
      chairs and not the tables.
    - BOTH, the count still wins and `per_table` is checked against it. Where
      the room cannot hold the list, ValueError names both ways out rather than
      quietly reseating people into chairs that are not there.
    - NEITHER, suggested_table_count.

    `per_table` is a CEILING, not a target. Forty-five people over six tables
    sit 8, 8, 8, 7, 7, 7 whatever ten chairs a table would allow, because
    spreading a room across the tables it has is what makes people meet, and
    filling four tables to ten to leave two empty is the opposite.

    THE TABLE COUNT IS NOT NEGOTIABLE AND NOT OPTIMISED
    Whatever it costs in repeat pairings, the draw uses exactly that many
    tables. Six tables of 28 people across three rounds forces hundreds of
    repeats and there is nothing to be done about it except run fewer rounds or
    find more tables, and both of those are somebody else's decision. So the
    result reports the floor rather than advising a different room.

    REPRODUCING A DRAW
    The seed returned is the seed of the whole SEARCH, and passing it back with
    the same people in the same order, the same per_table, rounds and attempts,
    reproduces the identical draw. An earlier version returned the winning
    attempt's own seed instead, on the theory that reproduction could then skip
    the search; it could not, because the search is what generates each attempt's
    seed, so feeding a winner back in produced a different draw entirely. The
    check in tests_networking.py is there to keep that from coming back.
    """
    roster = [
        {
            "id": p["id"],
            "name": p.get("name") or "",
            "company": p.get("company") or "",
            # Same company, case and spacing insensitive; blank never clashes.
            "_ck": " ".join((p.get("company") or "").split()).casefold(),
        }
        for p in people
    ]

    if not roster:
        return {
            "assignment": [], "tables": 0, "attendees": 0,
            "repeat_pairs": 0, "floor_repeats": 0, "optimal": True,
            "seed": 0, "rounds": rounds, "largest_table": 0,
        }

    per_table = int(per_table) if per_table else 0
    if not tables or tables < 1:
        # The chairs drive the count when the count is not given, which is the
        # whole point of the field: a venue quotes chairs per table more readily
        # than it quotes tables.
        tables = (max(MIN_TABLES, -(-len(roster) // per_table)) if per_table
                  else suggested_table_count(len(roster)))
    # More tables than people leaves empty tables, which is not an error but is
    # not a seating either; cap it so every table has somebody at it.
    tables = max(1, min(int(tables), len(roster)))
    sizes = table_sizes(len(roster), tables)

    # BOTH GIVEN AND THEY DISAGREE. Raised rather than resolved: seating twelve
    # people at a table of ten is not something to decide on the caller's behalf,
    # and neither is quietly adding the seventh table. Both numbers that would
    # work are in the message, because "does not fit" without them is a dead end.
    if per_table and sizes[0] > per_table:
        raise ValueError(
            f"{tables} tables of {per_table} seat {tables * per_table}, and "
            f"{len(roster)} people are registered. Either {-(-len(roster) // per_table)} "
            f"tables at {per_table} per table, or {sizes[0]} per table at "
            f"{tables} tables."
        )
    if seed is None:
        seed = random.randrange(2 ** 31)
    master = random.Random(seed)
    floor = floor_repeats(sizes, rounds, len(roster))

    # Seating one round costs about people * tables * table_size cost lookups,
    # so a big room is expensive per attempt AND needs the attempts least: with
    # hundreds of forced repeats the first greedy fill already lands within a
    # percent of the floor and the swap pass does the rest. Sixty attempts is
    # right for a room of small tables and absurd for six tables of sixty-seven.
    per_attempt = max(1, rounds * len(roster) * tables * max(sizes))
    attempts = max(1, min(int(attempts), SEAT_BUDGET // per_attempt))

    # EVERY attempt is repaired, not just the winner.
    #
    # Repairing only the best raw draw left a 40 person room stuck one repeat
    # above the floor and a 6 person room two above it, because first improvement
    # swapping has local minima and one draw gets one escape attempt. Repaired
    # per attempt, the floor is reached on every size measured. It is also FASTER
    # in the normal case rather than slower, because a repaired draw usually hits
    # the floor on the first or second attempt and the loop stops there; the
    # unrepaired version had to grind through all sixty before giving up.
    best = None
    spent = 0
    for _ in range(max(1, attempts)):
        seated, counts = _one_draw(
            roster, sizes, rounds, random.Random(master.randrange(2 ** 31)),
        )
        repeats, used = _repair(seated, counts, floor, REPAIR_BUDGET - spent)
        spent += used
        if best is None or repeats < best[0]:
            best = (repeats, seated)
        if repeats <= floor or spent >= REPAIR_BUDGET:
            # Either provably the best this room allows, or the budget is spent
            # and further attempts would cost more than they can win.
            break

    repeats, seated = best

    # Per person table numbers, in round order, one based because they are
    # printed on a card somebody reads out loud.
    tables_for = {p["id"]: [0] * rounds for p in roster}
    for round_index, room in enumerate(seated):
        for table_index, table in enumerate(room):
            for person in table:
                tables_for[person["id"]][round_index] = table_index + 1

    assignment = [
        {
            "delegate_id": p["id"],
            "name": p["name"],
            "company": p["company"],
            "tables": tables_for[p["id"]],
        }
        for p in sorted(roster, key=lambda p: (p["_ck"], p["name"]))
    ]

    return {
        "assignment": assignment,
        "tables": tables,
        "attendees": len(roster),
        "repeat_pairs": repeats,
        # What this room makes unavoidable, and whether the draw hit it. The
        # page says "best possible" off `optimal`, never off repeat_pairs being
        # zero, because with the table count fixed zero is often not available.
        "floor_repeats": floor,
        "optimal": repeats <= floor,
        "seed": seed,
        "rounds": rounds,
        # How many people end up at the busiest table, which is the figure
        # somebody laying out a room actually needs.
        "largest_table": max(sizes),
    }
