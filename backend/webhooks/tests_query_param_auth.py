"""
webhooks/tests_query_param_auth.py
───────────────────────────────────
The API key is accepted in the query string, so ONE URL is a complete
integration.

WHY: handing an external team a URL they can paste into curl, a browser or a
monitoring check turns a multi-day back-and-forth about header configuration
into a same-day test. The whole value of that is destroyed if the URL only
works when the sender also gets the Content-Type right, so the ingest view now
parses a JSON body under any declared media type, and undoes the mangling that
application/x-www-form-urlencoded does to a JSON body.

WHAT MUST NOT MOVE: the X-CRM-API-KEY header keeps absolute priority, so no
sender that authenticates today can change behaviour; and a key that travelled
in a URL must never be written back out into WebhookLog.headers or .payload,
because the logs UI is readable by people the key was never shared with.
"""
import json
from datetime import date
from urllib.parse import urlencode

from django.test import TestCase, override_settings
from django.urls import reverse

from book_event.models import BookEvent
from webhooks.models import WebhookApiKey, WebhookLog
from webhooks.tests_event_resolution import make_event
from webhooks.utils import QUERY_KEY_ALIASES
from webhooks.views import WebhookIngestionView


def payload(invoice="INV-URL-001"):
    return {
        "InvoiceNumber": invoice,
        "Eventcode":     "TST - PM",
        "Eventname":     "whatever",
        "Date":          "2026-02-11",
        "InvoiceDate":   "2026-02-01",
        "Discount":      0,
        "PreTaxAmount":  100,
        "TaxAmount":     0,
        "TotalAmount":   100,
        "AddOnsTotalAmount": 0,
        "Delegates": [{
            "FirstName": "Test",
            "LastName":  "Person",
            "Email":     "test.person@example.com",
        }],
    }


def ingest_url(**params):
    """The ingest path with an arbitrary query string attached."""
    base = reverse("webhook-ingest")
    return f"{base}?{urlencode(params)}" if params else base


