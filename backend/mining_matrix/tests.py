"""
mining_matrix/tests.py
───────────────────────
What is worth pinning here, and why.

1.  THE CODE JOIN. Everything on this page hangs off canonical_code mapping an
    Events code onto a Ticket Central purpose. When it is wrong the row shows
    zero, which is indistinguishable on screen from an event that is genuinely
    fully mined — a silent wrong answer, not a visible failure. So the cases the
    live catalogue actually contains are asserted by name.

2.  THE PARTITION. `upcoming` and `unlinked` must between them account for every
    unmined ticket. If they do not, work disappears from the one screen that
    exists to surface it. Verified on the live database at the time of writing:
    5,788 + 7,462 = 13,250, the exact unmined row count.

3.  THE TOTALS. In the `all` view three editions of one family each carry the
    same figures, so adding the visible column up double-counts. The footer is
    counted over distinct canonical codes instead, and that is asserted directly
    against the case that breaks it.

4.  THE GATE. A new CRM module must be denied until granted.
"""
import json
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from events.models import Event
from teams.models import Team, TeamPermission
from ticket_central.models import Ticket

from . import services
from .codes import canonical_code, known_purpose_codes
from .views import MiningMatrixViewSet

User = get_user_model()


class CanonicalCodeTests(TestCase):
    """
    The four passes of codes.canonical_code, and the shapes the live catalogue
    holds. Every literal here is a real event code read off the Events table.
    """

    KNOWN = frozenset({"AFS", "DDU", "MMU", "WSE", "BAPE", "SFIL", "WLKE", "FLNU"})

    def test_a_bare_code_is_itself(self):
        self.assertEqual(canonical_code("AFS", self.KNOWN), "AFS")

    def test_case_and_padding_are_noise(self):
        self.assertEqual(canonical_code("  afs ", self.KNOWN), "AFS")

    def test_a_stream_suffix_is_dropped(self):
        self.assertEqual(canonical_code("AFS - JS", self.KNOWN), "AFS")

    def test_a_month_year_prefix_is_dropped(self):
        """The dominant shape in the live catalogue: MAR2027_PRM-JS."""
        self.assertEqual(canonical_code("Feb2027_AFS-JS", self.KNOWN), "AFS")
        self.assertEqual(canonical_code("FEB2027_SAFE-JS", self.KNOWN | {"SAFE"}), "SAFE")

    def test_the_leading_family_wins_over_a_later_token(self):
        """'MMU/GS - JS26' is an MMU event; GS is a co-location marker."""
        self.assertEqual(canonical_code("MMU/GS - JS26", self.KNOWN), "MMU")

    def test_a_glued_year_still_resolves(self):
        self.assertEqual(canonical_code("AFS26", self.KNOWN), "AFS")

    def test_a_longer_code_is_not_truncated_to_three_letters(self):
        """
        The failure that ruled out events.codes.normalize_master_code, which
        returns the first three alphabetic characters: BAPE would become BAP,
        SFIL would become SFI, and every ticket under the real code would be lost.
        """
        for code in ("BAPE", "SFIL", "WLKE", "FLNU"):
            self.assertEqual(canonical_code(f"Jun2027_{code}-PM", self.KNOWN), code)

    def test_a_month_only_code_does_not_resolve_to_nothing(self):
        self.assertEqual(canonical_code("FEB", self.KNOWN), "FEB")

    def test_an_unknown_code_falls_back_to_its_own_first_token(self):
        """
        Honest rather than clever. The row names itself and reports zero, which
        the payload marks with matched=False so the UI can say "no tickets" rather
        than "all mined".
        """
        self.assertEqual(canonical_code("MAR2027_FLTX-DV", self.KNOWN), "FLTX")

    def test_empty_in_empty_out(self):
        self.assertEqual(canonical_code("", self.KNOWN), "")
        self.assertEqual(canonical_code(None, self.KNOWN), "")


