"""
accounts/tests_module_filter_fields.py
───────────────────────────────────────
The fields registered so that Ticket Central, Paper Review and Webhook Logs
filter over the whole table actually select the right rows.

accounts/tests_server_filter_coverage.py proves the two ends are WIRED — every
column names a field, and every field is registered. That is a static check and
it cannot tell a correct annotation from a plausible one. These run the queries.

Three of the fields here hold a value no column holds, and each restates in SQL
something the frontend computes per row (api/webhooks.js):

    api_key_name   the related key's name
    records        records_inserted + records_updated
    duration_ms    processing_duration (SECONDS, float) as rounded milliseconds

Where those drift from the cell, the rows returned stop being the rows the table
describes — which is a quieter failure than the page-only filtering they
replace, so it is worth the fixtures.

    python manage.py test accounts.tests_module_filter_fields
"""
import json
from datetime import datetime, timezone as dt_timezone
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from paper_review.models import PaperReview
from paper_review.views import PaperReviewViewSet
from teams.models import Team
from ticket_central.models import Ticket
from ticket_central.views import TicketViewSet
from webhooks.models import WebhookApiKey, WebhookLog
from webhooks.views import WebhookLogViewSet

User = get_user_model()

TICKETS = TicketViewSet.as_view({"get": "list"})
REVIEWS = PaperReviewViewSet.as_view({"get": "list"})
LOGS = WebhookLogViewSet.as_view({"get": "list"})


def spec_qs(criteria, **extra):
    q = {"filter_spec": json.dumps({"match": "all", "criteria": criteria}),
         "page_size": 500}
    q.update(extra)
    return "&".join(f"{k}={quote(str(v))}" for k, v in q.items())


class _Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.role = Team.objects.create(name="mff_admin", is_all_access=True)
        cls.user = User.objects.create_user(
            username="mff_user", password="x", role="admin", email="mff@iq-hub.com",
        )
        cls.user.team = cls.role
        cls.user.save()

    def setUp(self):
        self.factory = APIRequestFactory()

    def _ids(self, view, criteria):
        req = self.factory.get("/?" + spec_qs(criteria))
        force_authenticate(req, user=self.user)
        resp = view(req)
        resp.render()
        self.assertEqual(resp.status_code, 200, resp.content)
        return {r["id"] for r in json.loads(resp.content)["results"]}


class TicketProvenanceFilterTests(_Base):
    """
    The seven columns DEFAULT_EXCLUDES held back. Provenance is what someone
    reaches for when tracing a bad import — precisely the moment the answer has
    to cover the whole table rather than the current scroll position.
    """

    def setUp(self):
        super().setUp()
        self.a = Ticket.objects.create(
            purpose="PROV-A", type_of_ticket="BX",
            source_spreadsheet_id="sheet-alpha", source_tab="Jan",
            source_row_number=12, idempotency_key="key-alpha",
        )
        self.b = Ticket.objects.create(
            purpose="PROV-B", type_of_ticket="BX",
            source_spreadsheet_id="sheet-beta", source_tab="Feb",
            source_row_number=99, idempotency_key="key-beta",
        )
        Ticket.objects.filter(pk=self.a.pk).update(
            created_at=datetime(2026, 2, 3, 9, 0, tzinfo=dt_timezone.utc),
            updated_at=datetime(2026, 2, 3, 9, 0, tzinfo=dt_timezone.utc))
        Ticket.objects.filter(pk=self.b.pk).update(
            created_at=datetime(2026, 5, 6, 9, 0, tzinfo=dt_timezone.utc),
            updated_at=datetime(2026, 5, 6, 9, 0, tzinfo=dt_timezone.utc))

    def test_source_columns(self):
        self.assertEqual(
            self._ids(TICKETS, [{"field": "source_spreadsheet_id", "op": "is",
                                 "value": "sheet-alpha"}]),
            {self.a.id})
        self.assertEqual(
            self._ids(TICKETS, [{"field": "source_tab", "op": "is", "value": "Feb"}]),
            {self.b.id})
        self.assertEqual(
            self._ids(TICKETS, [{"field": "idempotency_key", "op": "contains",
                                 "value": "beta"}]),
            {self.b.id})

    def test_source_row_number_is_a_number_not_text(self):
        self.assertEqual(
            self._ids(TICKETS, [{"field": "source_row_number", "op": "gt", "value": 50}]),
            {self.b.id})
        self.assertEqual(
            self._ids(TICKETS, [{"field": "source_row_number", "op": "between",
                                 "values": [10, 20]}]),
            {self.a.id})

    def test_id_column(self):
        self.assertEqual(
            self._ids(TICKETS, [{"field": "id", "op": "is", "value": self.a.id}]),
            {self.a.id})

    def test_added_and_modified_time_windows(self):
        """
        Both edges of the requested day, which is what a has_time field is sent.
        A bare date as the upper bound would be that day's midnight and would
        drop everything that happened during it.
        """
        self.assertEqual(
            self._ids(TICKETS, [{"field": "created_at", "op": "between", "values": [
                "2026-02-01T00:00:00+00:00", "2026-02-28T23:59:59.999999+00:00"]}]),
            {self.a.id})
        self.assertEqual(
            self._ids(TICKETS, [{"field": "updated_at", "op": "after",
                                 "value": "2026-03-01T00:00:00+00:00"}]),
            {self.b.id})


