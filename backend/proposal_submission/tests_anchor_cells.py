"""
proposal_submission/tests_anchor_cells.py
──────────────────────────────────────────
Cells that arrive as HTML anchor markup.

THE BUG THIS PINS
Zoho writes several export columns as HTML, so a LinkedIn cell reaches the
importer as `<a href="https://www.linkedin.com/in/eli" target="_blank">Eli</a>`
rather than as the address alone. The importer stored that string verbatim, and
a URLField holding markup is not a link on any count: the grid rendered the whole
tag as the href, so the click went nowhere; the tags ate the 500-character
column, so a short address could report as over-length; and a CSV export wrote
the markup back out for the next import to inherit.

The address inside the tag is what the column was always meant to hold, so that
is what gets stored, and the grid then renders it as an ordinary link that opens
in its own tab. Text columns keep their words but shed the tags.
"""
from django.test import SimpleTestCase

from accounts.import_common import (
    CREATE, ERROR, absolute_url, as_url, plain_text_cell, unwrap_anchor,
)
from proposal_submission.importer import classify_rows, map_headers
from proposal_submission.tests import _Base

SPEAKER_ANCHOR = (
    '<a href="https://www.linkedin.com/in/eli-jasso" target="_blank" '
    'rel="noopener">Eli Jasso</a>'
)


