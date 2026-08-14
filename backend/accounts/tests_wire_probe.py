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
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from accounts.models import (
    CRM_MODULES, TEAM_NAME_ROLE_KEYWORDS, role_from_team_name,
)
from book_delegate.models import BookDelegate
from book_delegate.views import BookDelegateViewSet
from book_event.models import BookEvent
from teams.models import Team, TeamPermission

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
            # Select-all, and the batching it forced on every id-carrying
            # surface. Named here because both halves fail SILENTLY: a paged
            # select-all still selects rows and still reports a number, and an
            # unbatched 35,690-id body comes back 400 on an action the user
            # already confirmed.
            "select-all sends no page or page_size",
            "select-all narrows by exactly the same terms as the page it replaces",
            "bulkRemove batches at the endpoint's 1000-id cap",
            "bulkRemove sends every id exactly once across its batches",
            "mapLimit never exceeds its limit in flight",
            "merged plan sums permitted",
            "merged collateral is flagged as batched",
            # The Bookings modal's invoice write. Named here because both bugs it
            # covers were invisible from the browser — the request succeeded and
            # the table still looked correct — so nothing else would report their
            # return: an over-eager PATCH that empties invoice columns nobody
            # edited, and delegate fields dropped before the request is built.
            "invoice PATCH omits invoice fields the caller never set",
            "delegate payload carries booking_code",
            "discount is sent as the stored fraction",
            "transfer body uses the server's field names",
            # The admin surfaces. Named here because their bugs presented as a
            # SUCCESSFUL request: a role saved with a same-shaped permission
            # payload the backend read as all-false, and a Deactivate button
            # whose empty body the endpoint refused.
            "team permissions use the backend's can_* field names",
            "team permissions do NOT send the UI's bare view/create/update/delete keys",
            "user create sends is_team_lead, not the UI's is_lead",
            "a cell matching the team is sent as null, so it keeps inheriting",
            "a revoke is sent as false, not as an omission",
            "toggle-status patches the user's toggle action with an empty body",
            "team create carries name, colour and description",
        ):
            self.assertIn(surface, names, f"probe no longer covers: {surface}")