class PaperReviewFilterTests(_Base):
    def setUp(self):
        super().setUp()
        # Two reviews sharing an email on one event are each other's duplicate;
        # the third stands alone.
        self.dup_a = PaperReview.objects.create(
            event_code="PRF - AA", speaker_name="Dup One",
            email="same@example.com", nos=True)
        self.dup_b = PaperReview.objects.create(
            event_code="PRF - AA", speaker_name="Dup Two",
            email="same@example.com", nos=False)
        self.solo = PaperReview.objects.create(
            event_code="PRF - AA", speaker_name="Solo",
            email="solo@example.com", nos=False)

    def test_duplicate_count_filters_on_the_subquery_annotation(self):
        """
        duplicate_count is a correlated Subquery attached by get_queryset, not a
        column — the marker the grid shows. filter_spec runs after get_queryset,
        so a criterion naming it is evaluated by the database per row.
        """
        self.assertEqual(
            self._ids(REVIEWS, [{"field": "duplicate_count", "op": "gt", "value": 0}]),
            {self.dup_a.id, self.dup_b.id})
        self.assertEqual(
            self._ids(REVIEWS, [{"field": "duplicate_count", "op": "is", "value": 0}]),
            {self.solo.id})

    def test_nos_is_filterable_by_the_tokens_the_picker_sends(self):
        """
        NOS? is the one boolean column. The picker sends the strings 'true' and
        'false' (PaperReviewPage's `opts`), which _coerce_value turns into real
        booleans — anything else is a 400, which is why the column has a picker
        rather than a text box.
        """
        self.assertEqual(
            self._ids(REVIEWS, [{"field": "nos", "op": "is", "value": "true"}]),
            {self.dup_a.id})
        self.assertEqual(
            self._ids(REVIEWS, [{"field": "nos", "op": "is", "value": "false"}]),
            {self.dup_b.id, self.solo.id})
        # is_not was missing from the boolean vocabulary, so "not ticked" used to
        # fall back to the loaded page.
        self.assertEqual(
            self._ids(REVIEWS, [{"field": "nos", "op": "is_not", "value": "true"}]),
            {self.dup_b.id, self.solo.id})


class WebhookLogDerivedFilterTests(_Base):
    def setUp(self):
        super().setUp()
        self.key = WebhookApiKey.objects.create(name="Zoho Live", api_key="k-live")
        self.fast = WebhookLog.objects.create(
            source="wire", status="success", api_key=self.key,
            records_inserted=3, records_updated=2, processing_duration=0.25)
        self.slow = WebhookLog.objects.create(
            source="wire", status="success",
            records_inserted=0, records_updated=1, processing_duration=2.5)
        self.untimed = WebhookLog.objects.create(
            source="wire", status="success",
            records_inserted=0, records_updated=0, processing_duration=None)

    def test_api_key_name_reaches_through_the_relation(self):
        self.assertEqual(
            self._ids(LOGS, [{"field": "api_key_name", "op": "contains",
                              "value": "zoho"}]),
            {self.fast.id})
        self.assertEqual(
            self._ids(LOGS, [{"field": "api_key_name", "op": "is_empty"}]),
            {self.slow.id, self.untimed.id})

    def test_records_is_the_sum_the_cell_shows(self):
        self.assertEqual(
            self._ids(LOGS, [{"field": "records", "op": "is", "value": 5}]),
            {self.fast.id})
        self.assertEqual(
            self._ids(LOGS, [{"field": "records", "op": "gt", "value": 0}]),
            {self.fast.id, self.slow.id})

    def test_duration_is_filtered_in_milliseconds(self):
        """
        The column stores SECONDS as a float and the cell renders milliseconds.
        Filtering the raw column for 250 would match nothing while looking like
        it worked.
        """
        self.assertEqual(
            self._ids(LOGS, [{"field": "duration_ms", "op": "is", "value": 250}]),
            {self.fast.id})
        self.assertEqual(
            self._ids(LOGS, [{"field": "duration_ms", "op": "gt", "value": 1000}]),
            {self.slow.id})
        # api/webhooks.js shows 0 for a delivery that recorded no duration, so a
        # filter for 0 has to find it rather than treating it as absent.
        self.assertEqual(
            self._ids(LOGS, [{"field": "duration_ms", "op": "is", "value": 0}]),
            {self.untimed.id})