class UnwrapAnchorTests(SimpleTestCase):
    def test_a_plain_value_is_reported_as_not_markup_at_all(self):
        href, text = unwrap_anchor("https://www.linkedin.com/in/eli-jasso")
        self.assertIsNone(href, "None means 'never was a link tag'")
        self.assertEqual(text, "https://www.linkedin.com/in/eli-jasso")

    def test_the_href_and_the_words_come_back_separately(self):
        href, text = unwrap_anchor(SPEAKER_ANCHOR)
        self.assertEqual(href, "https://www.linkedin.com/in/eli-jasso")
        self.assertEqual(text, "Eli Jasso")

    def test_every_quoting_style_a_spreadsheet_writes(self):
        cases = [
            '<a href="https://x.com/a">a</a>',
            "<a href='https://x.com/a'>a</a>",
            "<a href=https://x.com/a>a</a>",
            '<A HREF = "https://x.com/a" >a</A>',
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                self.assertEqual(unwrap_anchor(raw)[0], "https://x.com/a")

    def test_entities_in_the_address_are_decoded(self):
        href, _ = unwrap_anchor(
            '<a href="https://x.com/p?a=1&amp;b=2">deck</a>')
        self.assertEqual(href, "https://x.com/p?a=1&b=2")

    def test_an_anchor_with_no_href_is_empty_string_not_none(self):
        """The distinction the row-error rule turns on. See as_url."""
        href, text = unwrap_anchor('<a name="bookmark">Eli Jasso</a>')
        self.assertEqual(href, "")
        self.assertEqual(text, "Eli Jasso")


class AbsoluteUrlTests(SimpleTestCase):
    def test_a_scheme_less_address_gains_https(self):
        # Without this the browser treats it as a path and reloads the CRM.
        self.assertEqual(absolute_url("linkedin.com/in/eli"),
                         "https://linkedin.com/in/eli")
        self.assertEqual(absolute_url("www.x.co.uk"), "https://www.x.co.uk")

    def test_prose_is_not_a_url_and_never_becomes_one(self):
        for raw in ("N/A", "not on LinkedIn", "ask the speaker", "—", ""):
            with self.subTest(raw=raw):
                self.assertIsNone(absolute_url(raw))

    def test_only_http_and_https_survive(self):
        self.assertEqual(absolute_url("http://x.com"), "http://x.com")
        for raw in ("javascript:alert(1)", "data:text/html,<script>",
                    "mailto:eli@x.com", "file:///etc/passwd"):
            with self.subTest(raw=raw):
                self.assertIsNone(absolute_url(raw))


class AsUrlTests(SimpleTestCase):
    def test_markup_collapses_to_the_address_inside_it(self):
        url, error = as_url(SPEAKER_ANCHOR)
        self.assertIsNone(error)
        self.assertEqual(url, "https://www.linkedin.com/in/eli-jasso")

    def test_a_javascript_href_is_refused_rather_than_stored(self):
        """
        The reason this errors instead of storing the text: the frontend renders
        a stored URL column straight into an href, so a javascript: address
        smuggled in through a spreadsheet would be one click from running in the
        CRM's own origin.
        """
        url, error = as_url('<a href="javascript:alert(1)">profile</a>')
        self.assertEqual(url, "")
        self.assertIn("not an http or https address", error)

    def test_an_anchor_carrying_no_address_errors(self):
        url, error = as_url("<a></a>")
        self.assertEqual(url, "")
        self.assertIn("empty link tag", error)

    def test_an_anchor_without_an_href_falls_back_to_its_own_text(self):
        url, error = as_url("<a>linkedin.com/in/eli</a>")
        self.assertIsNone(error)
        self.assertEqual(url, "https://linkedin.com/in/eli")

    def test_prose_typed_into_a_link_column_is_kept_not_errored(self):
        """
        "N/A" is a person answering the question. Failing the row over it would
        discard twenty good columns to police one, so it is stored as typed and
        rendered as plain text.
        """
        for raw in ("N/A", "not on LinkedIn"):
            with self.subTest(raw=raw):
                url, error = as_url(raw)
                self.assertIsNone(error)
                self.assertEqual(url, raw)

    def test_blank_stays_blank(self):
        self.assertEqual(as_url(""), ("", None))
        self.assertEqual(as_url(None), ("", None))


class PlainTextCellTests(SimpleTestCase):
    def test_a_text_column_keeps_its_words_and_loses_its_tags(self):
        self.assertEqual(
            plain_text_cell('see <a href="https://x.com/d">the <b>deck</b></a>'),
            "see the deck, https://x.com/d")

    def test_markup_with_no_anchor_in_it_is_left_alone_on_purpose(self):
        """
        The narrow rule, pinned so nobody widens it by accident. Stripping every
        tag from every text column would also eat the angle brackets people type,
        and an anchor is the only shape whose contents the frontend navigates to.
        """
        self.assertEqual(plain_text_cell("<p>slot confirmed</p>"),
                         "<p>slot confirmed</p>")
        self.assertEqual(plain_text_cell("<not stated>"), "<not stated>")

    def test_an_address_the_words_do_not_already_carry_is_kept(self):
        self.assertEqual(
            plain_text_cell('<a href="https://x.com/deck">the deck</a>'),
            "the deck, https://x.com/deck")

    def test_an_address_the_words_already_carry_is_not_repeated(self):
        self.assertEqual(
            plain_text_cell('<a href="https://x.com/deck">https://x.com/deck</a>'),
            "https://x.com/deck")

    def test_a_mailto_anchor_reads_as_the_address_once(self):
        """How an email column arrives from this export."""
        self.assertEqual(
            plain_text_cell('<a href="mailto:eli@x.co">eli@x.co</a>'),
            "eli@x.co")

    def test_untagged_text_is_untouched(self):
        self.assertEqual(plain_text_cell("weak on region"), "weak on region")


class ClassifyRowsAnchorTests(_Base):
    """End to end through the real plan builder, on the real header labels."""

    HEADERS = [
        "Event Code", "Speaker Name", "Email Address", "LinkedIn (Speaker)",
        "LinkedIn (Company)", "SpEx Remarks",
    ]

    def plan_for(self, **cells):
        row = {
            "Event Code": "AFS - JS",
            "Speaker Name": "Eli Jasso",
            "Email Address": "eli.jasso@cicadalogistics.co",
        }
        row.update(cells)
        mapping, _ = map_headers(self.HEADERS)
        return classify_rows([row], mapping, self.user, set())[0]

    def test_an_anchor_wrapped_linkedin_cell_imports_as_the_address(self):
        entry = self.plan_for(**{"LinkedIn (Speaker)": SPEAKER_ANCHOR})
        self.assertEqual(entry["classification"], CREATE, entry["errors"])
        self.assertEqual(entry["_payload"]["linkedin_speaker"],
                         "https://www.linkedin.com/in/eli-jasso")

    def test_no_anchor_markup_reaches_the_stored_payload(self):
        entry = self.plan_for(**{
            "LinkedIn (Speaker)": SPEAKER_ANCHOR,
            "LinkedIn (Company)": '<a href="https://x.com/co">Cicada</a>',
            "SpEx Remarks": 'strong, see <a href="https://x.com/d">the deck</a>',
        })
        for field, value in entry["_payload"].items():
            if isinstance(value, str):
                self.assertNotIn("<a", value, field)
        self.assertEqual(entry["_payload"]["spex_remarks"],
                         "strong, see the deck, https://x.com/d")

    def test_a_mailto_anchor_in_the_email_column_still_satisfies_required(self):
        """
        Before the unwrapping this row read as markup rather than as an address,
        so the required-field check passed on tag soup and a nonsense email was
        stored. Now the column holds the address and duplicate detection, which
        keys on lower(email), sees the same value the form path would.
        """
        entry = self.plan_for(**{
            "Email Address": '<a href="mailto:eli.jasso@cicadalogistics.co">'
                             'eli.jasso@cicadalogistics.co</a>',
        })
        self.assertEqual(entry["classification"], CREATE, entry["errors"])
        self.assertEqual(entry["_payload"]["email"],
                         "eli.jasso@cicadalogistics.co")

    def test_an_empty_anchor_in_a_required_column_is_a_row_error(self):
        entry = self.plan_for(**{"Speaker Name": "<a></a>"})
        self.assertEqual(entry["classification"], ERROR)
        self.assertEqual([e["field"] for e in entry["errors"]], ["Speaker Name"])

    def test_a_dangerous_href_names_its_column_rather_than_being_stored(self):
        entry = self.plan_for(**{
            "LinkedIn (Company)": '<a href="javascript:alert(1)">Cicada</a>'})
        self.assertEqual(entry["classification"], ERROR)
        problems = {e["field"]: e["problem"] for e in entry["errors"]}
        self.assertIn("LinkedIn (Company)", problems)
        self.assertIn("http or https", problems["LinkedIn (Company)"])
        self.assertNotIn("_payload", entry)

    def test_length_is_judged_on_the_address_not_on_the_tags(self):
        """
        The tags around a 60-character address run to well over 500 once Zoho's
        target and rel attributes are counted. Measured before unwrapping, this
        row failed column_errors for a URL that fits the column comfortably.
        """
        address = "https://www.linkedin.com/in/" + "e" * 40
        entry = self.plan_for(**{
            "LinkedIn (Speaker)": f'<a href="{address}" target="_blank" '
                                  f'rel="noopener noreferrer" '
                                  f'title="{"t" * 420}">Eli</a>',
        })
        self.assertEqual(entry["classification"], CREATE, entry["errors"])
        self.assertEqual(entry["_payload"]["linkedin_speaker"], address)

    def test_a_plain_url_row_is_unchanged_by_any_of_this(self):
        entry = self.plan_for(**{
            "LinkedIn (Speaker)": "https://www.linkedin.com/in/eli-jasso",
            "SpEx Remarks": "strong on region",
        })
        self.assertEqual(entry["classification"], CREATE, entry["errors"])
        self.assertEqual(entry["_payload"]["linkedin_speaker"],
                         "https://www.linkedin.com/in/eli-jasso")
        self.assertEqual(entry["_payload"]["spex_remarks"], "strong on region")


class CommitWritesTheAddressTests(_Base):
    """The value actually reaching the database, through the real endpoint."""

    def test_import_commit_stores_a_navigable_url(self):
        from proposal_submission.models import ProposalSubmission

        self.client.force_authenticate(self.user)
        rows = [{
            "Event Code": "AFS - JS",
            "Speaker Name": "Eli Jasso",
            "Email Address": "eli.jasso@cicadalogistics.co",
            "LinkedIn (Speaker)": SPEAKER_ANCHOR,
        }]
        preview = self.client.post(
            self.LIST + "import/preview/", {"rows": rows}, format="json")
        self.assertEqual(preview.status_code, 200, preview.data)

        commit = self.client.post(self.LIST + "import/commit/", {
            "rows": rows,
            "plan_hash": preview.data["plan_hash"],
            "import_batch_id": preview.data["import_batch_id"],
        }, format="json")
        self.assertEqual(commit.status_code, 201, commit.data)

        stored = ProposalSubmission.objects.get(
            email="eli.jasso@cicadalogistics.co")
        self.assertEqual(stored.linkedin_speaker,
                         "https://www.linkedin.com/in/eli-jasso")