class WireLiteralReplayTests(TestCase):
    """The literal query string the probe captured, replayed at Django."""

    @classmethod
    def setUpTestData(cls):
        cls.role = Team.objects.create(
            name="wire_probe_admin", is_all_access=True,
        )
        cls.user = User.objects.create_user(
            username="wire_probe", password="x", role="admin", email="wp@iq-hub.com",
            team=cls.role,
        )

    def setUp(self):
        self.probe = require_probe(self)
        self.factory = APIRequestFactory()
        # The admin-surface replays below go through the router rather than a
        # hand-built request, so the URL a captured body is posted to is the same
        # one the browser uses.
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
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

    def test_captured_team_permissions_body_actually_grants_them(self):
        """
        THE REGRESSION TEST FOR THE PERMISSION-GRID BUG.

        The frontend's grid and the backend's columns are different names for the
        same four booleans, and the request carrying the wrong set still returned
        200 — set_permissions defaults every absent key to False, so a grid saved
        with every box ticked came back with none of them. Replaying the body the
        real api/teams.js builds is the only way to see that from a test: both
        payloads are well-formed JSON and both are accepted, and the grid now
        belongs to a TEAM, so one bad save is everyone in it.
        """
        body = self.probe["literals"]["team_permissions_body"]
        self.assertIsNotNone(body, "probe captured no team permissions body")

        team = Team.objects.create(name="wp_grid")
        resp = self.client.put(
            f"/api/teams/{team.id}/permissions/", body, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        stored = {p.module: p for p in team.permissions.all()}
        self.assertEqual(set(stored), set(CRM_MODULES), "not every module was written")
        for module, perm in stored.items():
            self.assertTrue(
                perm.can_view and perm.can_create and perm.can_update and perm.can_delete,
                f"{module} was ticked in the UI and stored as "
                f"view={perm.can_view} create={perm.can_create} "
                f"update={perm.can_update} delete={perm.can_delete}",
            )

    def test_captured_user_create_body_is_accepted_and_answers_with_an_id(self):
        """
        The Add user form's body, replayed.

        Two things at once: the field names have to land (is_team_lead, team_id),
        and the RESPONSE has to carry the read shape. It used to echo the write
        serializer, which has no `id` and no `full_name`, so the frontend mapped a
        freshly created user to `{id: undefined}`.
        """
        body = dict(self.probe["literals"]["user_create_body"])
        team = Team.objects.create(name="WP Sales")
        body["team_id"] = team.id
        body.pop("custom_role_id", None)

        resp = self.client.post("/api/users/", body, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)

        data = resp.json()
        self.assertIn("id", data, f"write response carries no id: {sorted(data)}")
        self.assertEqual(data["username"], body["username"])
        self.assertEqual(data["full_name"], "Ada Lovelace")

        created = User.objects.get(username=body["username"])
        self.assertTrue(created.is_team_lead, "the Team lead checkbox was discarded")
        self.assertEqual(created.team_id, team.id)
        self.assertTrue(
            created.check_password(body["password"]),
            "the password was accepted and not set",
        )

    def test_captured_team_create_body_is_accepted(self):
        body = self.probe["literals"]["team_create_body"]
        resp = self.client.post("/api/teams/", body, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        team = Team.objects.get(name=body["name"])
        self.assertEqual(team.color, body["color"])
        self.assertEqual(team.description, body["description"])
        self.assertTrue(team.slug, "the server derived no slug")

    def test_the_forms_team_to_role_preview_matches_what_save_does(self):
        """
        The Add user form fills Role in from the Team, using a JavaScript copy of
        the keyword chain in accounts/models.py. A preview that disagrees with
        the server is worse than none, because the user sees one role, saves, and
        gets another with no indication anything happened.

        Both sides are ordered keyword scans, so drift shows up on exactly the
        names where order decides the answer, and nowhere else. Every name the
        probe evaluated is put through the Python implementation here.
        """
        js_answers = self.probe["literals"]["team_name_role_map"]
        self.assertTrue(js_answers, "probe evaluated no team names")

        disagreements = []
        for name, js_role in js_answers.items():
            py_role = role_from_team_name(name)
            py_role = py_role.value if py_role is not None else None
            if py_role != js_role:
                disagreements.append(f"{name!r}: js={js_role!r} python={py_role!r}")
        self.assertEqual(
            disagreements, [],
            "the form previews a different role than User.save() stores:\n  "
            + "\n  ".join(disagreements),
        )

    def test_the_two_keyword_chains_are_the_same_list_in_the_same_order(self):
        """
        Order IS the behaviour, so matching answers on a sample is necessary but
        not sufficient; a reordering that happens not to change any sampled name
        would still be a live divergence waiting for the first team called
        something the sample does not cover.
        """
        js_pairs = [tuple(p) for p in self.probe["literals"]["team_name_role_keywords"]]
        py_pairs = [(kw, role.value) for kw, role in TEAM_NAME_ROLE_KEYWORDS]
        self.assertEqual(
            js_pairs, py_pairs,
            "frontend/src/lib/roleFromTeam.js has drifted from "
            "accounts/models.py TEAM_NAME_ROLE_KEYWORDS",
        )

    def test_captured_toggle_status_body_flips_the_status(self):
        """
        The captured body is `{}` — that IS the request. The endpoint used to
        require a `status` key and answered 400 for every click of a button whose
        entire job is to toggle.
        """
        body = self.probe["literals"]["user_toggle_body"]
        self.assertEqual(body, {}, "the drawer no longer sends an empty body")

        subject = User.objects.create_user(
            username="wp_toggle", password="x", email="wp_toggle@iq-hub.com",
        )
        self.assertEqual(subject.status, "active")

        resp = self.client.patch(
            f"/api/users/{subject.id}/toggle-status/", body, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        subject.refresh_from_db()
        self.assertEqual(subject.status, "inactive")
        self.assertFalse(subject.is_active, "is_active did not follow status")
