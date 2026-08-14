"""
book_event/tests_import_schema.py
──────────────────────────────────
Guards the Smart Import field lists against drift, in all three places they live.

THE BUG THIS CLOSES
The wizard's mappable-field list was a hand-written array in
frontend/src/api/import.js. Measured against what the importers actually read it
offered 17 of 28 booking columns and 15 of ~40 ticket columns. That failure is
SILENT and it is silent in the worst way: a field absent from the list has nothing
for a spreadsheet column to map onto, so the column is skipped — and a skipped
column is indistinguishable, in the wizard and in the result summary, from a
column the file never contained. "Import 4,000 bookings" succeeded, reported no
errors, and dropped Currency, Position, Sales Executive and Delegate Count.

So the list is now published by the backend from the importer's own definition,
and these tests assert the three representations agree:

    what bulk_import READS   ==   what import_schema PUBLISHES   ==   the JS fallback

A new column added to an importer therefore fails a test until it is offered,
rather than being quietly unmappable for however long nobody notices.

The JS-scanning approach mirrors accounts/tests_pipeline_modules.py, which guards
CRM_MODULES the same way and for the same reason.
"""
import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from book_event.views import BOOKING_IMPORT_FIELDS, BookEventViewSet
from ticket_central.utils import IMPORT_HIDDEN_FIELDS, import_fields
from ticket_central.views import TicketViewSet
from teams.models import Team

User = get_user_model()

BACKEND = Path(settings.BASE_DIR)
FRONTEND = Path(settings.BASE_DIR).parent / "frontend" / "src"

# Every way the bookings importer reaches into a row.
ROW_KEY_RE = re.compile(r'_clean\(row,\s*"([a-z0-9_]+)"\)|row\.get\("([a-z0-9_]+)"\)')


def importer_row_keys(path):
    """The row keys a bulk_import implementation reads, straight from its source."""
    src = path.read_text(encoding="utf-8")
    return {a or b for a, b in ROW_KEY_RE.findall(src)}


def js_target_fields(kind):
    """
    The keys listed under TARGET_FIELDS.<kind> in frontend/src/api/import.js.

    Returns None when the file is absent so a backend-only checkout skips rather
    than fails. Deliberately crude — it only has to cope with the flat
    ['key', 'Label', ['alias']] entries this file actually contains.
    """
    path = FRONTEND / "api" / "import.js"
    if not path.exists():
        return None
    src = path.read_text(encoding="utf-8")
    # From "<kind>: [" to the closing bracket at the same two-space indent. Cannot
    # stop at the first "]," — the alias arrays are nested inside.
    m = re.search(rf"^  {kind}: \[$(.*?)^  \],$", src, re.S | re.M)
    if not m:
        return None
    return [k for k in re.findall(r"\['([a-z0-9_]+)'", m.group(1))]


def call(view, user):
    request = APIRequestFactory().get("/")
    force_authenticate(request, user=user)
    response = view(request)
    # Asserted here rather than left to a KeyError on the body: both viewsets are
    # gated by crm_permission(), which answers 403 for a user with no team,
    # and "KeyError: 'kind'" does not say that.
    assert response.status_code == 200, f"{response.status_code}: {response.data}"
    return response


def all_access_admin(username):
    """
    An admin who actually passes crm_permission().

    role="admin" alone is NOT enough: accounts/crm_permissions.py reads
    `user.team.is_all_access`, and a user without a team is refused
    whatever their role field says.
    """

    role, _ = Team.objects.get_or_create(
        name="import_schema_admin",
        defaults={"is_all_access": True},
    )
    user = User.objects.create_user(
        username=username, password="x", role=User.Role.ADMIN,
        email=f"{username}@iq-hub.com",
    )
    user.team = role
    user.save()
    return user


class BookingImportSchemaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = all_access_admin("imp.admin")

    def test_every_column_the_importer_reads_is_offered(self):
        """
        The list and the importer, compared directly. This is the assertion that
        would have caught the original defect: `currency` was read by
        bulk_import and absent from the list, and nothing anywhere said so.
        """
        offered = {key for key, _, _ in BOOKING_IMPORT_FIELDS}
        reads = importer_row_keys(BACKEND / "book_event" / "views.py")
        self.assertEqual(
            reads - offered, set(),
            "bulk_import reads these columns but Smart Import cannot map them",
        )
        self.assertEqual(
            offered - reads, set(),
            "Smart Import offers these columns but bulk_import ignores them — "
            "a column mapped to one is silently dropped",
        )

    def test_the_money_columns_are_not_offered(self):
        """
        A regression guard with a specific shape: total_amount looks like an
        obvious thing to add to this list, and bulk_import does not write it. An
        offered-but-ignored field is worse than a missing one, because the wizard
        counts the column as mapped and the row summary says nothing.
        """
        offered = {key for key, _, _ in BOOKING_IMPORT_FIELDS}
        for field in ("total_amount", "pre_tax_amount", "tax_amount",
                      "add_ons_total_amount", "payment_due_date", "parent_code"):
            self.assertNotIn(field, offered)

    def test_no_duplicate_keys_and_every_field_has_a_label(self):
        keys = [key for key, _, _ in BOOKING_IMPORT_FIELDS]
        self.assertEqual(len(keys), len(set(keys)), "a key is listed twice")
        for key, label, _ in BOOKING_IMPORT_FIELDS:
            self.assertTrue(label.strip(), f"{key} has no label")

    def test_endpoint_publishes_the_list(self):
        view = BookEventViewSet.as_view({"get": "import_schema"})
        data = call(view, self.admin).data
        self.assertEqual(data["kind"], "bookings")
        self.assertEqual(
            [f["key"] for f in data["fields"]],
            [key for key, _, _ in BOOKING_IMPORT_FIELDS],
        )
        for f in data["fields"]:
            self.assertIn("label", f)
            self.assertIsInstance(f["aliases"], list)

    def test_the_js_fallback_matches(self):
        """
        The static list ships as the offline fallback, so it has to be right on its
        own — the wizard uses it while the schema request is in flight and keeps it
        if that request fails.
        """
        js = js_target_fields("bookings")
        if js is None:
            self.skipTest("frontend/src/api/import.js not present")
        self.assertEqual(js, [key for key, _, _ in BOOKING_IMPORT_FIELDS])


class TicketImportSchemaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = all_access_admin("imp.tk")

    def test_derived_from_the_model_allowlist(self):
        """
        import_fields() must offer exactly what _coerce_row will accept — the
        model's writable columns, less the workflow-owned ones, plus created_at.
        """
        from ticket_central.models import Ticket
        from ticket_central.utils import _WRITABLE_FIELDS

        offered = {key for key, _ in import_fields()}
        expected = (set(_WRITABLE_FIELDS) - set(IMPORT_HIDDEN_FIELDS)) | {"created_at"}
        self.assertEqual(offered, expected)
        # And the exclusions are real model fields, not stale names.
        for name in IMPORT_HIDDEN_FIELDS:
            Ticket._meta.get_field(name)

    def test_workflow_fields_are_never_mappable(self):
        """
        A spreadsheet must not be able to say who submitted a ticket or when. Those
        columns are written by the transition endpoints from the request user, and
        an import that could name them could rewrite the audit trail.
        """
        offered = {key for key, _ in import_fields()}
        for field in ("created_by", "mr_submitted_by", "mr_submitted_at",
                      "dmd_submitted_by", "returned_by", "returned_at"):
            self.assertNotIn(field, offered)

    def test_the_dmd_and_lx2_blocks_are_offered(self):
        """
        The specific columns the old 15-entry list left out. Named individually
        rather than counted, so the test says what was missing.
        """
        offered = {key for key, _ in import_fields()}
        for field in ("assign_name", "assign_date", "actual_number",
                      "new_contacts_created", "mined_count", "complete_date",
                      "hubspot_entry_date", "dm_comments", "status", "ticket_type",
                      "assign_name_lx2", "actual_count_lx2", "complete_date_lx2",
                      "dm_comments_lx2", "created_at"):
            self.assertIn(field, offered)

    def test_no_duplicate_keys(self):
        keys = [key for key, _ in import_fields()]
        self.assertEqual(len(keys), len(set(keys)), "a key is listed twice")

    def test_endpoint_publishes_the_list(self):
        view = TicketViewSet.as_view({"get": "import_schema"})
        data = call(view, self.admin).data
        self.assertEqual(data["kind"], "tickets")
        self.assertEqual([f["key"] for f in data["fields"]],
                         [key for key, _ in import_fields()])

    def test_the_js_fallback_covers_the_same_fields(self):
        """
        Set equality, not order: this list is derived from model declaration order,
        which is not an order worth pinning a JS file to.
        """
        js = js_target_fields("tickets")
        if js is None:
            self.skipTest("frontend/src/api/import.js not present")
        self.assertEqual(set(js), {key for key, _ in import_fields()})
        self.assertEqual(len(js), len(set(js)), "a key is listed twice in the JS")


class EventImportSchemaTests(TestCase):
    """
    Events has no import_schema endpoint, because its JS list was already complete.
    This asserts that remains true — it is the reason the endpoint is absent, so it
    is the thing that has to keep holding.
    """

    # Derived in Event.save() from the sources the list DOES offer, so importing
    # into them would be discarded. See TARGET_FIELDS.events for the reasoning.
    DERIVED = {"name", "accepting_web_bookings", "tele_marketing_team"}

    def test_the_js_list_covers_every_column_the_importer_reads(self):
        js = js_target_fields("events")
        if js is None:
            self.skipTest("frontend/src/api/import.js not present")
        reads = importer_row_keys(BACKEND / "events" / "views.py")
        self.assertEqual(
            reads - set(js) - self.DERIVED, set(),
            "the events importer reads these columns but Smart Import cannot map them",
        )
