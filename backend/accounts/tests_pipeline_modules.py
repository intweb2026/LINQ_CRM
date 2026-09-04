"""
accounts/tests_pipeline_modules.py
──────────────────────────────────
Covers the two placeholder CRM modules — paper_review and proposal_submission
— registered ahead of their real functionality.

Two things are worth guarding here:

1.  **Default deny.** These modules exist so an admin can grant them later, not
    so they appear now. Every seeded role must hold all-False rows, and a role
    without the grant must be refused by crm_permission().

2.  **List drift.** CRM_MODULES is duplicated in three places outside
    models.py — the AuthContext matrix, the RolesPage checkbox grid, and the
    default-landing redirect. A module missing from any one of them fails
    silently and differently: no full-access grant, no admin checkbox, or a
    dead landing page. Cheaper to assert than to debug.
"""
import json
import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.crm_permissions import crm_permission
from accounts.models import CRM_MODULES
from accounts.views import UserViewSet
from teams.views import TeamViewSet
from teams.models import Team, TeamPermission

User = get_user_model()

NEW_MODULES = ["paper_review", "proposal_submission"]

FRONTEND = Path(settings.BASE_DIR).parent / "frontend" / "src"


class PipelineModuleRegistrationTests(TestCase):
    def test_both_modules_are_registered(self):
        for module in NEW_MODULES:
            self.assertIn(module, CRM_MODULES)

    def test_crm_modules_has_no_duplicates(self):
        self.assertEqual(len(CRM_MODULES), len(set(CRM_MODULES)))


class PipelineModuleDefaultDenyTests(TestCase):
    """
    Registering a module must not grant it to anyone.

    This used to assert that migration 0020 had BACKFILLED an all-false row onto
    every seeded CustomRole, because a module with no row was previously
    unanswerable — crm_permission did `permissions.get(module=...)` and a missing
    row meant an exception, not a decision.

    Access hangs off the team now and the guarantee is structural rather than
    backfilled: team_permission_matrix() and User.effective_permissions() both
    start from a dense all-false matrix and only ever turn cells ON. A module
    nobody has heard of is therefore denied by construction, with no migration
    needed to say so. That is the property worth pinning, so it is pinned
    directly instead of through the migration that used to imply it.
    """

    def test_a_module_with_no_row_grants_nothing(self):
        team = Team.objects.create(name="Blank Grid")
        member = User.objects.create_user(
            username="blank", password="x", email="blank@iq-hub.com", team=team,
        )
        resolved = member.effective_permissions()
        for module in NEW_MODULES:
            self.assertIn(module, resolved, f"{module} has no answer at all")
            self.assertFalse(any(resolved[module].values()), module)

    def test_an_existing_grid_does_not_reach_a_newly_registered_module(self):
        """
        The failure this guards: a team granted everything it was asked for at
        the time, then a module is added to CRM_MODULES and silently falls inside
        that grant. Rows are per module, so it cannot — asserted rather than
        assumed, because the cost of being wrong is a module visible to everyone
        the day it ships.
        """
        team = Team.objects.create(name="Older Grid")
        for module in CRM_MODULES:
            if module in NEW_MODULES:
                continue
            TeamPermission.objects.create(
                team=team, module=module,
                can_view=True, can_create=True, can_update=True, can_delete=True,
            )
        member = User.objects.create_user(
            username="older", password="x", email="older@iq-hub.com", team=team,
        )
        resolved = member.effective_permissions()
        for module in NEW_MODULES:
            self.assertFalse(any(resolved[module].values()),
                             f"{module} was granted by a grid written before it existed")
        self.assertTrue(resolved["bookings"]["view"], "the rest of the grid was lost")


class PipelineModulePermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.open_role = Team.objects.create(
            name="pm_admin", is_all_access=True)
        cls.admin = User.objects.create_user(
            username="pm_admin_u", password="x", role="admin", email="pma@iq-hub.com")
        cls.admin.team = cls.open_role
        cls.admin.save()

        cls.rep_role = Team.objects.create(
            name="pm_rep", is_all_access=False)
        for module in CRM_MODULES:
            TeamPermission.objects.create(
                team=cls.rep_role, module=module,
                can_view=(module == "bookings"))
        cls.rep = User.objects.create_user(
            username="pm_rep_u", password="x", role="sales", email="pmr@iq-hub.com")
        cls.rep.team = cls.rep_role
        cls.rep.save()

    def setUp(self):
        self.factory = APIRequestFactory()

    def _my_permissions(self, user):
        view = UserViewSet.as_view({"get": "my_permissions"})
        req = self.factory.get("/")
        force_authenticate(req, user=user)
        resp = view(req)
        resp.render()
        self.assertEqual(resp.status_code, 200, resp.content)
        return json.loads(resp.content)

    def test_all_access_role_receives_both_modules(self):
        modules = self._my_permissions(self.admin)["modules"]
        for module in NEW_MODULES:
            self.assertIn(module, modules)
            self.assertTrue(modules[module]["view"])

    def test_ordinary_role_receives_them_denied(self):
        modules = self._my_permissions(self.rep)["modules"]
        for module in NEW_MODULES:
            self.assertIn(module, modules)
            self.assertFalse(modules[module]["view"])

    def test_crm_permission_denies_without_the_grant(self):
        for module in NEW_MODULES:
            perm = crm_permission(module)()
            req = self.factory.get("/")
            req.user = self.rep
            self.assertFalse(perm.has_permission(req, _StubView("list")))

    def test_crm_permission_allows_once_granted(self):
        TeamPermission.objects.filter(
            team=self.rep_role, module="paper_review",
        ).update(can_view=True)
        self.rep_role.refresh_from_db()
        perm = crm_permission("paper_review")()
        req = self.factory.get("/")
        req.user = User.objects.get(pk=self.rep.pk)   # drop the cached role
        self.assertTrue(perm.has_permission(req, _StubView("list")))

    def test_set_permissions_accepts_the_new_modules(self):
        """
        TeamViewSet.set_permissions validates against CRM_MODULES; the Teams &
        permissions page always posts the full grid, so an unregistered key
        would 400 the whole save — not just the new rows.
        """
        view = TeamViewSet.as_view({"put": "set_permissions"})
        body = [
            {"module": m, "can_view": m in NEW_MODULES,
             "can_create": False, "can_update": False, "can_delete": False}
            for m in CRM_MODULES
        ]
        req = self.factory.put("/", body, format="json")
        force_authenticate(req, user=self.admin)
        resp = view(req, pk=self.rep_role.pk)
        resp.render()
        self.assertEqual(resp.status_code, 200, resp.content)
        for module in NEW_MODULES:
            self.assertTrue(
                TeamPermission.objects.get(
                    team=self.rep_role, module=module).can_view)


class _StubView:
    """Minimal stand-in for the `view` argument of has_permission()."""

    def __init__(self, action):
        self.action = action


class ModuleListSyncTests(TestCase):
    """
    The frontend keeps its own copy of the module list. Assert it matches the
    backend rather than trusting hand-edited files to stay aligned.

    This class used to check three files — contexts/AuthContext.jsx,
    pages/RolesPage.jsx and App.jsx — because the CRA frontend scattered the list
    across all three. The Vite tree has a SINGLE canonical list in
    lib/constants.js from which ALL_MODULES derives, and SessionContext,
    api/roles.js and the role editor all read that. So there is one place to
    check, and its absence is a failure rather than a skip: there is no longer a
    second copy to fall back on.
    """

    CONSTANTS = FRONTEND / "lib" / "constants.js"
    NAV = FRONTEND / "lib" / "nav.js"

    def _frontend_present(self):
        if not FRONTEND.exists():
            self.skipTest("frontend/src not present in this checkout")

    def test_frontend_module_list_matches_backend(self):
        """
        A key here the backend does not know produces a permission checkbox that
        can never grant anything; a backend key missing here is a module no role
        can be granted at all.
        """
        self._frontend_present()
        self.assertTrue(self.CONSTANTS.exists(), f"missing {self.CONSTANTS}")
        src = self.CONSTANTS.read_text(encoding="utf-8")
        block = re.search(r"const\s+CRM_MODULES\s*=\s*\[(.*?)\];", src, re.S)
        self.assertIsNotNone(block, "constants.CRM_MODULES not found")
        # Entries look like { k: 'bookings', l: 'Bookings' } — `k` is the key.
        keys = re.findall(r"""k:\s*["']([a-z_]+)["']""", block.group(1))
        frontend_only = sorted(set(keys) - set(CRM_MODULES))
        backend_only = sorted(set(CRM_MODULES) - set(keys))
        self.assertEqual(
            set(keys), set(CRM_MODULES),
            "frontend lib/constants.js CRM_MODULES is out of sync with "
            "accounts.models.CRM_MODULES\n"
            f"  frontend only: {frontend_only}\n"
            f"  backend only:  {backend_only}",
        )

    def test_every_nav_module_is_a_real_backend_module(self):
        """
        A sidebar entry whose `mod` the backend does not recognise is gated on a
        permission that can never be granted, so its page is unreachable for
        every role. `mod: null` marks an entry that is not module-gated.

        This one carries more weight now than when it was written. The sidebar
        HIDES what a user cannot view rather than showing it padlocked, so an
        unknown `mod` no longer presents as a locked row somebody can report — the
        entry would simply be absent for every non-admin, and nobody would think
        to look for a menu item they had never seen. See NavVisibilityTests.
        """
        self._frontend_present()
        self.assertTrue(self.NAV.exists(), f"missing {self.NAV}")
        src = self.NAV.read_text(encoding="utf-8")
        mods = set(re.findall(r"""mod:\s*["']([a-z_]+)["']""", src))
        unknown = sorted(mods - set(CRM_MODULES))
        self.assertEqual(unknown, [], f"nav.js references unknown modules: {unknown}")