class MatrixDataTests(TestCase):
    """
    One family with three editions, one past and two ahead, plus a purpose with
    unmined work and no event at all.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="mm_admin", password="x", role="admin",
            email="mm_admin@iq-hub.com",
        )
        today = timezone.localdate()
        cls.today = today

        # Three editions of one family. Only one is ahead and open.
        Event.objects.create(event_code="AFS", event_date=today - timedelta(days=400),
                             status=Event.Status.COMPLETED)
        cls.live = Event.objects.create(
            event_code="Feb2027_AFS-JS", event_date=today + timedelta(days=30),
            end_date=today + timedelta(days=32), status=Event.Status.UPCOMING,
        )
        Event.objects.create(event_code="AFS - JS", event_date=today + timedelta(days=90),
                             status=Event.Status.CANCELLED)
        # A second family, upcoming, with no tickets at all.
        Event.objects.create(event_code="Mar2027_DDU-PT",
                             event_date=today + timedelta(days=45),
                             status=Event.Status.UPCOMING)

        # AFS: three unmined (one without an estimate, one without a priority)
        # and one already mined, which must not be counted.
        # type_of_ticket carries the DISPLAY string the live column holds, both
        # spacings, so extract_type_code is exercised rather than assumed.
        Ticket.objects.create(purpose="AFS", priority="AS", estimate=100,
                              type_of_ticket="White - WH")
        Ticket.objects.create(purpose="AFS", priority="DD", estimate=50,
                              type_of_ticket="Comp.-CX")
        Ticket.objects.create(purpose="AFS", priority="", estimate=None,
                              type_of_ticket="")
        Ticket.objects.create(purpose="AFS", priority="AS", estimate=999,
                              type_of_ticket="White - WH", actual_number=7)
        # Mined but yielded nothing. Finished work, NOT unmined.
        Ticket.objects.create(purpose="AFS", priority="AS", estimate=40,
                              type_of_ticket="White - WH", actual_number=0)
        # A purpose the catalogue has never heard of.
        Ticket.objects.create(purpose="ZZQ", priority="SPEX", estimate=70,
                              type_of_ticket="LinkedIn - LX")

    def _payload(self, view, include_zero=False):
        return services.build_payload(self.user, view=view, include_zero=include_zero)

    def test_known_purposes_are_read_off_the_tickets(self):
        self.assertEqual(known_purpose_codes(), frozenset({"AFS", "ZZQ"}))

    def test_upcoming_shows_only_the_open_future_edition(self):
        rows = self._payload(services.VIEW_UPCOMING)["rows"]
        self.assertEqual([r["event_code"] for r in rows], ["Feb2027_AFS-JS"])

    def test_a_cancelled_future_edition_is_not_upcoming(self):
        codes = [r["event_code"] for r in self._payload(services.VIEW_ALL)["rows"]]
        self.assertIn("AFS - JS", codes, "the all view should still carry it")
        self.assertNotIn(
            "AFS - JS",
            [r["event_code"] for r in self._payload(services.VIEW_UPCOMING)["rows"]],
        )

    def test_col_c_counts_rows_and_col_d_sums_estimate(self):
        row = self._payload(services.VIEW_UPCOMING)["rows"][0]
        # Three unmined rows: 100 + 50 + (no estimate).
        self.assertEqual(row["unmined_links"], 3)
        self.assertEqual(row["unmined_data"], 150)

    def test_a_mined_zero_is_not_unmined(self):
        """actual_number = 0 is a link that was worked and yielded nothing."""
        row = self._payload(services.VIEW_UPCOMING)["rows"][0]
        self.assertEqual(row["unmined_links"], 3, "actual_number=0 was counted")

    def test_every_split_adds_up_to_col_d_on_its_own(self):
        """
        Priority and ticket type are two CUTS of the same money, not two halves of
        it, so each block sums to Col D independently. A block that summed to half
        would look like a rounding bug and be reconciled by hand for ever.
        """
        row = self._payload(services.VIEW_UPCOMING)["rows"][0]
        for dim in services.SPLIT_KEYS:
            self.assertEqual(sum(row["split_data"][dim].values()), row["unmined_data"], dim)
            self.assertEqual(sum(row["split_links"][dim].values()), row["unmined_links"], dim)

    def test_the_priority_split_is_the_priority_column(self):
        row = self._payload(services.VIEW_UPCOMING)["rows"][0]
        self.assertEqual(row["split_data"][services.SPLIT_PRIORITY],
                         {"AS": 100, "DD": 50, "": 0})

    def test_the_type_split_reduces_the_display_string_to_its_code(self):
        """
        The live column holds 'White - WH', 'Comp.-CX', 'ZID'. Three columns headed
        with the long form would be most of this table's width spent on words the
        reader already knows, so extract_type_code reduces them — the same function
        that builds ticket numbers out of the field.
        """
        row = self._payload(services.VIEW_UPCOMING)["rows"][0]
        self.assertEqual(row["split_data"][services.SPLIT_TYPE],
                         {"WH": 100, "CX": 50, "": 0})

    def test_ticket_type_leads_and_priority_follows(self):
        """
        SPLITS order IS the page order — the frontend draws the blocks in the
        order the payload lists them and hardcodes neither. Ticket type first: it
        is the coarser cut, saying what KIND of work is waiting and so who can
        pick it up, with priority ranking within that.
        """
        payload = self._payload(services.VIEW_UPCOMING)
        self.assertEqual([d["key"] for d in payload["splits"]],
                         [services.SPLIT_TYPE, services.SPLIT_PRIORITY])

    def test_the_type_split_reads_the_mr_column_not_the_dmd_one(self):
        """
        `ticket_type` is the Data Mining field and is empty on 93% of unmined rows
        by construction — DMD fills it in as it works a ticket. Reading it here
        would produce a block that is almost entirely blank on the very rows this
        page exists to show.
        """
        self.assertEqual(
            next(s["field"] for s in services.SPLITS if s["key"] == services.SPLIT_TYPE),
            "type_of_ticket",
        )

    def test_a_blank_value_gets_its_own_column_in_every_split(self):
        cols = self._payload(services.VIEW_UPCOMING)["split_columns"]
        for spec in services.SPLITS:
            keys = [c["key"] for c in cols[spec["key"]]]
            self.assertIn(services.BLANK_VALUE, keys, spec["key"])
            self.assertEqual(keys[-1], services.BLANK_VALUE, "blank belongs last")
            label = next(c["label"] for c in cols[spec["key"]]
                         if c["key"] == services.BLANK_VALUE)
            self.assertEqual(label, spec["blank_label"])

    def test_a_value_with_no_work_gets_no_column(self):
        cols = self._payload(services.VIEW_UPCOMING)["split_columns"]
        self.assertNotIn("ASSOC", [c["key"] for c in cols[services.SPLIT_PRIORITY]])
        self.assertNotIn("GX", [c["key"] for c in cols[services.SPLIT_TYPE]])

    def test_the_totals_carry_every_split(self):
        totals = self._payload(services.VIEW_UPCOMING)["totals"]
        for dim in services.SPLIT_KEYS:
            self.assertEqual(sum(totals["split_data"][dim].values()),
                             totals["unmined_data"], dim)

    def test_days_to_go_counts_from_today(self):
        row = self._payload(services.VIEW_UPCOMING)["rows"][0]
        self.assertEqual(row["days_to_go"], 30)
        self.assertEqual(row["start_date"], self.live.event_date)
        self.assertEqual(row["end_date"], self.live.end_date)

    def test_an_event_with_nothing_outstanding_is_hidden_by_default(self):
        codes = [r["event_code"] for r in self._payload(services.VIEW_UPCOMING)["rows"]]
        self.assertNotIn("Mar2027_DDU-PT", codes)

    def test_include_zero_brings_it_back_and_marks_it_unmatched(self):
        rows = self._payload(services.VIEW_UPCOMING, include_zero=True)["rows"]
        ddu = next(r for r in rows if r["event_code"] == "Mar2027_DDU-PT")
        self.assertEqual(ddu["unmined_links"], 0)
        self.assertFalse(ddu["matched"],
                         "zero with no tickets must not read as fully mined")

    def test_unlinked_carries_the_work_upcoming_cannot_show(self):
        rows = self._payload(services.VIEW_UNLINKED)["rows"]
        self.assertEqual([r["canonical_code"] for r in rows], ["ZZQ"])
        self.assertEqual(rows[0]["unmined_links"], 1)
        self.assertFalse(rows[0]["linked"], "no event exists for ZZQ")

    def test_the_two_views_account_for_every_unmined_ticket(self):
        """
        The property that matters most: nothing falls between the views. Asserted
        against the raw row count rather than against a second derived figure, so
        a bug in the aggregate cannot make both sides wrong in the same way.
        """
        total = Ticket.objects.filter(actual_number__isnull=True).count()
        upcoming = self._payload(services.VIEW_UPCOMING)["totals"]["unmined_links"]
        unlinked = self._payload(services.VIEW_UNLINKED)["totals"]["unmined_links"]
        self.assertEqual(upcoming + unlinked, total)

    def test_the_all_view_totals_do_not_double_count_editions(self):
        """
        Three AFS editions each show 3 links, because Ticket Central has one AFS
        purpose and not three. Adding the column up gives 9; the footer must say 3.
        """
        payload = self._payload(services.VIEW_ALL)
        afs_rows = [r for r in payload["rows"] if r["canonical_code"] == "AFS"]
        self.assertEqual(len(afs_rows), 3, "all three editions should be listed")
        self.assertEqual(sum(r["unmined_links"] for r in afs_rows), 9)
        self.assertEqual(payload["totals"]["unmined_links"], 3)
        self.assertEqual(payload["totals"]["unmined_data"], 150)
        self.assertEqual(payload["totals"]["codes"], 1)

    def test_view_counts_agree_with_the_rows_each_view_renders(self):
        counts = self._payload(services.VIEW_UPCOMING)["view_counts"]
        for view in services.VIEWS:
            self.assertEqual(
                counts[view], len(self._payload(view)["rows"]),
                f"the {view} tab count disagrees with its own table",
            )

    def test_an_unknown_view_falls_back_to_upcoming(self):
        self.assertEqual(
            services.build_payload(self.user, view="nonsense")["view"],
            services.VIEW_UPCOMING,
        )

    def test_a_ticket_with_no_purpose_is_reported_rather_than_dropped(self):
        Ticket.objects.create(purpose="", priority="AS", estimate=25)
        payload = self._payload(services.VIEW_UPCOMING)
        self.assertEqual(payload["no_purpose"], {"links": 1, "estimate": 25})


class ScopingTests(TestCase):
    """
    The figures must be drawn from the same tickets the click-through can show.
    A row says "5 unmined links" and navigates to Ticket Central filtered on that
    purpose; if the matrix aggregated unscoped, the destination would show fewer
    rows than the number that sent the user there, with nothing explaining it.
    """

    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        Event.objects.create(event_code="Feb2027_AFS-JS",
                             event_date=today + timedelta(days=10),
                             status=Event.Status.UPCOMING)
        cls.mine = User.objects.create_user(
            username="mm_mr", password="x", role="market_research",
            email="mm_mr@iq-hub.com",
        )
        cls.other = User.objects.create_user(
            username="mm_other", password="x", role="market_research",
            email="mm_other@iq-hub.com",
        )
        Ticket.objects.create(purpose="AFS", priority="AS", estimate=100,
                              created_by=cls.mine)
        Ticket.objects.create(purpose="AFS", priority="AS", estimate=900,
                              created_by=cls.other)

    def test_a_scoped_role_sees_only_its_own_tickets(self):
        row = services.build_payload(self.mine)["rows"][0]
        self.assertEqual(row["unmined_links"], 1)
        self.assertEqual(row["unmined_data"], 100)

    def test_an_unscoped_role_sees_everything(self):
        admin = User.objects.create_user(
            username="mm_admin2", password="x", role="admin",
            email="mm_admin2@iq-hub.com",
        )
        row = services.build_payload(admin)["rows"][0]
        self.assertEqual(row["unmined_links"], 2)
        self.assertEqual(row["unmined_data"], 1000)


class ClickThroughTests(TestCase):
    """
    THE CONTRACT BETWEEN THE TWO SCREENS.

    A matrix row says "3 unmined links" and links to Ticket Central. If the table
    it lands on shows a different number, the user has no way to tell which one is
    lying — both look authoritative and neither explains itself. The two are
    computed by completely different code (a GROUP BY here, a filter_spec there),
    so nothing but a test holds them together.

    The criteria below are the ones frontend/src/pages/TicketCentralPage.jsx
    builds from the link's query string, written out literally. If that function
    changes shape, this fails.
    """

    @classmethod
    def setUpTestData(cls):
        # An all-access TEAM, not merely role="admin": access is resolved from
        # the team grid (User.effective_permissions), so a role alone opens
        # nothing — which is what makes this a real end-to-end check of the link.
        cls.user = User.objects.create_user(
            username="ct_admin", password="x", role="admin",
            email="ct_admin@iq-hub.com",
            team=Team.objects.create(name="ct_all", is_all_access=True),
        )
        Event.objects.create(
            event_code="Feb2027_AFS-JS",
            event_date=timezone.localdate() + timedelta(days=20),
            status=Event.Status.UPCOMING,
        )
        for estimate in (10, 20, 30):
            Ticket.objects.create(purpose="AFS", priority="AS", estimate=estimate)
        Ticket.objects.create(purpose="AFS", priority="AS", estimate=99, actual_number=4)
        Ticket.objects.create(purpose="AFS", priority="AS", estimate=99, actual_number=0)
        Ticket.objects.create(purpose="DDU", priority="AS", estimate=99)

    def _ticket_count(self, criteria):
        from ticket_central.views import TicketViewSet

        view = TicketViewSet.as_view({"get": "list"})
        request = APIRequestFactory().get(
            "/api/tickets/",
            {"filter_spec": json.dumps({"match": "all", "criteria": criteria}),
             "page_size": 1},
        )
        force_authenticate(request, user=self.user)
        response = view(request)
        response.render()
        self.assertEqual(response.status_code, 200, response.content)
        return json.loads(response.content)["count"]

    def test_the_link_lands_on_exactly_the_rows_the_row_counted(self):
        row = services.build_payload(self.user)["rows"][0]
        self.assertEqual(row["unmined_links"], 3)
        self.assertEqual(
            self._ticket_count([
                {"field": "purpose", "op": "is", "value": row["canonical_code"]},
                {"field": "actual_number", "op": "is_empty"},
            ]),
            row["unmined_links"],
        )

    def test_the_link_must_carry_the_canonical_code_not_the_event_code(self):
        """
        Why api/miningMatrix.js links on `canonical_code`. The displayed code
        carries a month prefix and a stream suffix that Ticket Central has never
        heard of, so filtering on it finds nothing — and an empty table is not
        obviously a bug to whoever clicked.
        """
        row = services.build_payload(self.user)["rows"][0]
        self.assertEqual(row["event_code"], "Feb2027_AFS-JS")
        self.assertEqual(
            self._ticket_count([
                {"field": "purpose", "op": "is", "value": row["event_code"]},
                {"field": "actual_number", "op": "is_empty"},
            ]),
            0,
            "the displayed event code should NOT match any ticket",
        )

    def test_both_criteria_are_required_to_reproduce_the_figure(self):
        """Each half of the filter is load-bearing; neither alone is the answer."""
        self.assertEqual(
            self._ticket_count([{"field": "actual_number", "op": "is_empty"}]), 4,
            "unmined alone spans every purpose",
        )
        self.assertEqual(
            self._ticket_count([{"field": "purpose", "op": "is", "value": "AFS"}]), 5,
            "purpose alone includes the already-mined tickets",
        )


class WireSourceTests(TestCase):
    """
    The link is BUILT in api/miningMatrix.js and READ in TicketCentralPage.jsx.
    Two files, one contract, and no JavaScript test runner in this tree — so it is
    asserted against the source, the same approach accounts/tests_pipeline_modules
    and tests_event_picker_sources take.

    A rename on either side is silent otherwise: the link still navigates, the
    table still renders, and the filter simply does not apply.
    """

    FRONTEND = Path(settings.BASE_DIR).parent / "frontend" / "src"

    def _read(self, *parts):
        if not self.FRONTEND.exists():
            self.skipTest("frontend/src not present in this checkout")
        path = self.FRONTEND.joinpath(*parts)
        self.assertTrue(path.exists(), f"missing {path}")
        return path.read_text(encoding="utf-8")

    def test_the_link_is_built_from_the_canonical_code(self):
        src = self._read("api", "miningMatrix.js")
        self.assertIn("row.canonical_code", src)
        self.assertIn("purpose=", src)
        self.assertIn("unmined=1", src)

    def test_ticket_central_reads_both_params_the_link_sets(self):
        src = self._read("pages", "TicketCentralPage.jsx")
        self.assertIn("params.get('purpose')", src)
        self.assertIn("params.get('unmined')", src)

    def test_ticket_central_applies_them_as_the_same_two_criteria(self):
        """
        The field names and operators, not merely the params. `actual_number` +
        `is_empty` is the exact predicate services.unmined_by_purpose aggregates
        on; anything else here would count a different set of tickets.
        """
        src = self._read("pages", "TicketCentralPage.jsx")
        self.assertIn("field: 'purpose', op: 'is'", src)
        self.assertIn("field: 'actual_number', op: 'is_empty'", src)

    def test_every_enum_value_has_a_column_band_colour(self):
        """
        Col E onwards is two adjacent blocks of numbers, coloured so the boundary
        between them is visible without reading a header. A value with no class
        renders unbanded — which is the deliberate fallback for the free-text
        values these CharFields permit, but is a GAP for a value the model itself
        declares. Those are known in advance, so they are asserted rather than
        discovered when one first appears in the data.

        Class names are the value lower-cased and stripped to alphanumerics; see
        bandClass() in MiningMatrixPage.jsx, which must keep agreeing with this.
        """
        import re

        src = self._read("styles", "components.css")
        defined = set(re.findall(r"^\.bnd-([a-z0-9]+)\{", src, re.M))
        for value in list(Ticket.TypeOfTicket.values) + list(Ticket.Priority.values):
            slug = re.sub(r"[^a-z0-9]", "", value.lower())
            self.assertIn(slug, defined,
                          f"{value} has no .bnd-{slug} band colour")

    def test_the_band_palette_defines_all_three_properties_per_value(self):
        """
        A class setting only some of --bd / --bd-bg / --bd-tx would inherit the
        rest from the neutral defaults on `.bnd`, giving a column the wrong
        accent against the right tint — visible, but only as "that looks a bit
        off", which is the kind of defect nobody files.
        """
        import re

        src = self._read("styles", "components.css")
        for cls, body in re.findall(r"^\.bnd-([a-z0-9]+)\{([^}]*)\}", src, re.M):
            for prop in ("--bd:", "--bd-bg:", "--bd-tx:"):
                self.assertIn(prop, body, f".bnd-{cls} is missing {prop}")

    def test_the_band_palette_has_a_dark_theme_for_every_token(self):
        """
        The colours are theme tokens rather than inline hex precisely so they can
        follow the theme. A token defined only in :root would be a light-theme
        colour painted on a dark surface — the one thing on the page that ignores
        the switch.

        SLICED FROM THE [band_palette] MARKER, not from the first ':root{' in the
        file. Written the obvious way this test passed while asserting nothing:
        base.css opens with its own :root and dark blocks long before this
        palette, so the "light" slice was the ORIGINAL theme block, which holds no
        --bd-* token at all, and an empty set minus anything is empty. Hence the
        non-empty guard below — a parity test that can pass with nothing on either
        side is worse than no test, because it reports coverage it does not have.
        """
        import re

        src = self._read("styles", "base.css")
        block = src[src.index("[band_palette]"):]
        light_at = block.index(":root{")
        dark_at = block.index("html[data-theme=dark]{")
        light = block[light_at:block.index("}", light_at)]
        dark = block[dark_at:block.index("}", dark_at)]

        names = lambda blob: set(re.findall(r"(--bd-[a-z0-9-]+):", blob))
        light_names, dark_names = names(light), names(dark)

        self.assertTrue(light_names, "no band tokens found — the slice is wrong")
        self.assertEqual(
            sorted(light_names - dark_names), [],
            "these band tokens have no dark-theme value",
        )
        self.assertEqual(
            sorted(dark_names - light_names), [],
            "these band tokens exist only in dark theme",
        )

    def test_the_matrix_page_is_gated_on_its_own_module(self):
        src = self._read("pages", "MiningMatrixPage.jsx")
        self.assertIn("canView('mining_matrix')", src)


class ModuleGateTests(TestCase):
    """A new CRM module grants nothing until it is granted."""

    @classmethod
    def setUpTestData(cls):
        cls.team = Team.objects.create(name="mm_rep_team", is_all_access=False)
        TeamPermission.objects.create(
            team=cls.team, module="ticket_central",
            can_view=True, can_create=True, can_update=True, can_delete=True,
        )
        cls.rep = User.objects.create_user(
            username="mm_rep", password="x", role="market_research",
            email="mm_rep@iq-hub.com", team=cls.team,
        )

    def _get(self, user):
        view = MiningMatrixViewSet.as_view({"get": "list"})
        request = APIRequestFactory().get("/api/mining-matrix/")
        force_authenticate(request, user=user)
        response = view(request)
        response.render()
        return response

    def test_ticket_central_alone_does_not_open_the_matrix(self):
        """
        The reason it is its own module: a full grant on the ticket queue must not
        carry the planning view with it.
        """
        self.assertEqual(self._get(self.rep).status_code, 403)

    def test_the_grant_opens_it(self):
        TeamPermission.objects.create(
            team=self.team, module="mining_matrix", can_view=True,
        )
        rep = User.objects.get(pk=self.rep.pk)      # drop the cached matrix
        self.assertEqual(self._get(rep).status_code, 200)

    def test_an_anonymous_request_is_refused(self):
        view = MiningMatrixViewSet.as_view({"get": "list"})
        response = view(APIRequestFactory().get("/api/mining-matrix/"))
        response.render()
        self.assertIn(response.status_code, (401, 403))
