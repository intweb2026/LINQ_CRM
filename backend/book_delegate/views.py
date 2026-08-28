import logging

from django.db import transaction
from django.db.models import DecimalField, ExpressionWrapper, F, TextField, Value
from django.db.models.functions import Coalesce, Concat, NullIf, Trim
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.bulk_update import BulkUpdateMixin, build_bulk_update_fields
from accounts.filter_spec import FilterSpecMixin, build_filter_spec_fields
from accounts.ordering import StableOrderingFilter
from accounts.period_filter import PeriodFilterMixin
from accounts.permissions import RBACMixin
from accounts.crm_permissions import crm_permission, has_module_action
from book_event.models import BookEvent
from .models import BookDelegate
from .serializers import (
    BookDelegateListSerializer, BookDelegateDetailSerializer, BookDelegateWriteSerializer,
)
from .filters import BookDelegateFilter

logger = logging.getLogger(__name__)


from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters


# ── Transfer breadcrumbs ─────────────────────────────────────────────────────
# The wording is not invented here — it is the convention already in the data,
# written by hand in Zoho across ~200 transfers. Two directions, two dominant
# spellings, kept exactly so a transfer made in this CRM is indistinguishable from
# the ones already recorded:
#
#     source row      "Transferred to FAU'25"        (29 rows use the "from -" form
#     destination row "Transferred from - AIU25"      below; both forms appear)
#
# The edition is rendered two-digit, as every existing example does.

def _yy(edition):
    """2026 → '26'. Empty when there is no edition to name."""
    return str(edition)[-2:] if edition else ""


def _transferred_to(event_code, edition):
    suffix = _yy(edition)
    return f"Transferred to {event_code}'{suffix}" if suffix else f"Transferred to {event_code}"


def _transferred_from(event_code, edition):
    return f"Transferred from - {event_code}{_yy(edition)}"


def _append_reference(current, note):
    """
    Add `note` to a reference without discarding what is already there.

    " / " is the separator the data uses — "OC250722019137000 / Transferred to
    FAU'25" keeps a payment reference AND the transfer note on one row. Overwriting
    would throw away the bank reference that proves the credit exists.
    """
    current = (current or "").strip()
    if not current:
        return note
    if note in current:
        return current
    return f"{current} / {note}"


# ── Computed filter expressions ──────────────────────────────────────────────
# Three of the Bookings columns show a value no column holds: the delegate's
# full name, the sales executive's display name, and the discount as a PERCENT.
# The serializer builds each of them in Python, which is fine for rendering and
# useless for filtering — a criterion the backend cannot express falls back to
# filtering whichever page the browser has loaded, so "Name contains smith" over
# 14,800 delegates answered from the 50 rows on screen and the footer counted
# those. These re-state the same three definitions as SQL so the filter reaches
# the whole table.
#
# Each must stay IDENTICAL to its serializer counterpart. Where they drift, the
# rows that come back are not the rows the cells describe, which is worse than
# the page-only filtering this replaces.


def _display_name(first_path, last_path):
    """`"first last".strip()` in SQL — BookDelegate.full_name, and User.get_full_name."""
    return Trim(Concat(
        Coalesce(F(first_path), Value("")),
        Value(" "),
        Coalesce(F(last_path), Value("")),
        output_field=TextField(),
    ))


def _delegate_name():
    """BookDelegate.full_name (models.py) — the value the Name column renders."""
    return _display_name("first_name", "last_name")


def _sales_executive_name():
    """serializers.get_sales_executive_name: the full name, else the username."""
    return Coalesce(
        NullIf(_display_name("invoice__sales_executive__first_name",
                             "invoice__sales_executive__last_name"), Value("")),
        F("invoice__sales_executive__username"),
        output_field=TextField(),
    )


def _discount_percent():
    """
    The stored FRACTION as the percent the table shows.

    book_delegates.discount holds 0.20 for a 20% discount (api/bookings.js
    fractionToPercent), so a filter sent straight at the column would compare
    against a number the user has never seen — "Discount is 20" would match
    nothing while looking like it worked. Multiplying here means the criterion
    is written in the units of the cell.
    """
    return ExpressionWrapper(
        F("discount") * Value(100),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )


class BookDelegateViewSet(PeriodFilterMixin, FilterSpecMixin, BulkUpdateMixin,
                          RBACMixin, viewsets.ModelViewSet):
    permission_classes = [crm_permission("bookings")]

    # ?period= presets, over the same date the Dashboard's monthly chart is keyed
    # on: request_date, falling back to invoice_date. Coalesced rather than
    # request_date alone because 85 of 2,230 invoices carry only an invoice_date,
    # and a window that dropped them would put a different number under the same
    # button on two screens. See accounts/period_filter.py.
    # Now the denormalised delegate column rather than the two-field COALESCE over
    # the joined invoice. accounts/period_filter.py passes a single field straight
    # through as a bare column instead of building COALESCE(...) across the join,
    # so the window is served directly by book_delegates_booked_id_idx. The value
    # is identical by construction: BookDelegate.booked_on IS
    # COALESCE(request_date, invoice_date), written by save() and kept in step by
    # BookEvent.save(). period_filter.py itself is unchanged.
    period_date_fields = ("booked_on",)

    # ── Compound filter spec ──────────────────────────────────────────────────
    # The five person-level fields are RESOLVED: the table shows the delegate
    # override when set, else the invoice's value, so filtering the raw override
    # column would miss every inheriting row — currently all of them, since
    # delegate_payment_status is NULL on every row in the database.
    _RESOLVED = {
        "payment_status": ("delegate_payment_status", "invoice__payment_status",
                           "choice", "Payment Status", list(BookEvent.PaymentStatus.values)),
        "payment_type":   ("delegate_payment_type", "invoice__payment_type",
                           "choice", "Payment Type", list(BookEvent.PaymentType.values)),
        "ticket_tier":    ("delegate_ticket_tier", "invoice__ticket_tier",
                           "choice", "Ticket Tier", list(BookEvent.TicketTier.values)),
        "paid_or_free":   ("delegate_paid_or_free", "invoice__paid_or_free",
                           "choice", "Payable / Free", list(BookEvent.PaidOrFree.values)),
        "payment_date":   ("delegate_payment_date", "invoice__payment_date",
                           "date", "Payment Date", None),
    }

    filter_spec_fields = {
        **build_filter_spec_fields(
            BookDelegate,
            # invoice is the FK object itself; its columns are exposed by name below.
            # delegate_number USED to be excluded here as well. It is a column the
            # table shows and people filter on, and excluding it did not make it
            # unfilterable — it made it filterable in the browser over the loaded
            # page only. It is registered now; the bulk-update exclusion is
            # separate and stays, because save() rewrites it on Cancelled rows.
            exclude={"invoice"},
            labels={"company_name_raw": "Company (raw)", "event_code": "Event Code"},
        ),
        # Person-level resolved fields
        **{
            key: {
                "type": ftype, "label": label,
                **({"choices": choices} if choices else {}),
                "resolved": {"override": override, "invoice": invoice},
            }
            for key, (override, invoice, ftype, label, choices) in _RESOLVED.items()
        },
        # Invoice-sourced scalars the table shows alongside each delegate
        "invoice_number": {"type": "text", "label": "Invoice Number",
                           "source": "invoice__invoice_number"},
        # booking_code is NOT declared here any more: it is a concrete column on
        # BookDelegate, so build_filter_spec_fields() above registers it against the
        # delegate's own value. Keeping the invoice__booking_code source would have
        # filtered a different column from the one the table now displays.
        "company_name":   {"type": "text", "label": "Company",
                           "source": "invoice__company_name"},
        "event_name":     {"type": "text", "label": "Event Name",
                           "source": "invoice__event_name"},
        # RESOLVED, not invoice-sourced: both dates have a per-delegate
        # override now, and filtering the invoice's column would miss every row
        # carrying one while claiming to filter the column the table displays.
        # Declared here rather than in _RESOLVED because that comprehension has
        # no place to carry `nullable`, and a nullable date is what makes the
        # is-empty operators available on these two.
        "request_date":   {"type": "date", "label": "Request Date", "nullable": True,
                           "resolved": {"override": "delegate_request_date",
                                        "invoice": "invoice__request_date"}},
        "invoice_date":   {"type": "date", "label": "Invoice Date", "nullable": True,
                           "resolved": {"override": "delegate_invoice_date",
                                        "invoice": "invoice__invoice_date"}},
        "total_amount":   {"type": "number", "label": "Total Amount",
                           "source": "invoice__total_amount", "nullable": True},
        "currency":       {"type": "choice", "label": "Currency",
                           "source": "invoice__currency",
                           "choices": list(BookEvent.Currency.values)},
        "source":         {"type": "choice", "label": "Source",
                           "source": "invoice__source",
                           "choices": list(BookEvent.Source.values)},

        # ── Columns the table shows that no column holds ──────────────────────
        # See the expression helpers above the class. Each mirrors the serializer
        # field of the same name, so the filter and the cell agree.
        "name":  {"type": "text", "label": "Name", "expression": _delegate_name},
        "owner": {"type": "text", "label": "Sales Executive",
                  "expression": _sales_executive_name},
        # The percent, not the stored fraction — filters are written in the units
        # of the cell. `discount` itself stays registered by the builder above
        # against the raw column, for any caller that means the fraction.
        "discount_percent": {"type": "number", "label": "Discount (%)",
                             "expression": _discount_percent},
        # Accounts Contact falls back to the delegate's own email when the
        # invoice's is blank (serializers.get_accounts_contact_email), which is
        # exactly the resolved shape the person-level fields use — override
        # first, then the other side — so it is declared the same way rather
        # than as an expression.
        "accounts_contact_email": {
            "type": "text", "label": "Accounts Contact",
            "resolved": {"override": "invoice__accounts_contact_email",
                         "invoice": "email"},
        },
        # created_at / updated_at are in DEFAULT_EXCLUDES because on most models
        # they carry no business meaning. On Bookings they are two visible
        # columns — Added Time and Modified Time — so they are registered here
        # under the names the table uses. has_time is what tells the client to
        # send the END of a day as the upper bound rather than its midnight; see
        # accounts/period_filter.day_bounds() for the trap it avoids.
        "added_time":    {"type": "date", "label": "Added Time",
                          "source": "created_at", "has_time": True},
        "modified_time": {"type": "date", "label": "Modified Time",
                          "source": "updated_at", "has_time": True},
    }

    # ── Mass update ───────────────────────────────────────────────────────────
    # Both groups are derived from their model's own columns, which is what keeps
    # the person-level keys honest: payment_status / payment_date /
    # invoice_number are read-only @property on BookDelegate (models.py:131-145)
    # rather than columns, so they cannot appear here at all — only the real
    # delegate_* OVERRIDE columns do. Hand-written, the bare name was one typo
    # away, and it fails QUIETLY: the 21 invoice-sourced fields on
    # BookDelegateListSerializer (serializers.py:48-76) are read_only, so DRF
    # discards a write to them without complaint.
    #
    # Row group  -> the delegate's own columns; touches exactly the selected rows.
    # Parent group -> the shared invoice; ALSO changes delegates on the same
    #                 invoice that the caller never selected. That is the
    #                 `collateral` set the preview enumerates before Apply.
    bulk_update_label       = "delegates"
    bulk_update_parent_path = "invoice"
    bulk_update_fields = {
        # ── Row group: the delegate's own columns ─────────────────────────────
        # nullable mirrors null=True per column, so clearing one of the five
        # delegate_* overrides makes the delegate inherit from the invoice again
        # — a real thing a rep needs, to undo a mistaken override.
        **build_bulk_update_fields(
            BookDelegate,
            exclude=(
                # identity: email and name together ARE the key (Meta.constraints),
                # and a name is not a batch property of anybody.
                "email", "first_name", "last_name",
                # derived in save() (models.py:88-97): event_code is re-parsed
                # into itself plus edition, or inherited from the invoice.
                "event_code", "edition",
                # positional, assigned per invoice rather than edited
                "delegate_number",
                # PARTIALLY derived, which is worse than fully derived here.
                # save() forces it to 0 on a Cancelled delegate and restores 1 on
                # the transition off Cancelled (models.py:72-87), so a batch
                # write would stick on some rows and be silently reverted on the
                # Cancelled ones — after a preview that promised all of them.
                # It moves as a SIDE EFFECT of delegate_payment_status instead,
                # which is declared. The invoice's own delegate_count has no such
                # save() logic and IS wired, in the parent group below.
                "delegate_count",
            ),
            # The delegate_* overrides are bare CharFields with no choices of
            # their own (models.py:107-111), so each list is sourced from the
            # corresponding BookEvent enum — the invoice value it shadows.
            # attendance is the exception: it has its own choices on models.py:46.
            choices={
                "delegate_payment_status": list(BookEvent.PaymentStatus.values),
                "delegate_payment_type":   list(BookEvent.PaymentType.values),
                "delegate_ticket_tier":    list(BookEvent.TicketTier.values),
                "delegate_paid_or_free":   list(BookEvent.PaidOrFree.values),
            },
            labels={
                "delegate_payment_status": "Payment Status (override)",
                "delegate_payment_type":   "Payment Type (override)",
                "delegate_ticket_tier":    "Ticket Tier (override)",
                "delegate_paid_or_free":   "Payable / Free (override)",
                "delegate_payment_date":   "Payment Date (override)",
                "delegate_request_date":   "Request Date (override)",
                "delegate_invoice_date":   "Invoice Date (override)",
                "company_name_raw":        "Company (raw)",
                "add_ons":                 "Add-ons",
            },
        ),
        # ── Parent group: written on the shared invoice ───────────────────────
        # EVERY key here writes the invoice, so it also changes delegates the
        # caller did not select. The mixin counts that blast radius as
        # `collateral` and the modal refuses the two-click path because of it.
        **build_bulk_update_fields(
            BookEvent,
            prefix="invoice",
            group="parent",
            exclude=(
                # identity — unique, and pre-generated by the website
                "invoice_number",
                # derived in BookEvent.save() (models.py:167-183): event_code is
                # re-parsed into itself plus edition, and event_name is rebuilt
                # from the Event catalogue.
                "event_code", "edition", "event_name",
                # website intake provenance — writing these would falsify where
                # a booking came from
                "source", "form_name", "form_url",
                # superseded by paid_or_free, kept only for historical rows
                "paid_free",
            ),
            labels={
                "accounts_contact_email": "Accounts Contact Email",
                "add_ons_total_amount":   "Add-ons Total Amount",
                "pre_tax_amount":         "Pre-tax Amount",
                "parent_code":            "Parent Code",
                "add_ons":                "Add-ons",
                "paid_or_free":           "Payable / Free",
                "delegate_count":         "Delegate Count (invoice)",
                "attendance":             "Attendance (invoice)",
                "discount_code":          "Discount Code",
            },
        ),
    }
    bulk_update_side_effects = {
        ("delegate_payment_status", "Cancelled"): "also sets delegate_count → 0",
    }

    def get_bulk_update_side_effects(self, field, raw_value):
        """
        Two consequences fire for ANY value, so neither can be keyed by
        (field, value) in the static dict above.

        booking_code: the Bookings modal keeps the invoice's own copy in step by
        writing the delegates' shared code back whenever they all agree
        (frontend/src/api/bookings.js). Nothing does that here, so a bulk write
        leaves invoice.booking_code holding the old value while revenue
        classification still reads it (book_event/views.py, config/views.py).

        Any parent write: BookEvent.save() re-derives event_name from the Event
        catalogue and re-parses edition out of event_code on every save, whatever
        column was actually set.
        """
        if field == "booking_code":
            return [
                "the invoice's own booking_code is NOT updated; revenue "
                "classification and the sync export still read that column"
            ]
        config = self.bulk_update_fields.get(field) or {}
        if config.get("group") == "parent":
            return [
                "saving the invoice also re-derives its event_name from the "
                "Events catalogue and re-parses its edition from the event code"
            ]
        return super().get_bulk_update_side_effects(field, raw_value)
    # StableOrderingFilter, not the stock one: the default sort
    # (-_sort_request_date) is heavily tied, and without a pk tiebreaker
    # pagination duplicates and skips rows. See accounts/ordering.py.
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, StableOrderingFilter]
    filterset_class = BookDelegateFilter
    search_fields   = [
        "first_name", "last_name", "email", "position",
        "invoice__invoice_number", "event_code", "company_name_raw",
    ]
    ordering_fields = [
        "id",   # was silently dropped as an unknown field before
        "_sort_invoice", "_sort_status", "_sort_date", "_sort_name", "_sort_request_date",
        "first_name", "last_name", "email", "event_code", "attendance", "created_at",
        "position", "company_name_raw",
        # Resolved person-level ordering. These are what the table must use for the
        # five override-backed columns — _sort_status orders by the invoice value and
        # therefore disagrees with the displayed cell. DRF silently DROPS an ordering
        # term that is not listed here, which is why they are named explicitly.
        "_sort_effective_payment_status", "_sort_effective_payment_type",
        "_sort_effective_paid_or_free", "_sort_effective_ticket_tier",
        "_sort_effective_payment_date",
        # The denormalised sort key. Listed because DRF silently DROPS an ordering
        # term it does not find here, so an unlisted default would degrade to the
        # pk tiebreak alone without any error.
        "booked_on",
        # The Modified Time column, and the DEFAULT below. It was absent, which is
        # why that column's header was dead: BookingsPage.jsx declares a
        # serverOrdering only for terms listed here, and DataTable disables the
        # header when there is none rather than sort one loaded page and imply it
        # sorted the table.
        "updated_at",
    ]
    # HISTORY, kept because both columns below are still live sort terms and
    # still carry indexes. This block explains the move from -booked_on to
    # -created_at; the CURRENT default is -updated_at and its reasoning sits
    # immediately above the `ordering` line at the end of this block.
    #
    # PREVIOUSLY: DEFAULT ORDERING CHANGED, from -booked_on to -created_at.
    #
    # WHAT THE USER ASKED FOR, AND WHY booked_on COULD NOT GIVE IT
    # The Bookings table must show the most RECENTLY ADDED rows first. booked_on
    # is COALESCE(invoice.request_date, invoice.invoice_date) — a BUSINESS date
    # authored on the invoice, not a record of when the row was created. A
    # delegate added today to an invoice raised three weeks ago inherits that
    # invoice's dates and lands three weeks down the table, so newly entered
    # work was invisible at the top. That is the reported symptom: rows entered
    # on 2026-08-24 sat below a 2026-08-21 head of table.
    #
    # THE KNOWN COST, MEASURED, NOT ASSUMED. import_booking_excel DOES carry
    # Zoho's "Added Time" onto created_at (`if del_dt: bd.created_at = del_dt`),
    # so it is tempting to call created_at a true per-row entry time. On the
    # development database it is NOT: all 1,251 imported delegates carry
    # 2026-08-14 15:07:xx, the load timestamp, and exactly one row — the only one
    # a person actually entered — carries a distinct 2026-08-21. The imported
    # rows differ only by the microseconds of the insert loop, which encodes the
    # source FILE's order and nothing about the business.
    #
    # So for the BACKLOG this ordering is arbitrary-looking where -booked_on was
    # chronological, and that is a real regression for anyone scanning old rows
    # by date. It is accepted deliberately, because the whole backlog still sorts
    # BELOW every later entry — which is the property that was asked for — and
    # because Request Date remains a sortable column for the chronological read.
    # If Added Time is ever backfilled properly, this ordering gets better on its
    # own with no code change.
    #
    # NOT NULLABLE, so unlike booked_on there is no nulls_first hazard here and
    # no nulls_last spelling is needed: created_at is
    # DateTimeField(default=timezone.now).
    #
    # -id IS PART OF THE DEFAULT, NOT LEFT TO StableOrderingFilter. The filter
    # would append `pk` ASCENDING, which resolves ties oldest-first inside a
    # tied second — wrong for a newest-first table, and it would not match the
    # index below. Spelling -id here also makes the filter pass the ordering
    # through untouched, because it treats a leading -id as already
    # deterministic.
    #
    # WHAT DID NOT CHANGE. period_date_fields stays ("booked_on",): the period
    # WINDOW is a business-date question and must keep agreeing with the
    # dashboards. Filtering and sorting are now deliberately different columns,
    # which is why booked_on keeps both its column and its index.
    #
    # Served by book_delegates_created_id_idx — (created_at DESC, id DESC),
    # added in backend/sql/2026_08_bookings_created_order.sql — so this is one
    # single-table index scan, the same shape the booked_on work established.
    #
    # PREVIOUS RATIONALE, KEPT because booked_on is still the period column and
    # this explains why it exists at all. The ordering before booked_on was
    # -_sort_request_date, an F() on invoice__request_date: the sort lived on the
    # JOINED side while the pk tiebreak lived on the DRIVING side, across a
    # varchar join, so no index could serve the shape and the measured plan was a
    # full hash join plus a sort of the entire set to return 50 rows. Moving the
    # value onto book_delegates.booked_on made it single-table, and it stays the
    # period window's column for exactly that reason.
    #
    # _sort_request_date is deliberately KEPT in both the annotation block and
    # ordering_fields: frontend/src/pages/BookingsPage.jsx sends it as the Request
    # Date column's serverOrdering, and dropping it would silently disable that
    # header. The same is true of created_at, which the Added Time column sends.
    # DEFAULT ORDERING CHANGED AGAIN, from -created_at to -updated_at, BY REQUEST.
    #
    # WHAT WAS ASKED FOR. The table must lead with the row someone touched LAST,
    # not the row entered last. Those are the same thing only until the first
    # edit; -created_at pinned a booking to its entry position forever, so a
    # correction made this morning to a row entered in July stayed in July and
    # the person who made it had no way to see their own work.
    #
    # updated_at IS auto_now=True, so every full save() stamps it and the rows
    # reorder themselves with no extra bookkeeping. The write paths that matter
    # all take that route: BookDelegateListSerializer.update() calls a bare
    # instance.save(), and accounts/bulk_update.py deliberately saves object by
    # object rather than through queryset.update() (see its module docstring), so
    # a mass edit stamps every row it touches.
    #
    # THE GAPS, BOTH NOW CLOSED, stated because neither is visible from here.
    #
    # A queryset .update() bypasses auto_now entirely, so every such path has to
    # stamp updated_at itself. There are four: services.py
    # clear_delegate_overrides() and sync_invoice_to_delegates(),
    # book_event/serializers.py's invoice-number cascade, and — the one that
    # mattered — the per-delegate branch of BookEventSerializer.update(), which
    # is where EVERY delegate edit made in the Bookings modal lands.
    #
    # And an invoice-level edit used to bump BookEvent.updated_at and NOT the
    # delegates', so a booking changed through the invoice panel did not rise.
    # That was written down here as defensible, on the grounds that fixing it
    # meant touching every delegate on an invoice on every invoice save. It was
    # defensible for SORT ORDER and indefensible for the Data API's
    # ?updated_since= delta feed, which reads this column and nothing else, so an
    # invoice payment edit left an external consumer showing the old status for
    # good. BookEvent.save() now stamps the delegates, but only when a column in
    # BookEvent.DELEGATE_EXPORT_FIELDS actually moved, so an invoice save that
    # touched none of them still writes nothing.
    #
    # A BULK EDIT MOVES EVERY ROW IT TOUCHED to the top of the table. That is
    # inherent to the request, not a defect, and it is accepted knowingly.
    #
    # -id IS PART OF THE DEFAULT, NOT LEFT TO StableOrderingFilter, for exactly
    # the reason spelled out for -created_at above: the filter would append `pk`
    # ASCENDING, resolving ties oldest-first inside a tied microsecond.
    #
    # Served by book_delegates_updated_id_idx — (updated_at DESC, id DESC), added
    # in backend/sql/2026_08_bookings_modified_order.sql. updated_at is NOT NULL,
    # so there is no nulls_first hazard and no nulls_last spelling is needed.
    #
    # created_at stays in ordering_fields and keeps its index: it is still the
    # Added Time column's serverOrdering and still a sort the user can pick.
    ordering        = ["-updated_at", "-id"]
    # The date columns are all nullable, and Postgres orders NULLs FIRST on a
    # DESC sort: "newest first" on Date Paid came back led by every delegate
    # with no payment date at all. Undated rows now land at the END in both
    # directions, matching the browser-side sort. created_at is deliberately
    # NOT here — it is never null and its plain DESC term is index-served.
    nulls_last_ordering_fields = [
        "_sort_request_date", "_sort_date", "_sort_effective_payment_date",
    ]

    def get_queryset(self):
        from django.db.models import F, Value
        from django.db.models.functions import Coalesce, Concat, NullIf
        qs = BookDelegate.objects.select_related("invoice__sales_executive", "company")
        qs = qs.annotate(
            _sort_invoice=F("invoice__invoice_number"),
            # NOTE: _sort_status orders by the INVOICE column, which is NOT what the
            # Payment Status cell displays. Kept because existing callers pass it,
            # but the table should use _sort_effective_payment_status below.
            _sort_status=F("invoice__payment_status"),
            # Both spelled as the RESOLVED value for the same reason as the
            # effective_* terms below: a delegate can carry its own request or
            # invoice date, and sorting the invoice's column would order the
            # table by a value the cell is not showing.
            _sort_date=Coalesce("delegate_invoice_date", "invoice__invoice_date"),
            _sort_request_date=Coalesce("delegate_request_date", "invoice__request_date"),
            _sort_name=Concat(F("first_name"), Value(" "), F("last_name")),
            # ── Ordering over the RESOLVED person-level values ────────────────
            # Same expression as accounts/filter_spec.py _resolved_expression and
            # the serializer's effective_* fields:
            #   COALESCE(NULLIF(<override>, ''), <invoice column>)
            # Without these, sorting Payment Status ordered by the invoice value
            # while the cell showed the resolved one, so the header claimed an order
            # the rows did not have as soon as any delegate carried an override.
            # See accounts/tests_resolved_ordering.py for the reproduction.
            _sort_effective_payment_status=Coalesce(
                NullIf("delegate_payment_status", Value("")), "invoice__payment_status"),
            _sort_effective_payment_type=Coalesce(
                NullIf("delegate_payment_type", Value("")), "invoice__payment_type"),
            _sort_effective_paid_or_free=Coalesce(
                NullIf("delegate_paid_or_free", Value("")), "invoice__paid_or_free"),
            _sort_effective_ticket_tier=Coalesce(
                NullIf("delegate_ticket_tier", Value("")), "invoice__ticket_tier"),
            # A DateField cannot hold '', so NULLIF would be a type error here.
            _sort_effective_payment_date=Coalesce(
                "delegate_payment_date", "invoice__payment_date"),
        )
        return self.rbac_filter_invoice(qs)

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return BookDelegateWriteSerializer
        if self.action == "retrieve":
            return BookDelegateDetailSerializer
        return BookDelegateListSerializer

    @action(detail=False, methods=["get"], url_path=r"by_invoice/(?P<invoice_number>[^/.]+)")
    def by_invoice(self, request, invoice_number=None):
        """GET /api/delegates/by_invoice/{invoice_number}/"""
        qs = self.get_queryset().filter(invoice__invoice_number=invoice_number)
        return Response(BookDelegateListSerializer(qs, many=True).data)

    # NO permission_classes OVERRIDE — the viewset's crm_permission("bookings")
    # gates this, and "bulk_delete" is in its _DELETE_ACTIONS set, so the caller
    # needs the `delete` cell on Bookings and nothing more.
    #
    # It used to carry permission_classes=[IsAdminRole], which REPLACES the module
    # gate rather than adding to it. IsAdminRole admits only HP, `role == "admin"`,
    # or a team flagged is_all_access — so granting somebody Bookings → delete in
    # the permission grid did not let them delete. The UI shows the Delete button on
    # exactly that grant (BookingsPage.jsx), so the button appeared, the click 403'd,
    # and the rows stayed: the reported "delete does nothing". The two gates have to
    # read the same cell or the grid is not the answer to "who may delete a booking".
    #
    # This is not a widening past what the grid says: reach is still bounded by
    # get_queryset() -> rbac_filter_invoice() below, so the delete cell buys the
    # ACTION, never rows outside the caller's scope.
    @action(detail=False, methods=["post"], url_path="bulk_delete")
    def bulk_delete(self, request):
        """
        Delete up to 1000 delegate records by ID, RBAC-SCOPED.

        Previously this ran `BookDelegate.objects.filter(id__in=ids)` — the default
        manager, not the scoped queryset — so any caller past the permission gate
        could delete ANY delegate row by guessing its id, regardless of event
        assignment.

        Resolving through self.get_queryset() routes the deletion through
        rbac_filter_invoice(), so a caller can only ever delete rows already inside
        their scope. IDs outside it are silently skipped rather than 403ing the
        whole batch — the response reports requested vs permitted so a partial
        delete is visible rather than looking like a success.

        Two-step (ids first, then a clean manager) because get_queryset() carries
        annotations and select_related, and .delete() on an annotated queryset is
        not reliable. permitted_ids is already scoped, so scoping is preserved.
        """
        from accounts.models import ActionLog
        ids = request.data.get("ids", [])
        if not isinstance(ids, list) or not ids:
            return Response({"detail": "ids list required"}, status=400)
        if len(ids) > 1000:
            return Response({"detail": "Maximum 1000 IDs per request"}, status=400)

        permitted_ids = list(
            self.get_queryset().filter(id__in=ids).values_list("id", flat=True)
        )
        skipped = len(set(ids)) - len(permitted_ids)
        if skipped:
            logger.warning(
                "bulk_delete: %s requested %d delegate ids, %d were out of scope",
                request.user.username, len(set(ids)), skipped,
            )
        if not permitted_ids:
            return Response(
                {"detail": "None of the requested records are in your scope.",
                 "deleted": 0, "requested": len(ids), "permitted": 0},
                status=403,
            )

        with transaction.atomic():
            qs = BookDelegate.objects.filter(id__in=permitted_ids)
            count = qs.count()
            # The invoices these delegates hang off, read BEFORE the delete,
            # because afterwards no row points at them any more.
            touched_invoices = set(qs.values_list("invoice_id", flat=True))
            ActionLog.objects.create(
                user    = request.user,
                action  = f"Bulk deleted {count} booking delegates",
                # Full permitted list, not ids[:50]: the audit trail should record
                # what was actually deleted, not a truncated sample of what was asked.
                details = (
                    f"requested={len(ids)} permitted={count} out_of_scope={skipped}\n"
                    f"ids={sorted(permitted_ids)}"
                ),
            )
            qs.delete()

            # DELETE THE INVOICE ITS LAST DELEGATE JUST LEFT.
            #
            # Without this, deleting every delegate on an invoice cleared the rows
            # from the Bookings table and left the BookEvent behind: invisible in
            # the UI, still counted by the dashboard aggregates that read BookEvent
            # (config/views.py), still exported by the Data API's `bookings`
            # resource, and still holding its unique invoice_number. That last part
            # is what made a delete look undone. The webhook and every importer
            # upsert on invoice_number, so the next payload carrying that number
            # UPDATED the surviving invoice and re-created its delegates. Nothing
            # was soft-deleted and nothing was backed up; the invoice was simply
            # never deleted in the first place.
            #
            # Only invoices that lost a delegate in THIS request are considered, and
            # only those with none left, so an invoice that legitimately has no
            # delegates yet — a website booking whose delegates have not arrived —
            # is untouched unless this request is what emptied it.
            orphaned = [
                number for number in touched_invoices
                if number and not BookDelegate.objects.filter(invoice_id=number).exists()
            ]
            if orphaned:
                BookEvent.objects.filter(invoice_number__in=orphaned).delete()
                ActionLog.objects.create(
                    user    = request.user,
                    action  = f"Deleted {len(orphaned)} emptied invoices",
                    details = f"invoice_numbers={sorted(orphaned)}",
                )
                logger.info("bulk_delete: removed %d invoices left with no delegates",
                            len(orphaned))
        return Response({
            "deleted": count, "requested": len(ids), "permitted": count,
            "out_of_scope": skipped, "invoices_deleted": len(orphaned),
        })

    @action(detail=True, methods=["patch"], url_path="update_attendance")
    def update_attendance(self, request, pk=None):
        """PATCH /api/delegates/{id}/update_attendance/"""
        delegate   = self.get_object()
        attendance = request.data.get("attendance")
        choices    = dict(BookDelegate.Attendance.choices)
        if attendance not in choices:
            return Response({"detail": f"Invalid attendance: {attendance}"}, status=400)
        delegate.attendance = attendance
        delegate.save(update_fields=["attendance", "updated_at"])
        return Response({"id": delegate.id, "attendance": delegate.attendance})

    @action(detail=True, methods=["post"], url_path="transfer")
    def transfer(self, request, pk=None):
        """
        POST /api/delegates/{id}/transfer/  {"target_event_code", "invoice_number"}

        Move ONE delegate's credit to another event. Kept as its own route because
        the table's per-row Transfer button has a single row and nothing else, and
        because ~200 existing transfers in the data are single-delegate moves.

        The work happens in _perform_transfer(), shared with transfer_batch below —
        a partial transfer of 2 delegates out of 5 must behave exactly like two
        single transfers onto the same invoice, and one implementation is the only
        way to guarantee that.
        """
        delegate = self.get_object()          # RBAC-scoped by rbac_filter_invoice
        return _perform_transfer(
            request,
            [delegate],
            (request.data.get("target_event_code") or "").strip(),
            (request.data.get("invoice_number") or "").strip(),
        )

    @action(detail=False, methods=["post"], url_path="transfer")
    def transfer_batch(self, request):
        """
        POST /api/delegates/transfer/
            {"delegate_ids": [..], "target_event_code": "...", "invoice_number": "..."}

        Move SOME of an invoice's delegates to another event, in one transaction.

        WHY THIS EXISTS
        An invoice carrying five delegates where only two are moving is the ordinary
        case, not an edge case. It was already expressible — transfer the first
        delegate, then transfer the second onto the invoice number the first one
        created (the modal's hint said as much) — but as N separate requests it had
        three problems this endpoint fixes:

          NOT ATOMIC. A failure on delegate 4 of 5 leaves four moved and one behind,
          with no way to tell from the data whether that was the intent.

          THE SOURCE INVOICE'S STATUS FLIPPED TOO EARLY. The rule is "the invoice
          reads Credit Transferred when nothing is left on it". Run one delegate at a
          time, and the LAST call is the one that empties the invoice — so the
          invoice's status depended on transfer order, and an interrupted run left it
          holding a status that described a move that had not finished.

          IT LOOKED LIKE FIVE UNRELATED TRANSFERS in the action log.

        SAME SOURCE INVOICE, ONE DESTINATION INVOICE
        Every id must name a delegate on ONE invoice: the operation is "split this
        invoice", and the whole-invoice question above cannot be answered for a mixed
        set. Mixing two source invoices is a 400 rather than a silent guess.
        """
        ids = request.data.get("delegate_ids")
        if not isinstance(ids, list) or not ids:
            return Response(
                {"detail": "delegate_ids must be a non-empty list of delegate ids."},
                status=400,
            )
        try:
            ids = [int(i) for i in ids]
        except (TypeError, ValueError):
            return Response({"detail": "delegate_ids must be integers."}, status=400)

        # Ordered by delegate_number so the rows land on the destination invoice in
        # the order they were listed on the source one, not in id order.
        # get_queryset() is RBAC-scoped (rbac_filter_invoice), so ids outside this
        # user's scope simply do not come back and are reported as not found —
        # never as a permission error naming a record they may not know exists.
        delegates = list(
            self.get_queryset()
            .filter(id__in=set(ids))
            .select_related("invoice")
            .order_by("delegate_number", "id")
        )
        missing = set(ids) - {d.id for d in delegates}
        if missing:
            return Response(
                {"detail": "No booking found for id "
                           + ", ".join(str(i) for i in sorted(missing)) + "."},
                status=404,
            )

        invoice_ids = {d.invoice_id for d in delegates}
        if len(invoice_ids) > 1:
            return Response(
                {"detail": "These bookings are on "
                           f"{len(invoice_ids)} different invoices. Transfer one "
                           "invoice's delegates at a time."},
                status=400,
            )

        return _perform_transfer(
            request,
            delegates,
            (request.data.get("target_event_code") or "").strip(),
            (request.data.get("invoice_number") or "").strip(),
        )