class NavVisibilityTests(TestCase):
    """
    A module the user cannot view is ABSENT from the navigation, not displayed
    greyed out behind a padlock.

    THE BEHAVIOUR THIS REPLACED
    Sidebar.jsx rendered two lists per group: the items the role could open, and
    then every item it could not, each as a disabled row with a lock icon and a
    toast reading "You do not have access to X". So a Sales session listed Ticket
    Central, Paper Review, Proposal Submission, Users, Permissions, Teams
    Management, Webhooks and Google Sync — the entire product — as rows that did
    nothing. The menu answered "what exists" when the only useful question it can
    answer is "where can I go".

    Asserted against the SOURCE because this tree has no JavaScript test runner,
    which is the same approach as ModuleListSyncTests above and
    tests_event_picker_sources.py. The page-level guards are unaffected and are
    tested for real elsewhere: hiding the menu row is not the access control, it
    is what the access control looks like.
    """

    SIDEBAR = FRONTEND / "components" / "Sidebar.jsx"
    PALETTE = FRONTEND / "components" / "CommandPalette.jsx"
    NAV = FRONTEND / "lib" / "nav.js"

    def _read(self, path):
        if not FRONTEND.exists():
            self.skipTest("frontend/src not present in this checkout")
        self.assertTrue(path.exists(), f"missing {path}")
        return path.read_text(encoding="utf-8")

    def _code(self, path):
        """
        The file with its comments removed.

        A source-text assertion that reads comments as code fails on the very
        sentence explaining why the code is right: `test_the_sidebar_renders_no_
        locked_rows` was matching the phrase "No access" inside Sidebar.jsx's own
        comment about having REMOVED the locked rows. Prose is not the render.
        """
        src = self._read(path)
        src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        # (?<!:) so "https://" inside a string is not read as a comment.
        return re.sub(r"(?m)(?<!:)//.*$", "", src)

    def test_the_sidebar_renders_no_locked_rows(self):
        code = self._code(self.SIDEBAR)
        for marker in ("rail-lock", "rail-item locked", "No access"):
            self.assertNotIn(
                marker, code,
                f"Sidebar.jsx still renders {marker!r}. A module the user cannot "
                f"view belongs out of the rail, not in it behind a padlock.",
            )

    def test_the_sidebar_gates_items_on_can_view(self):
        """
        The filter itself, so removing the locked rows cannot be 'fixed' later by
        dropping the check and showing everything to everybody.

        The rule used to be spelled inline here as `!i.mod || canView(i.mod)` and
        this test pinned that text. It moved into lib/nav.js canAccess(), which
        answers the same question and two more (adminOnly, hpOnly) that an inline
        canView could not — see the comment on canAccess about the three copies
        it replaced. So the pin moved with it: the consumer must route through
        canAccess, and canAccess must still consult canView. Pinning the old
        literal asserted the shape of the code, not the property.
        """
        self.assertRegex(
            self._code(self.SIDEBAR),
            r"g\.items\.filter\(\(i\) => canAccess\(i, canView,",
            "Sidebar.jsx no longer filters group items through canAccess",
        )
        self.assertRegex(
            self._code(self.NAV),
            r"return !item\.mod \|\| canView\(item\.mod\)",
            "nav.js canAccess no longer gates an item on its module's view grant",
        )

    def test_an_empty_group_takes_its_heading_with_it(self):
        """
        Otherwise a role with no Admin rights still reads the word "Admin" over a
        gap, which is the same disclosure in smaller type.
        """
        src = self._read(self.SIDEBAR)
        self.assertRegex(
            src, r"if \(!vis\.length\) return null",
            "Sidebar.jsx renders a group heading with no visible items under it",
        )

    def test_the_command_palette_hides_the_same_pages(self):
        """
        The other NAV consumer. Hiding a row in the rail while leaving it
        one keystroke away in the palette would not be hiding it at all.
        """
        self.assertRegex(
            self._code(self.PALETTE),
            r"if \(!canAccess\(i, canView,",
            "CommandPalette.jsx no longer filters nav entries through canAccess",
        )
