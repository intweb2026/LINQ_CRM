"""
events/tests_owner_routing.py
─────────────────────────────
Four sales accounts were added in production with the right team and the right
permissions, and every one of them opened an empty Events page. The events they
own name them in the SCA column; what none of those rows had was
`Event.sales_executive`, the FK that events/views.py get_queryset and
accounts.User.assigned_event_codes both scope by. The FK was resolved once, while
the event row was being saved, against a user table that did not yet contain
them, and nothing ever re-ran it.

What these tests hold still:

  * a name resolves to a user, or to NOBODY. The old resolver ended in a two way
    substring compare that took the first hit, so "Sam" claimed Samantha's
    events and a full name was claimed by anyone whose name was a fragment of it.
    That is silent and it is wrong in both directions;
  * two people who cannot be told apart leave the field ALONE and are reported;
  * creating an account picks up the events that already name it, so configuring
    a new starter is complete in one step;
  * routing never takes an event off its current owner;
  * the FK a human set by hand outranks any string, and clearing the SCA text
    still unassigns.
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.serializers import UserWriteSerializer
from accounts.user_resolution import AMBIGUOUS, EMPTY, NO_MATCH, resolve_owner
from events.models import Event
from events.owner_routing import route_events, route_events_for_user

User = get_user_model()


def make_user(username, first="", last="", email="", **kwargs):
    return User.objects.create_user(
        username=username, password="x",
        first_name=first, last_name=last,
        email=email or (username + "@iq-hub.com"),
        **kwargs,
    )


def make_event(code, sales_team="", **kwargs):
    return Event.objects.create(
        event_code=code, event_date=date(2026, 6, 1),
        sales_team=sales_team, **kwargs,
    )


class NameResolutionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.harrison = make_user("harrison.peck", "Harrison", "Peck")
        cls.samantha = make_user("sjones", "Samantha", "Jones")

    def test_exact_full_name(self):
        self.assertEqual(resolve_owner("Harrison Peck")[0], self.harrison)

    def test_case_spacing_and_punctuation_are_not_the_answer(self):
        for text in ("harrison peck", "  HARRISON   PECK ", "Harrison-Peck",
                     "Peck, Harrison", "harrison.peck", "harrison.peck@iq-hub.com"):
            with self.subTest(text=text):
                self.assertEqual(resolve_owner(text)[0], self.harrison)

    def test_reversed_order(self):
        self.assertEqual(resolve_owner("Peck Harrison")[0], self.harrison)

    def test_middle_name_on_one_side_only(self):
        self.assertEqual(resolve_owner("Harrison James Peck")[0], self.harrison)

    def test_unique_first_name(self):
        self.assertEqual(resolve_owner("Harrison")[0], self.harrison)

    def test_a_fragment_is_not_a_match(self):
        # The regression. "Sam" was contained in "samantha jones", so the old
        # resolver in Event.save() handed Samantha's events to whoever typed it.
        for text in ("Sam", "Jon", "Pec", "Harris"):
            with self.subTest(text=text):
                user, reason = resolve_owner(text)
                self.assertIsNone(user)
                self.assertEqual(reason, NO_MATCH, text)

    def test_a_longer_name_does_not_swallow_a_shorter_one(self):
        # "Harrison Peck" contains no user's full name, and the reverse compare
        # used to make any single word user a match for it.
        make_user("h", "H", "")
        self.assertEqual(resolve_owner("Harrison Peck")[0], self.harrison)

    def test_two_people_with_one_name_is_ambiguous_not_a_coin_toss(self):
        # Both in sales, so the preferred-role tie break cannot separate them
        # either, and that is the point; it is a tie break, not a licence to pick.
        make_user("hpeck2", "Harrison", "Peck")
        user, reason = resolve_owner("Harrison Peck")
        self.assertIsNone(user)
        self.assertEqual(reason, AMBIGUOUS)

    def test_blank_placeholders_are_nobody_not_a_failure(self):
        # EMPTY rather than NO_MATCH. NewEventModal writes an em dash into owner
        # columns it has no editor for, and counting those as failures would bury
        # the names that genuinely need a human in a report nobody then reads.
        for text in ("", "  ", "-", "—", "N/A", "TBC"):
            with self.subTest(text=text):
                user, reason = resolve_owner(text)
                self.assertIsNone(user)
                self.assertEqual(reason, EMPTY)

    def test_sales_role_wins_a_tie_with_another_role(self):
        # Same first name, one in sales, one not. The SCA column names a sales
        # person, so that pool is tried first and the answer is not ambiguous.
        make_user("hjones", "Harrison", "Jones", role=User.Role.OPERATIONS)
        self.assertEqual(resolve_owner("Harrison")[0], self.harrison)

    def test_a_non_sales_account_is_still_reachable(self):
        # User.save() derives role from the team NAME, so a genuine sales person
        # can end up labelled otherwise. They must not become unresolvable.
        ops = make_user("kim.lee", "Kim", "Lee", role=User.Role.OPERATIONS)
        self.assertEqual(resolve_owner("Kim Lee")[0], ops)

    def test_inactive_accounts_are_not_matched(self):
        # Deactivation in this product is `status`; User.save() drives is_active
        # from it (accounts/models.py:126-130), so that is what has to be set.
        make_user("gone", "Gone", "Away", status=User.Status.INACTIVE)
        self.assertEqual(resolve_owner("Gone Away"), (None, NO_MATCH))


class EventSaveOwnerSyncTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.harrison = make_user("harrison.peck", "Harrison", "Peck")
        cls.samantha = make_user("sjones", "Samantha", "Jones")

    def test_create_with_sca_text_sets_the_fk(self):
        ev = make_event("NEW-1", sales_team="Harrison Peck")
        self.assertEqual(ev.sales_executive, self.harrison)

    def test_create_normalises_the_text_to_the_account_name(self):
        ev = make_event("NEW-2", sales_team="harrison.peck@iq-hub.com")
        self.assertEqual(ev.sales_team, "Harrison Peck")

    def test_create_with_an_explicit_fk_names_the_text_column(self):
        ev = Event.objects.create(event_code="NEW-3", event_date=date(2026, 6, 1),
                                  sales_executive=self.samantha)
        self.assertEqual(ev.sales_team, "Samantha Jones")

    def test_changing_the_text_moves_the_event(self):
        ev = make_event("MOVE-1", sales_team="Harrison Peck")
        ev.sales_team = "Samantha Jones"
        ev.save()
        ev.refresh_from_db()
        self.assertEqual(ev.sales_executive, self.samantha)

    def test_clearing_the_text_unassigns(self):
        ev = make_event("CLEAR-1", sales_team="Harrison Peck")
        ev.sales_team = ""
        ev.save()
        ev.refresh_from_db()
        self.assertIsNone(ev.sales_executive)

    def test_a_hand_set_fk_outranks_the_text(self):
        ev = make_event("FK-1", sales_team="Harrison Peck")
        ev.sales_executive = self.samantha
        ev.save()
        ev.refresh_from_db()
        self.assertEqual(ev.sales_executive, self.samantha)
        self.assertEqual(ev.sales_team, "Samantha Jones")

    def test_an_unknown_name_leaves_the_fk_null_and_keeps_the_text(self):
        ev = make_event("UNK-1", sales_team="Someone Else")
        self.assertIsNone(ev.sales_executive)
        self.assertEqual(ev.sales_team, "Someone Else")

    def test_an_ambiguous_name_does_not_disturb_an_existing_owner(self):
        ev = make_event("AMB-1", sales_team="Harrison Peck")
        make_user("hpeck2", "Harrison", "Peck")
        ev.sales_team = "  Harrison   Peck  "   # changed text, same person named
        ev.save()
        ev.refresh_from_db()
        self.assertEqual(ev.sales_executive, self.harrison)

    def test_a_null_fk_is_re_resolved_on_any_later_save(self):
        # The production shape. The row already carries the name, the FK is null
        # because the account did not exist when it was written, and the text is
        # not being changed by this save.
        ev = make_event("LEGACY-1", sales_team="Nobody Yet")
        self.assertIsNone(ev.sales_executive)
        nobody = make_user("nobody.yet", "Nobody", "Yet")
        ev.location = "Berlin"
        ev.save()
        ev.refresh_from_db()
        self.assertEqual(ev.sales_executive, nobody)

    def test_a_blank_placeholder_fills_in_from_the_fk(self):
        ev = Event.objects.create(event_code="DASH-1", event_date=date(2026, 6, 1),
                                  sales_team="—", sales_executive=self.harrison)
        self.assertEqual(ev.sales_team, "Harrison Peck")


class RoutingBackfillTests(TestCase):
    """
    The repair pass, which is what actually rescues the accounts that are already
    in production with an empty screen.
    """

    def setUp(self):
        self.owned = make_event("OWNED-1", sales_team="Nadia Rahman")
        self.other = make_event("OWNED-2", sales_team="Nadia Rahman")
        self.someone_elses = make_event("OTHER-1", sales_team="Nadia Rahman")
        self.assertIsNone(self.owned.sales_executive)

    def test_creating_the_account_routes_its_events(self):
        nadia = make_user("nadia.rahman", "Nadia", "Rahman")
        report = route_events_for_user(nadia)
        self.assertEqual(len(report["routed"]), 3)
        self.owned.refresh_from_db()
        self.assertEqual(self.owned.sales_executive, nadia)

    def test_the_user_api_routes_on_create_with_no_second_step(self):
        ser = UserWriteSerializer(data={
            "username": "nadia.rahman", "first_name": "Nadia",
            "last_name": "Rahman", "email": "nadia.rahman@iq-hub.com",
            "password": "hunter2hunter2", "role": User.Role.SALES,
        })
        ser.is_valid(raise_exception=True)
        nadia = ser.save()
        self.owned.refresh_from_db()
        self.assertEqual(self.owned.sales_executive, nadia)

    def test_the_new_user_can_now_see_those_events(self):
        # The property the empty screens were a symptom of. assigned_event_codes
        # feeds Bookings and Delegates through accounts/permissions.py, and
        # events/views.py filters on the same pair of links.
        nadia = make_user("nadia.rahman", "Nadia", "Rahman")
        self.assertEqual(nadia.assigned_event_codes(), [])
        route_events_for_user(nadia)
        self.assertCountEqual(nadia.assigned_event_codes(),
                              ["OWNED-1", "OWNED-2", "OTHER-1"])

    def test_routing_never_takes_an_event_off_its_owner(self):
        incumbent = make_user("i.holder", "Ida", "Holder")
        self.someone_elses.sales_executive = incumbent
        self.someone_elses.save()

        nadia = make_user("nadia.rahman", "Nadia", "Rahman")
        route_events_for_user(nadia)

        self.someone_elses.refresh_from_db()
        self.assertEqual(self.someone_elses.sales_executive, incumbent)

    def test_reassign_is_the_only_way_an_owned_row_moves(self):
        incumbent = make_user("i.holder", "Ida", "Holder")
        Event.objects.filter(pk=self.someone_elses.pk).update(
            sales_executive=incumbent, sales_team="Nadia Rahman",
        )
        nadia = make_user("nadia.rahman", "Nadia", "Rahman")
        route_events(users=[nadia], reassign=True)
        self.someone_elses.refresh_from_db()
        self.assertEqual(self.someone_elses.sales_executive, nadia)

    def test_dry_run_writes_nothing(self):
        nadia = make_user("nadia.rahman", "Nadia", "Rahman")
        report = route_events(users=[nadia], commit=False)
        self.assertEqual(len(report["routed"]), 3)
        self.owned.refresh_from_db()
        self.assertIsNone(self.owned.sales_executive)

    def test_scoped_to_one_user_leaves_other_names_alone(self):
        make_event("THIRD-1", sales_team="Omar Farouk")
        omar = make_user("omar.farouk", "Omar", "Farouk")
        nadia = make_user("nadia.rahman", "Nadia", "Rahman")

        route_events(users=[nadia])

        self.assertEqual(Event.objects.get(event_code="THIRD-1").sales_executive, None)
        route_events(users=[omar])
        self.assertEqual(Event.objects.get(event_code="THIRD-1").sales_executive, omar)

    def test_unresolved_names_are_reported_not_silently_dropped(self):
        make_event("GHOST-1", sales_team="Ghost Person")
        report = route_events(commit=False)
        unmatched = {row["name"] for row in report["unmatched"]}
        self.assertIn("Ghost Person", unmatched)

    def test_ambiguous_names_are_reported_and_skipped(self):
        make_user("nadia.rahman", "Nadia", "Rahman")
        make_user("nrahman2", "Nadia", "Rahman")   # same name, also sales
        report = route_events(commit=False)
        self.assertEqual(len(report["routed"]), 0)
        self.assertEqual(len(report["ambiguous"]), 3)
        self.owned.refresh_from_db()
        self.assertIsNone(self.owned.sales_executive)

    def test_renaming_a_user_routes_under_the_corrected_spelling(self):
        wrong = make_user("nadia.r", "Nadai", "Rahman")
        self.assertIsNone(Event.objects.get(event_code="OWNED-1").sales_executive)

        ser = UserWriteSerializer(wrong, data={"first_name": "Nadia"}, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()

        self.assertEqual(Event.objects.get(event_code="OWNED-1").sales_executive, wrong)