def _invoice_level_value(delegates, field, fallback):
    """
    The value a DESTINATION invoice should carry for one of the fields a delegate
    can override.

    Every delegate agreeing on a non-empty override means the group really does
    share that value, so it belongs on the invoice. Anything else — nobody set it,
    only some did, or they disagree — falls back to the source invoice's column and
    the differences ride along as per-delegate overrides.

    With a single delegate this reduces exactly to the `delegate.x or invoice.x`
    the one-delegate transfer used before this was factored out, which is what
    keeps the existing behaviour (and its tests) intact.
    """
    values = [getattr(d, field) for d in delegates]
    present = [v for v in values if v not in (None, "")]
    if len(present) == len(values) and len(set(present)) == 1:
        return present[0]
    return fallback


def _perform_transfer(request, delegates, target_code, new_number):
    """
    Move `delegates` — all on one invoice — onto `target_code` under `new_number`.

    Every row transferred away reads "Credit Transferred" and a new booking appears
    on the target event reading "Paid (Transferred)". Both rows survive: the pair IS
    the audit trail, and it is the shape ~200 transfers already in the database take,
    made by hand in Zoho (book_delegate/tests_delegate_transfer.py pins the chain
    this mirrors).

    ONE REQUEST, NOT THREE PER DELEGATE
    A transfer is a create plus two updates. Driven from the browser as separate
    requests, a failure between them leaves a delegate credited on two events at
    once, or transferred away to nowhere. So the whole set is one transaction.

    WHERE THE SOURCE STATUS LANDS — AND WHY IT IS DECIDED ONCE, FOR THE SET
    "Credit Transferred" belongs ON the source invoice when the transfer empties it,
    and on the moved delegates as per-delegate overrides when it does not, or the
    delegates staying behind would be relabelled as transferred along with the ones
    that left. The test is therefore whether any delegate is LEFT, counted against
    the whole set at once — not per delegate, which would make the answer depend on
    the order they were processed in.

    WHAT IS NOT DERIVED HERE
    The target invoice number. Existing transfers use the destination event's own
    numbering (AIU25HOU-2804 -> FAU25USA-2587), which this code has no way to
    generate, so the caller supplies it and the collision rules below decide whether
    it may be used.
    """
    from datetime import date
    from django.db.utils import IntegrityError
    from events.models import Event

    source_invoice = delegates[0].invoice

    # Gated on create by the permission class (POST falls through to can_create);
    # the update half has to be asserted here. See
    # accounts/crm_permissions.has_module_action.
    if not has_module_action(request.user, "bookings", "update"):
        return Response(
            {"detail": "Transferring a booking also changes the booking it leaves, "
                       "which needs update permission on bookings."},
            status=status.HTTP_403_FORBIDDEN,
        )

    if not target_code:
        return Response({"detail": "target_event_code is required."}, status=400)
    if not new_number:
        return Response({"detail": "invoice_number is required."}, status=400)

    target_event = Event.objects.filter(event_code=target_code).first()
    if target_event is None:
        return Response({"detail": f"No event with code '{target_code}'."}, status=400)
    if target_code == source_invoice.event_code:
        return Response(
            {"detail": f"This booking is already on {target_code}."}, status=400)

    # No duplicate check over the selection itself. Every delegate here is on ONE
    # invoice (the caller enforces that), and BookDelegate's key refuses the same
    # PERSON twice on one invoice — see Meta.constraints. The reuse check below is
    # still needed: it compares against a DIFFERENT invoice's delegates.

    # An invoice number already in use may be REUSED, but only when it is the same
    # event — that is how a second delegate joins a transfer already made. Anywhere
    # else it would silently file these delegates under another event.
    existing = BookEvent.objects.filter(invoice_number=new_number).first()
    if existing is not None:
        if existing.event_code != target_code:
            return Response(
                {"detail": f"Invoice {new_number} already exists on "
                           f"{existing.event_code}. Use a different number."},
                status=409,
            )
        # Matched on the PERSON, not on the email alone. Two delegates on one
        # invoice may share an email address — one office address covering two
        # owners is ordinary — so an email-only test refused to transfer Emily
        # onto an invoice already holding Brendon, and refused it by naming an
        # address that belongs to somebody else as well as to her.
        taken = {
            BookDelegate.person_key(*row)
            for row in existing.delegates.values_list("email", "first_name", "last_name")
        }
        clash = [d for d in delegates if d.own_person_key in taken]
        if clash:
            return Response(
                {"detail": f"{clash[0].full_name} <{clash[0].email}> is already on "
                           f"invoice {new_number}."
                           + (f" ({len(clash) - 1} more of the selected bookings are "
                              "too.)" if len(clash) > 1 else "")},
                status=409,
            )

    # The destination edition is the TARGET event's year, not the source's:
    # BookEvent.save() only derives an edition from trailing digits in the code, and
    # catalogue codes carry none, so an unset edition would stay null and the booking
    # would be missing from every per-edition report.
    target_edition = (
        target_event.event_date.year if target_event.event_date else source_invoice.edition
    )
    today = date.today()

    # Decided BEFORE anything is written, over the set as a whole. Counting after
    # the fact would see the new rows this transfer creates.
    moved_ids = {d.id for d in delegates}
    left_behind = source_invoice.delegates.exclude(pk__in=moved_ids).count()
    scope = "delegate" if left_behind else "invoice"

    created_delegates = []
    try:
        with transaction.atomic():
            if existing is None:
                dest_invoice = BookEvent.objects.create(
                    invoice_number = new_number,
                    event_code     = target_code,
                    edition        = target_edition,
                    event_date     = target_event.event_date,
                    # Dates are the TRANSFER's, not the original booking's — matching
                    # the existing pairs, where the destination carries the date the
                    # transfer was made.
                    request_date   = today,
                    invoice_date   = today,
                    booking_code   = _invoice_level_value(
                        delegates, "booking_code", source_invoice.booking_code),
                    company_name   = source_invoice.company_name,
                    contact_name   = source_invoice.contact_name,
                    contact_email  = source_invoice.contact_email,
                    contact_phone  = source_invoice.contact_phone,
                    accounts_contact_email = source_invoice.accounts_contact_email,
                    currency       = source_invoice.currency,
                    ticket_tier    = _invoice_level_value(
                        delegates, "delegate_ticket_tier", source_invoice.ticket_tier),
                    payment_type   = _invoice_level_value(
                        delegates, "delegate_payment_type", source_invoice.payment_type),
                    payment_date   = _invoice_level_value(
                        delegates, "delegate_payment_date", source_invoice.payment_date),
                    paid_or_free   = _invoice_level_value(
                        delegates, "delegate_paid_or_free", source_invoice.paid_or_free),
                    payment_status = BookEvent.PaymentStatus.PAID_TRANSFERRED,
                    # Created by hand in the CRM, whatever the original arrived as.
                    source         = BookEvent.Source.MANUAL,
                    sales_executive = BookEvent.auto_assign_sales(target_code),
                    # reference is NOT copied: in the existing pairs the destination
                    # carries only the "Transferred from" breadcrumb, never the
                    # source's payment reference, which belongs to the money received
                    # against the OTHER invoice.
                    #
                    # parent_code is left alone too. It is empty on all 11,042
                    # invoices and nothing reads it, so what it was meant to hold
                    # ("parent event code"? parent invoice?) is a guess — and a link
                    # recorded in the wrong field is worse than one recorded only in
                    # the references and the action log, as here.
                )
            else:
                dest_invoice = existing

            for delegate in delegates:
                # Reusing an invoice must not silently change what THIS row promises.
                # The invoice's own status is left alone — other delegates are booked
                # against it — so where it is not already Paid (Transferred), the
                # delegate carries the transferred status as an override. Without
                # this, joining a Pending invoice would land the transfer as Pending,
                # which is not what the transfer said it would do.
                dest_override = (
                    None
                    if dest_invoice.payment_status == BookEvent.PaymentStatus.PAID_TRANSFERRED
                    else BookEvent.PaymentStatus.PAID_TRANSFERRED
                )

                new_delegate = BookDelegate.objects.create(
                    invoice         = dest_invoice,
                    event_code      = target_code,
                    edition         = target_edition,
                    company         = delegate.company,
                    company_name_raw = delegate.company_name_raw,
                    first_name      = delegate.first_name,
                    last_name       = delegate.last_name,
                    email           = delegate.email,
                    phone_number    = delegate.phone_number,
                    position        = delegate.position,
                    ticket_package  = delegate.ticket_package,
                    sponsorship_level = delegate.sponsorship_level,
                    booking_code    = delegate.booking_code,
                    delegate_number = delegate.delegate_number,
                    discount        = delegate.discount,
                    add_ons         = delegate.add_ons,
                    dietary_requirements = delegate.dietary_requirements,
                    notes           = delegate.notes,
                    # A new event has its own door: nobody has attended it yet.
                    attendance      = BookDelegate.Attendance.PENDING,
                    reference       = _transferred_from(source_invoice.event_code,
                                                       source_invoice.edition),
                    # Normally None: a freshly created destination invoice carries
                    # Paid (Transferred) itself, so the resolved status reads from one
                    # place. See dest_override above for the reuse case.
                    delegate_payment_status = dest_override,
                    # The per-delegate values that did NOT make it onto the
                    # destination invoice ride along as overrides, or a group moving
                    # with different tiers would arrive all on one tier. Only written
                    # where they actually differ from what the invoice now says, so a
                    # transfer never invents an override that shadows a matching
                    # invoice value.
                    delegate_ticket_tier = (
                        delegate.delegate_ticket_tier
                        if delegate.delegate_ticket_tier
                        and delegate.delegate_ticket_tier != dest_invoice.ticket_tier
                        else None
                    ),
                    delegate_payment_type = (
                        delegate.delegate_payment_type
                        if delegate.delegate_payment_type
                        and delegate.delegate_payment_type != dest_invoice.payment_type
                        else None
                    ),
                    delegate_paid_or_free = (
                        delegate.delegate_paid_or_free
                        if delegate.delegate_paid_or_free
                        and delegate.delegate_paid_or_free != dest_invoice.paid_or_free
                        else None
                    ),
                    delegate_payment_date = (
                        delegate.delegate_payment_date
                        if delegate.delegate_payment_date
                        and delegate.delegate_payment_date != dest_invoice.payment_date
                        else None
                    ),
                )
                created_delegates.append(new_delegate)

                # ── The row transferred away ─────────────────────────────────
                if scope == "invoice":
                    # Cleared, not left: an override would shadow the invoice value
                    # and the row would still read as whatever it was before.
                    delegate.delegate_payment_status = None
                else:
                    delegate.delegate_payment_status = (
                        BookEvent.PaymentStatus.CREDIT_TRANSFERRED
                    )
                delegate.reference = _append_reference(
                    delegate.reference, _transferred_to(target_code, target_edition))
                delegate.save(update_fields=[
                    "delegate_payment_status", "reference", "updated_at",
                ])

            if scope == "invoice":
                source_invoice.payment_status = BookEvent.PaymentStatus.CREDIT_TRANSFERRED
            source_invoice.updated_by = request.user
            source_invoice.save()

            from accounts.models import ActionLog
            who = ", ".join(d.email for d in delegates[:5]) + (
                f" +{len(delegates) - 5} more" if len(delegates) > 5 else "")
            # A single move keeps the exact wording it has always had
            # ("Transferred delegate <email> to <code>"): rows written before this
            # endpoint existed read that way, and the audit log is searched by that
            # prefix. Only the several-at-once case needs new words.
            ActionLog.objects.create(
                user=request.user,
                action=(
                    f"Transferred delegate {delegates[0].email} to {target_code}"
                    if len(delegates) == 1
                    else f"Transferred {len(delegates)} delegates to {target_code}"
                ),
                details=(
                    f"{who}; from invoice {source_invoice.invoice_number} "
                    f"({source_invoice.event_code}) -> {dest_invoice.invoice_number} "
                    f"({target_code}); source scope={scope}; "
                    f"{left_behind} delegate(s) left on the source invoice; "
                    f"new delegate ids="
                    + ",".join(str(d.id) for d in created_delegates)
                ),
            )
    except IntegrityError as exc:
        # The uniqueness checks above are not a lock: a concurrent transfer can take
        # the number in between. Reported as a conflict rather than a 500.
        logger.warning("transfer collision for %s: %s", new_number, exc)
        return Response(
            {"detail": f"Invoice {new_number} was just taken. Try another number."},
            status=409,
        )

    return Response({
        # `source` and `created` keep the single-delegate shape they have always
        # had — the per-row Transfer button reads them — and name the FIRST moved
        # delegate when several went. `delegates` carries the full pairing, and
        # `count`/`left_behind` are what a partial transfer needs to report.
        "source": {
            "delegate_id": delegates[0].id,
            "invoice_number": source_invoice.invoice_number,
            "event_code": source_invoice.event_code,
            "payment_status": BookEvent.PaymentStatus.CREDIT_TRANSFERRED,
            "scope": scope,
            "left_behind": left_behind,
        },
        "created": {
            "delegate_id": created_delegates[0].id,
            "invoice_id": dest_invoice.id,
            "invoice_number": dest_invoice.invoice_number,
            "event_code": dest_invoice.event_code,
            "payment_status": BookEvent.PaymentStatus.PAID_TRANSFERRED,
            "reused_invoice": existing is not None,
        },
        "count": len(created_delegates),
        "delegates": [
            {"source_delegate_id": src.id, "delegate_id": dst.id, "email": dst.email}
            for src, dst in zip(delegates, created_delegates)
        ],
    }, status=status.HTTP_201_CREATED)
