"""
events/tests_bulk_import_fields.py
──────────────────────────────────
Field coverage for POST /api/events/bulk_import/.

The import wizard's dropdown (frontend/src/api/import.js TARGET_FIELDS.events)
is a hand-written list with no API behind it, so it can silently drift from what
bulk_import actually consumes. It had drifted: the dropdown offered 12 targets
while the endpoint read 33, and content_check / marketing_check were on the model
but read by nobody.

test_insert_writes_every_offered_field posts one row carrying a value for every
key the dropdown now offers and asserts each one landed. If bulk_import stops
honouring a key, that test fails rather than the column vanishing on import.

Fields DERIVED in Event.save() are asserted against their source, not imported
directly — see tests_bulk_update.DERIVED_FIELDS.
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate


from events.models import Event
from events.views import EventViewSet
from teams.models import Team

User = get_user_model()

IMPORT = EventViewSet.as_view({"post": "bulk_import"})

# Mirrors TARGET_FIELDS.events in frontend/src/api/import.js. Values are chosen
# so none of them substring-matches a test user, because bulk_import runs
# _resolve_user over every user for the team fields and would rewrite the stored
# string to that user's display name.
OFFERED_ROW = {
    "event_code": "imp-fields-01",
    "official_event_name": "Offered Field Coverage Summit",
    "event_date": "2026-09-01",
    "end_date": "2026-09-03",
    "location": "Zurich",
    "event_type": "Conference",
    "vr1_sent_status": "Sent",
    "status": Event.Status.UPCOMING,
    "website_live_date": "2026-07-15",
    "website": "https://example.invalid/summit",
    "web_bookings": "yes",
    "speaker_sales_team": "Quintus Speakerdesk",
    "sales_team": "Octavia Salesdesk",
    "team_leader": "Ptolemy Leaddesk",
    "spex_team": "Xanthe Spexdesk",
    "telemarketing_team": "Waldemar Teledesk",
    "market_research_senior": "Ysolde Seniordesk",
    "market_research_junior": "Zephyrine Juniordesk",
    "event_management_team": "Vasilisa Managedesk",
    "content_check": "Content signed off",
    "marketing_check": "Marketing signed off",
    "sales_check": "Sales signed off",
    "nearest_related_event": "NEAR-01",
    "email_marketing_name": "Summit Email Name",
    "branding_name": "Summit Branding Name",
    "annualisation": "Annual",
    "date_format": "DD MMM YYYY",
    "related_event_1": "REL-01",
    "related_event_2": "REL-02",
    "related_event_3": "REL-03",
    "upcoming_event_1": "UPC-01",
    "upcoming_event_2": "UPC-02",
    "upcoming_event_3": "UPC-03",
}

# Keys stored verbatim from the row. event_code is excluded because bulk_import
# upper-cases it, and the date and boolean keys are excluded because they are
# type-converted; all four are asserted separately.
VERBATIM_KEYS = [
    k for k in OFFERED_ROW
    if k not in {"event_code", "event_date", "end_date", "website_live_date", "web_bookings"}
]


class EventBulkImportFieldTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.all_access = Team.objects.create(
            name="ev_import_admin", is_all_access=True,
        )
        cls.user = User.objects.create_user(
            username="ev_import", password="x", role="admin", email="evimp@iq-hub.com",
        )
        cls.user.team = cls.all_access
        cls.user.save()

    def setUp(self):
        self.factory = APIRequestFactory()

    def _post(self, rows, strategy="skip"):
        request = self.factory.post(
            "/api/events/bulk_import/",
            {"rows": rows, "duplicate_strategy": strategy, "batch_number": 1},
            format="json",
        )
        force_authenticate(request, user=self.user)
        return IMPORT(request)

    # ── Insert path ───────────────────────────────────────────────────────────
    def test_insert_writes_every_offered_field(self):
        response = self._post([dict(OFFERED_ROW)])
        self.assertEqual(response.status_code, 200, response.data)

        event = Event.objects.get(event_code="IMP-FIELDS-01")
        for key in VERBATIM_KEYS:
            with self.subTest(field=key):
                self.assertEqual(getattr(event, key), OFFERED_ROW[key])

        self.assertEqual(event.event_date, date(2026, 9, 1))
        self.assertEqual(event.end_date, date(2026, 9, 3))
        self.assertEqual(event.website_live_date, date(2026, 7, 15))
        self.assertIs(event.web_bookings, True)

    def test_insert_derives_from_imported_sources(self):
        """
        The dropdown offers only sources, never their derived twins. Prove each
        derived field is populated from the source that was imported.
        """
        self._post([dict(OFFERED_ROW)])
        event = Event.objects.get(event_code="IMP-FIELDS-01")

        self.assertEqual(event.name, OFFERED_ROW["official_event_name"])
        self.assertEqual(event.official_name, OFFERED_ROW["official_event_name"])
        self.assertEqual(event.city, OFFERED_ROW["location"])
        self.assertEqual(event.country, OFFERED_ROW["location"])
        self.assertEqual(event.venue, OFFERED_ROW["location"])
        self.assertIs(event.accepting_web_bookings, True)
        self.assertEqual(event.tele_marketing_team, OFFERED_ROW["telemarketing_team"])
        self.assertEqual(event.market_research_team, OFFERED_ROW["market_research_senior"])

    # ── The two fields the endpoint previously ignored ────────────────────────
    def test_insert_writes_the_two_checks(self):
        self._post([{
            "event_code": "IMP-CHECKS-01",
            "event_date": "2026-09-01",
            "content_check": "Deck approved",
            "marketing_check": "Campaign approved",
        }])
        event = Event.objects.get(event_code="IMP-CHECKS-01")
        self.assertEqual(event.content_check, "Deck approved")
        self.assertEqual(event.marketing_check, "Campaign approved")

    def test_upsert_writes_the_two_checks(self):
        Event.objects.create(
            event_code="IMP-CHECKS-02", event_date="2026-09-01",
            content_check="", marketing_check="",
        )
        response = self._post([{
            "event_code": "IMP-CHECKS-02",
            "event_date": "2026-09-01",
            "content_check": "Deck approved late",
            "marketing_check": "Campaign approved late",
        }], strategy="upsert")
        self.assertEqual(response.status_code, 200, response.data)

        event = Event.objects.get(event_code="IMP-CHECKS-02")
        self.assertEqual(event.content_check, "Deck approved late")
        self.assertEqual(event.marketing_check, "Campaign approved late")

    def test_upsert_keeps_existing_checks_when_column_blank(self):
        """
        Matches the `or existing.<field>` idiom every other upsert field uses: a
        blank cell must not wipe a value that is already there.
        """
        Event.objects.create(
            event_code="IMP-CHECKS-03", event_date="2026-09-01",
            content_check="Keep me", marketing_check="Keep me too",
        )
        self._post([{
            "event_code": "IMP-CHECKS-03",
            "event_date": "2026-09-01",
            "content_check": "",
            "marketing_check": "",
        }], strategy="upsert")

        event = Event.objects.get(event_code="IMP-CHECKS-03")
        self.assertEqual(event.content_check, "Keep me")
        self.assertEqual(event.marketing_check, "Keep me too")

    def test_checks_do_not_widen_row_visibility(self):
        """
        sales_check resolves to a user and joins assigned_users. The two new
        checks are plain strings by design, matching update_events_csv.py, so a
        name in either column must not grant that user access to the row.
        """
        outsider = User.objects.create_user(
            username="ev_outsider", password="x", role="sales",
            email="evout@iq-hub.com", first_name="Marlowe", last_name="Outsider",
        )
        self._post([{
            "event_code": "IMP-CHECKS-04",
            "event_date": "2026-09-01",
            "content_check": "Marlowe Outsider",
            "marketing_check": "Marlowe Outsider",
        }])
        event = Event.objects.get(event_code="IMP-CHECKS-04")
        self.assertEqual(event.content_check, "Marlowe Outsider")
        self.assertNotIn(outsider, event.assigned_users.all())
