"""
accounts/tests_bulk_update_reachability.py
───────────────────────────────────────────
Every resource the frontend mass-updates must actually expose mass update.

THE FAILURE THIS CATCHES
The pages ask for a resource by STRING — useBulkUpdate('paper-reviews') — and the
string is not the module key the permission checks use ('paper_review'), nor the
api module's name, nor the model. Three plausible values, one correct, and the
wrong one fails as a 404 on the schema fetch: the "Update field…" button opens
nothing and a toast says the field list could not be loaded. Nothing else in
either half of the codebase relates those strings to the routes.

So this test reads the REAL page sources, extracts what they pass, and resolves
each against the REAL URL conf. A page wired to a resource with no bulk_update
endpoint fails here rather than in the browser.

WHY IT IS IN THE BACKEND SUITE
Same reasoning as accounts/tests_wire_probe.py: the assertion spans the two halves,
and only the backend half has a test runner. It skips if the frontend tree is
absent (a backend-only checkout), and fails — never skips — if the tree is there
but the pages no longer parse, since silence would mean nothing is checked.

    python manage.py test accounts.tests_bulk_update_reachability
"""
import re
from pathlib import Path

from django.test import TestCase
from django.urls import Resolver404, resolve

BACKEND_DIR = Path(__file__).resolve().parent.parent
FRONTEND_SRC = BACKEND_DIR.parent / "frontend" / "src"
PAGES = FRONTEND_SRC / "pages"

# useBulkUpdate('delegates', …) or useBulkUpdate(bookingsApi.RESOURCE, …)
_CALL = re.compile(r"""useBulkUpdate\(\s*(?:'([^']+)'|"([^"]+)"|([A-Za-z_$][\w$]*)\.RESOURCE)""")
# import * as bookingsApi from '../api/bookings';
_NS_IMPORT = re.compile(r"""import\s+\*\s+as\s+([A-Za-z_$][\w$]*)\s+from\s+['"][^'"]*api/([\w-]+)['"]""")
_RESOURCE_CONST = re.compile(r"""export\s+const\s+RESOURCE\s*=\s*['"]([^'"]+)['"]""")


def _resolve_indirect(page_src, namespace):
    """
    `bookingsApi.RESOURCE` → 'delegates', by following the page's own import to the
    api module and reading the constant there. Returns None when it cannot be
    followed, which the test reports rather than skipping past.
    """
    for ns, module in _NS_IMPORT.findall(page_src):
        if ns != namespace:
            continue
        api_file = FRONTEND_SRC / "api" / f"{module}.js"
        if not api_file.exists():
            return None
        found = _RESOURCE_CONST.search(api_file.read_text(encoding="utf-8"))
        return found.group(1) if found else None
    return None


def collect_wired_resources():
    """[(page filename, resource string or None)] for every useBulkUpdate call."""
    out = []
    for page in sorted(PAGES.glob("*.jsx")):
        src = page.read_text(encoding="utf-8")
        for literal_sq, literal_dq, namespace in _CALL.findall(src):
            resource = literal_sq or literal_dq or _resolve_indirect(src, namespace)
            out.append((page.name, resource))
    return out


class BulkUpdateReachabilityTests(TestCase):

    def setUp(self):
        if not PAGES.exists():
            self.skipTest(f"frontend pages not present at {PAGES}")
        self.wired = collect_wired_resources()

    def test_the_pages_are_still_readable(self):
        """
        A guard against this suite quietly passing on nothing. If the pages stop
        matching the pattern — a rename, a wrapper, a different hook — the tests
        below would have no work to do and would pass, which is exactly the failure
        mode tests_wire_probe.py documents.
        """
        self.assertGreaterEqual(
            len(self.wired), 5,
            "expected the five mass-update pages (Bookings, Ticket Central, Events, "
            "Paper Review, Proposal Submission) to call useBulkUpdate; found "
            f"{[w[0] for w in self.wired]}",
        )

    def test_every_wired_resource_resolves(self):
        unresolved = [page for page, resource in self.wired if not resource]
        self.assertEqual(
            unresolved, [],
            "useBulkUpdate called with something this test could not resolve to a "
            f"resource string, in: {unresolved}",
        )

    def test_every_wired_resource_has_a_bulk_update_endpoint(self):
        problems = []
        for page, resource in self.wired:
            if not resource:
                continue
            for action in ("bulk_update_schema", "bulk_update"):
                path = f"/api/{resource}/{action}/"
                try:
                    resolve(path)
                except Resolver404:
                    problems.append(f"{page}: {path} does not exist")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_every_wired_resource_declares_fields_to_update(self):
        """
        A route that exists but declares no fields gives the user a modal with an
        empty picker — reachable, and useless.
        """
        empty = []
        for page, resource in self.wired:
            if not resource:
                continue
            view = resolve(f"/api/{resource}/bulk_update_schema/").func
            fields = getattr(view.cls, "bulk_update_fields", None)
            if not fields:
                empty.append(f"{page}: {resource} declares no bulk_update_fields")
        self.assertEqual(empty, [], "\n".join(empty))
