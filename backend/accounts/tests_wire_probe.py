"""
accounts/tests_wire_probe.py
─────────────────────────────
The test that would have caught both shipped frontend→wire bugs.

Runs accounts/wire_probe.mjs under Node, which imports the REAL api/*.js and
lib/filterSpec.js with axios stubbed and records exactly what would go on the
wire. This suite then asserts the invariants AND replays the captured literals
against Django, so a serializer change that green unit tests would miss fails
here instead of in a browser.

Three bugs this locks down:
  1. A Set serialises to {} through JSON.stringify -> {"ids": {}} -> the
     backend answered "ids list required". Now the api layer throws first.
  2. A pre-encoded filter_spec got encoded a second time by URLSearchParams
     (%257B), and Django, which decodes once, saw literal "%7B..." text.
  3. The probe itself silently stopped matching the frontend, so every test
     here skipped and the suite went green having checked nothing. A probe
     that cannot run is now a FAILURE unless the machine genuinely lacks Node
     — see require_probe().

Skipped only when Node is unavailable, so the suite still runs on a machine
without it.

    python manage.py test accounts.tests_wire_probe
"""
import json
import shutil
import subprocess
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import CustomRole
from book_delegate.models import BookDelegate
from book_delegate.views import BookDelegateViewSet
from book_event.models import BookEvent

User = get_user_model()

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROBE = BACKEND_DIR / "accounts" / "wire_probe.mjs"
FRONTEND_SRC = BACKEND_DIR.parent / "frontend" / "src"

DELEGATES = BookDelegateViewSet.as_view({"get": "list"})


def _node():
    return shutil.which("node")


_PROBE_CACHE = {}


def run_probe():
    """
    Execute the Node probe once per process and memoise the result.

    Module-level rather than a shared setUpClass: borrowing another TestCase's
    setUpClass breaks its zero-argument super() call, which is exactly the
    error this replaced.

    Returns (result_dict_or_None, error_string_or_None).
    """
    if _PROBE_CACHE:
        return _PROBE_CACHE["result"], _PROBE_CACHE["error"]

    # `skippable` separates "this machine cannot run the probe" from "the probe
    # ran and something is wrong". Only the former may skip.
    #
    # The distinction is the whole point: when the frontend was replaced, the
    # probe died on an unstubbed axios import, run_probe() returned an error, and
    # every test below called skipTest() — so the suite stayed green while
    # asserting nothing at all about what the frontend sends. A broken probe is
    # now a failure.
    result = error = None
    skippable = False
    if not _node():
        error, skippable = "node is not on PATH", True
    elif not PROBE.exists():
        error, skippable = f"probe script missing at {PROBE}", True
    elif not FRONTEND_SRC.exists():
        error, skippable = f"frontend source missing at {FRONTEND_SRC}", True
    else:
        try:
            out = subprocess.run(
                [_node(), str(PROBE), str(FRONTEND_SRC)],
                capture_output=True, text=True, timeout=120, cwd=str(BACKEND_DIR),
            )
            if out.returncode != 0:
                error = out.stderr[-800:]
            else:
                result = json.loads(out.stdout)
        except Exception as exc:                       # noqa: BLE001
            error = repr(exc)

    _PROBE_CACHE["result"], _PROBE_CACHE["error"] = result, error
    _PROBE_CACHE["skippable"] = skippable
    return result, error


def require_probe(test_case):
    """
    Return the probe result, or end the test appropriately.

    Skips only when the environment cannot run the probe at all; a probe that ran
    and failed — or one whose imports no longer match the frontend — fails.
    """
    result, error = run_probe()
    if result is not None:
        return result
    if _PROBE_CACHE.get("skippable"):
        test_case.skipTest(f"wire probe unavailable: {error}")
    test_case.fail(
        "wire probe could not run against the real frontend modules. This is a "
        "FAILURE, not a skip: it means api/*.js no longer matches what "
        "accounts/wire_probe.mjs imports, so nothing about the frontend's wire "
        f"format is being checked.\n\n{error}"
    )
    return None


class WireProbeTests(TestCase):
    """Invariants captured from the real frontend modules."""

    def setUp(self):
        self.probe = require_probe(self)
        self.factory = APIRequestFactory()

    def test_every_wire_invariant_holds(self):
        failed = [c for c in self.probe["checks"] if not c["pass"]]
        self.assertEqual(
            failed, [],
            "wire invariants violated:\n"
            + "\n".join(f"  {c['name']}: {c['detail']}" for c in failed),
        )

    def test_probe_covered_the_real_wire_surfaces(self):
        """
        Guards against the probe quietly shrinking. It previously asserted three
        per-module bulkUpdate functions; bulk update is now a single generic
        helper in api/client.js, so the surfaces worth naming are these.
        """
        names = " ".join(c["name"] for c in self.probe["checks"])
        for surface in (
            "client.bulkUpdate", "client.assertIdArray", "filterSpec.partitionConds",
            "bookings.bulkRemove", "is_empty criterion carries no value key",
            "filter_spec is single-encoded", "rejects a Set loudly",
        ):
            self.assertIn(surface, names, f"probe no longer covers: {surface}")


class WireLiteralReplayTests(TestCase):
    """The literal query string the probe captured, replayed at Django."""

    @classmethod
    def setUpTestData(cls):
        cls.role = CustomRole.objects.create(
            name="wire_probe_admin", display_label="Wire Probe", is_all_access=True,
        )
        cls.user = User.objects.create_user(
            username="wire_probe", password="x", role="admin", email="wp@iq-hub.com",
        )
        cls.user.custom_role = cls.role
        cls.user.save()

    def setUp(self):
        self.probe = require_probe(self)
        self.factory = APIRequestFactory()
        inv = BookEvent.objects.create(
            invoice_number="WP-1", event_code="WP - AA",
            payment_status="Pending", ticket_tier="",
        )
        self.d = BookDelegate.objects.create(
            invoice=inv, event_code="WP - AA",
            first_name="Wire", last_name="Probe", email="wire@example.com",
        )

    def test_captured_list_query_is_accepted_by_django(self):
        query = self.probe["literals"]["delegates_list_query"]
        req = self.factory.get(f"/?{query}")
        force_authenticate(req, user=self.user)
        resp = DELEGATES(req)
        resp.render()
        self.assertEqual(resp.status_code, 200, resp.content)
        # tier empty AND status not Paid/Cancelled -> the one fixture row
        body = json.loads(resp.content)
        self.assertEqual({r["id"] for r in body["results"]}, {self.d.id})

    def test_captured_bulk_update_body_has_array_ids(self):
        body = self.probe["literals"]["delegates_bulk_update_body"]
        self.assertIsInstance(body["ids"], list, "ids not a list")
        self.assertNotEqual(body["ids"], {}, "ids serialised to an object")
        # A commit without plan_hash is how a stale plan gets applied silently.
        self.assertIn("plan_hash", body)

    def test_count_query_does_not_walk_every_page(self):
        """A count must cost one row, not ~35k across ~70 requests."""
        params = self.probe["literals"]["delegates_count_params"]
        self.assertEqual(params["page_size"], 1, params)