@override_settings(WEBHOOK_SECRET_KEY="")
class QueryStringKeyIngestsTests(TestCase):
    """The point of the change: a URL alone is enough to land a booking."""

    @classmethod
    def setUpTestData(cls):
        make_event("TST - PM", web_bookings=True, event_date=date(2026, 2, 11))
        cls.raw_key = WebhookApiKey.generate_key()
        cls.key = WebhookApiKey.objects.create(name="url-test", api_key=cls.raw_key)

    def _post_url(self, body, *, invoice="INV-URL-001", content_type="application/json"):
        return self.client.post(
            ingest_url(**{"X-CRM-API-KEY": self.raw_key}),
            data=body, content_type=content_type,
        )

    def test_json_body_with_a_url_key_creates_the_booking(self):
        resp = self._post_url(json.dumps(payload("INV-URL-001")))
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertTrue(BookEvent.objects.filter(invoice_number="INV-URL-001").exists())

    def test_a_url_key_delivery_leaves_exactly_one_log_row(self):
        self._post_url(json.dumps(payload("INV-URL-002")))
        self.assertEqual(WebhookLog.objects.count(), 1)

    def test_no_content_type_at_all_still_creates_the_booking(self):
        """
        An absent Content-Type header reaches DRF as "" (META.get with a ""
        default), which is what an empty content_type reproduces here. Before
        AnyTypeJSONParser this was a bare 415 that named nothing.
        """
        resp = self._post_url(json.dumps(payload("INV-URL-003")), content_type="")
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertTrue(BookEvent.objects.filter(invoice_number="INV-URL-003").exists())

    def test_text_plain_json_body_still_creates_the_booking(self):
        resp = self._post_url(json.dumps(payload("INV-URL-004")), content_type="text/plain")
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertTrue(BookEvent.objects.filter(invoice_number="INV-URL-004").exists())

    def test_json_body_declared_as_a_form_still_creates_the_booking(self):
        """
        FormParser reads a body with no "=" in it as one field NAME with an
        empty value, so the entire JSON document arrives as a dict key.
        coerce_form_wrapped_json undoes exactly that.
        """
        resp = self._post_url(
            json.dumps(payload("INV-URL-005")),
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertTrue(BookEvent.objects.filter(invoice_number="INV-URL-005").exists())

    def test_a_real_form_body_is_not_mangled_by_the_new_parser(self):
        """A genuine multi-field form still goes through FormParser untouched."""
        self._post_url(
            "InvoiceNumber=INV-URL-FORM&Eventcode=TST+-+PM",
            content_type="application/x-www-form-urlencoded",
        )
        stored = WebhookLog.objects.get().payload
        self.assertIn("InvoiceNumber", stored)
        value = stored["InvoiceNumber"]
        self.assertEqual(value[0] if isinstance(value, list) else value, "INV-URL-FORM")

    def test_header_key_still_creates_the_booking(self):
        """Regression: the path every live sender uses today is untouched."""
        resp = self.client.post(
            reverse("webhook-ingest"), data=payload("INV-URL-HDR"),
            content_type="application/json", HTTP_X_CRM_API_KEY=self.raw_key,
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertTrue(BookEvent.objects.filter(invoice_number="INV-URL-HDR").exists())


@override_settings(WEBHOOK_SECRET_KEY="")
class HeaderBeatsQueryStringTests(TestCase):
    """
    The header is read first and returned the moment it is non-empty. This is
    the whole backwards-compatibility guarantee, so an existing integration's
    behaviour cannot be altered by anything appended to its URL.
    """

    @classmethod
    def setUpTestData(cls):
        make_event("TST - PM", web_bookings=True, event_date=date(2026, 2, 11))
        cls.raw_key = WebhookApiKey.generate_key()
        WebhookApiKey.objects.create(name="url-test", api_key=cls.raw_key)

    def _post(self, url, header_key, invoice):
        return self.client.post(
            url, data=payload(invoice), content_type="application/json",
            HTTP_X_CRM_API_KEY=header_key,
        )

    def test_valid_header_wins_over_a_garbage_query_key(self):
        resp = self._post(
            ingest_url(**{"X-CRM-API-KEY": "crm_live_garbage"}),
            self.raw_key, "INV-URL-PREC-1",
        )
        self.assertEqual(resp.status_code, 201, resp.content)

    def test_garbage_header_is_not_rescued_by_a_valid_query_key(self):
        resp = self._post(
            ingest_url(**{"X-CRM-API-KEY": self.raw_key}),
            "crm_live_garbage", "INV-URL-PREC-2",
        )
        self.assertEqual(resp.status_code, 401, resp.content)


@override_settings(WEBHOOK_SECRET_KEY="")
class QueryKeyAliasTests(TestCase):
    """
    A sender handed a URL retypes it, and retypes the parameter name in
    whatever shape they already use. Names are matched lowercased with hyphens
    folded to underscores.
    """

    @classmethod
    def setUpTestData(cls):
        make_event("TST - PM", web_bookings=True, event_date=date(2026, 2, 11))
        cls.raw_key = WebhookApiKey.generate_key()
        WebhookApiKey.objects.create(name="url-test", api_key=cls.raw_key)

    def _post(self, param, invoice):
        return self.client.post(
            ingest_url(**{param: self.raw_key}),
            data=payload(invoice), content_type="application/json",
        )

    def test_every_alias_in_the_frozenset_authenticates(self):
        for i, alias in enumerate(sorted(QUERY_KEY_ALIASES)):
            with self.subTest(alias=alias):
                resp = self._post(alias, f"INV-URL-ALIAS-{i}")
                self.assertEqual(resp.status_code, 201, resp.content)

    def test_alias_matching_ignores_case_and_hyphens(self):
        for i, spelling in enumerate(["x-crm-api-key", "X-CRM-API-KEY", "CRM_Key"]):
            with self.subTest(spelling=spelling):
                resp = self._post(spelling, f"INV-URL-SPELL-{i}")
                self.assertEqual(resp.status_code, 201, resp.content)


@override_settings(WEBHOOK_SECRET_KEY="")
class QueryKeyRejectionTests(TestCase):
    """A bad key in a URL is rejected exactly as a bad key in a header is."""

    @classmethod
    def setUpTestData(cls):
        make_event("TST - PM", web_bookings=True, event_date=date(2026, 2, 11))
        cls.raw_key = WebhookApiKey.generate_key()
        cls.key = WebhookApiKey.objects.create(name="url-test", api_key=cls.raw_key)

    def _post(self, key_value):
        return self.client.post(
            ingest_url(**{"X-CRM-API-KEY": key_value}),
            data=payload("INV-URL-REJ"), content_type="application/json",
        )

    def test_unknown_query_key_is_rejected(self):
        resp = self._post("crm_live_not-a-real-key")
        self.assertEqual(resp.status_code, 401, resp.content)

    def test_a_rejected_query_key_is_still_audited(self):
        self._post("crm_live_not-a-real-key")
        self.assertEqual(WebhookLog.objects.count(), 1)

    def test_deactivated_query_key_is_rejected(self):
        WebhookApiKey.objects.update(is_active=False)
        resp = self._post(self.raw_key)
        self.assertEqual(resp.status_code, 401, resp.content)


@override_settings(WEBHOOK_SECRET_KEY="")
class QueryKeyIsNotStoredTests(TestCase):
    """
    The logs UI is readable by people the key was never shared with, so a key
    that travelled in a URL must not be readable back out of a row.
    """

    @classmethod
    def setUpTestData(cls):
        make_event("TST - PM", web_bookings=True, event_date=date(2026, 2, 11))
        cls.raw_key = WebhookApiKey.generate_key()
        WebhookApiKey.objects.create(name="url-test", api_key=cls.raw_key)

    def _post(self, invoice="INV-URL-LEAK", **headers):
        return self.client.post(
            ingest_url(**{"X-CRM-API-KEY": self.raw_key}),
            data=payload(invoice), content_type="application/json", **headers,
        )

    def test_the_raw_key_is_not_in_the_stored_headers(self):
        self._post()
        log = WebhookLog.objects.get()
        self.assertNotIn(self.raw_key, json.dumps(log.headers))

    def test_the_raw_key_is_not_in_the_stored_payload(self):
        self._post()
        log = WebhookLog.objects.get()
        self.assertNotIn(self.raw_key, json.dumps(log.payload))

    def test_a_referer_carrying_the_key_is_redacted(self):
        """
        A browser that follows a link to the ingest URL sends the whole URL
        back as Referer on the next request, key included.
        """
        leaky = f"https://testserver{ingest_url(**{'X-CRM-API-KEY': self.raw_key})}"
        self._post(HTTP_REFERER=leaky)
        log = WebhookLog.objects.get()
        self.assertNotIn(self.raw_key, log.headers.get("HTTP_REFERER", ""))


@override_settings(WEBHOOK_SECRET_KEY="")
class SourceStampingTests(TestCase):
    """
    A URL-key delivery is identifiable in the logs UI without opening the row,
    because that is the population you want to find when the test is over and
    the key should be regenerated.
    """

    @classmethod
    def setUpTestData(cls):
        make_event("TST - PM", web_bookings=True, event_date=date(2026, 2, 11))
        cls.raw_key = WebhookApiKey.generate_key()
        WebhookApiKey.objects.create(name="url-test", api_key=cls.raw_key)

    def test_a_query_delivery_is_stamped(self):
        self.client.post(
            ingest_url(**{"X-CRM-API-KEY": self.raw_key}),
            data=payload("INV-URL-SRC-1"), content_type="application/json",
        )
        self.assertIn("url-auth", WebhookLog.objects.get().source)

    def test_a_header_delivery_is_not_stamped(self):
        self.client.post(
            reverse("webhook-ingest"), data=payload("INV-URL-SRC-2"),
            content_type="application/json", HTTP_X_CRM_API_KEY=self.raw_key,
        )
        self.assertNotIn("url-auth", WebhookLog.objects.get().source)

    def test_a_maximum_length_key_name_still_fits_the_column(self):
        """
        WebhookApiKey.name and WebhookLog.source are both max_length 100, so the
        stamp has to make room for itself rather than overflow the column.
        """
        long_key = WebhookApiKey.generate_key()
        WebhookApiKey.objects.create(name="N" * 100, api_key=long_key)

        self.client.post(
            ingest_url(**{"X-CRM-API-KEY": long_key}),
            data=payload("INV-URL-SRC-3"), content_type="application/json",
        )
        source = WebhookLog.objects.get().source
        max_length = WebhookLog._meta.get_field("source").max_length
        self.assertLessEqual(len(source), max_length)
        self.assertTrue(source.endswith(" [url-auth]"), source)

    def _post_malformed(self, url, **headers):
        return self.client.post(
            url, data='{"InvoiceNumber": "INV-BAD", oops',
            content_type="application/json", **headers,
        )

    def test_the_parse_error_row_tracks_the_transport(self):
        """
        The row an operator reads while diagnosing a failed URL test. It fires
        once the key has already worked and the body has not, so the stamp is
        what says the URL half of the integration is fine.
        """
        query_resp = self._post_malformed(ingest_url(**{"X-CRM-API-KEY": self.raw_key}))
        self.assertEqual(query_resp.status_code, 400, query_resp.content)
        self.assertEqual(WebhookLog.objects.get().source, "url-test [url-auth]")

        WebhookLog.objects.all().delete()

        header_resp = self._post_malformed(
            reverse("webhook-ingest"), HTTP_X_CRM_API_KEY=self.raw_key,
        )
        self.assertEqual(header_resp.status_code, 400, header_resp.content)
        self.assertEqual(WebhookLog.objects.get().source, "url-test")

    def test_an_empty_base_stamps_without_leading_whitespace(self):
        """A cell holding whitespace then a tag reads as a rendering fault."""
        stamped = WebhookIngestionView._stamp_source("", "query")
        self.assertEqual(stamped, "[url-auth]")
        self.assertEqual(stamped, stamped.lstrip())
        self.assertEqual(WebhookIngestionView._stamp_source("   ", "query"), "[url-auth]")
        self.assertEqual(WebhookIngestionView._stamp_source("", "header"), "")


@override_settings(WEBHOOK_SECRET_KEY="")
class LivenessGetTests(TestCase):
    """
    GET is a liveness check, not a delivery. It must not appear in Delivery
    logs and must not move the usage figures the keys page reports.
    """

    @classmethod
    def setUpTestData(cls):
        make_event("TST - PM", web_bookings=True, event_date=date(2026, 2, 11))
        cls.raw_key = WebhookApiKey.generate_key()
        cls.key = WebhookApiKey.objects.create(name="url-test", api_key=cls.raw_key)

    def test_get_with_a_valid_query_key_is_ok(self):
        resp = self.client.get(ingest_url(**{"X-CRM-API-KEY": self.raw_key}))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json().get("success"))

    def test_get_writes_no_log_row(self):
        self.client.get(ingest_url(**{"X-CRM-API-KEY": self.raw_key}))
        self.assertEqual(WebhookLog.objects.count(), 0)

    def test_get_does_not_bump_usage(self):
        self.client.get(ingest_url(**{"X-CRM-API-KEY": self.raw_key}))
        self.key.refresh_from_db()
        self.assertEqual(self.key.usage_count, 0)
        self.assertIsNone(self.key.last_used_at)

    def test_get_without_a_key_is_rejected_and_writes_nothing(self):
        resp = self.client.get(reverse("webhook-ingest"))
        self.assertEqual(resp.status_code, 401, resp.content)
        self.assertEqual(WebhookLog.objects.count(), 0)

    def test_a_rejected_get_warns_without_disclosing_the_key(self):
        """
        A GET leaves no WebhookLog row by design, so the application log is the
        only place a failed credential check can surface at all. It carries a
        prefix of the attempted key and never the whole value.
        """
        bogus = WebhookApiKey.generate_key()
        with self.assertLogs("webhooks.views", level="WARNING") as captured:
            resp = self.client.get(ingest_url(**{"X-CRM-API-KEY": bogus}))

        self.assertEqual(resp.status_code, 401, resp.content)
        emitted = "\n".join(captured.output)
        self.assertNotIn(bogus, emitted)
        self.assertIn(bogus[:12], emitted)
        self.assertIn("query", emitted)
        self.assertEqual(WebhookLog.objects.count(), 0)
