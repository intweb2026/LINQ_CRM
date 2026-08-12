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
from accounts.models import CRM_MODULES, CustomRole, RolePermission
from accounts.views import CustomRoleViewSet, UserViewSet

User = get_user_model()

NEW_MODULES = ["paper_review", "proposal_submission"]

FRONTEND = Path(settings.BASE_DIR).parent / "frontend" / "src"


def _js_string_list(path, variable):
    """
    Pull the string literals out of a `const NAME = [...]` array in a JS file.

    Deliberately crude — it only has to cope with the flat lists of quoted
    module keys these three files actually contain. Returns None when the file
    or the variable is absent so the caller can skip rather than fail on a
    backend-only checkout.
    """
    if not path.exists():
        return None
    src = path.read_text(encoding="utf-8")
    m = re.search(r"const\s+" + re.escape(variable) + r"\s*=\s*\[(.*?)\]", src, re.S)
    if not m:
        return None
    return re.findall(r"[\"']([a-z_]+)[\"']", m.group(1))


class PipelineModuleRegistrationTests(TestCase):
    def test_both_modules_are_registered(self):
        for module in NEW_MODULES:
            self.assertIn(module, CRM_MODULES)

    def test_crm_modules_has_no_duplicates(self):
        self.assertEqual(len(CRM_MODULES), len(set(CRM_MODULES)))


class PipelineModuleMigrationTests(TestCase):
    """
    Exercises migration 0020 against the roles migration 0018 seeded. The test
    database is built by running the full migration chain, so these rows exist
    only if the backfill actually ran.
    """

    def test_every_seeded_role_has_rows_for_both_modules(self):
        roles = CustomRole.objects.all()
        self.assertGreater(roles.count(), 0, "0018 should have seeded system roles")
        for role in roles:
            for module in NEW_MODULES:
                self.assertTrue(
                    RolePermission.objects.filter(
                        custom_role=role, module=module).exists(),
                    f"{role.name} is missing a {module} row",
                )

    def test_backfilled_rows_grant_nothing(self):
        for perm in RolePermission.objects.filter(module__in=NEW_MODULES):
            self.assertFalse(perm.can_view,   f"{perm.custom_role.name}/{perm.module}")
            self.assertFalse(perm.can_create, f"{perm.custom_role.name}/{perm.module}")
            self.assertFalse(perm.can_update, f"{perm.custom_role.name}/{perm.module}")
            self.assertFalse(perm.can_delete, f"{perm.custom_role.name}/{perm.module}")


class PipelineModulePermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.open_role = CustomRole.objects.create(
            name="pm_admin", display_label="PM Admin", is_all_access=True)
        cls.admin = User.objects.create_user(
            username="pm_admin_u", password="x", role="admin", email="pma@iq-hub.com")
        cls.admin.custom_role = cls.open_role
        cls.admin.save()

        cls.rep_role = CustomRole.objects.create(
            name="pm_rep", display_label="PM Rep", is_all_access=False)
        for module in CRM_MODULES:
            RolePermission.objects.create(
                custom_role=cls.rep_role, module=module,
                can_view=(module == "bookings"))
        cls.rep = User.objects.create_user(
            username="pm_rep_u", password="x", role="sales", email="pmr@iq-hub.com")
        cls.rep.custom_role = cls.rep_role
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
        RolePermission.objects.filter(
            custom_role=self.rep_role, module="paper_review",
        ).update(can_view=True)
        self.rep_role.refresh_from_db()
        perm = crm_permission("paper_review")()
        req = self.factory.get("/")
        req.user = User.objects.get(pk=self.rep.pk)   # drop the cached role
        self.assertTrue(perm.has_permission(req, _StubView("list")))

    def test_set_permissions_accepts_the_new_modules(self):
        """
        CustomRoleViewSet.set_permissions validates against CRM_MODULES; the
        RolesPage always posts the full grid, so an unregistered key would 400
        the whole save — not just the new rows.
        """
        view = CustomRoleViewSet.as_view({"put": "set_permissions"})
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
                RolePermission.objects.get(
                    custom_role=self.rep_role, module=module).can_view)


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
        """
        self._frontend_present()
        self.assertTrue(self.NAV.exists(), f"missing {self.NAV}")
        src = self.NAV.read_text(encoding="utf-8")
        mods = set(re.findall(r"""mod:\s*["']([a-z_]+)["']""", src))
        unknown = sorted(mods - set(CRM_MODULES))
        self.assertEqual(unknown, [], f"nav.js references unknown modules: {unknown}")
