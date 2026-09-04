"""
accounts/spreadsheet_export.py
───────────────────────────────
`GET {resource}/export/` — the rows the table is CURRENTLY showing, as a real
.xlsx workbook, for administrators only.

THE COLUMNS ARE THE TABLE'S COLUMNS, DECLARED, NOT THE SERIALIZER'S
This started out writing every field the serializer reports, and that was
wrong in a way worth spelling out: the API payload is not the screen. Bookings
alone carries `id`, `book_event_id`, `accounts_contact_email_raw`, the seven
`delegate_*` override columns and the seven `effective_*` twins of the values
already shown, none of which anybody has ever seen in the table, and the
export handed all of them out. So each viewset now DECLARES `export_columns`
as (serializer field, header) pairs in the table's own order, with the table's
own labels, and a field that is not declared cannot leave. Declaring it is not
optional; a viewset that mixes this in without a column list raises rather
than falling back to "everything", because falling back to everything is the
defect.

The pairs mirror one specific frontend file, named in the `export_columns`
comment on the viewset, and adding a column to that table means adding it here
too. The duplication is deliberate and it is the same one
paper_review/importer.py's FIELD_TO_LABEL already carries.

BOOKINGS IS THE ONLY CALLER, AND THAT IS A RULE, NOT AN ACCIDENT
Export exists in exactly two places in this product: here, and Pre Event Docs,
which writes its own workbooks in the browser from
frontend/src/lib/exportSheet.js. Nowhere else. Ticket Central and Webhook Logs
held this mixin for a day and gave it back, and the CSV exports Paper Review
and Proposal Submission used to carry were deleted with it. An export on a
module nobody exports from is a permission surface and a column list to keep
in step, bought for nothing. Before adding a third caller, get it asked for.

XLSX, NOT CSV
The desk opens these in Excel, and a CSV opened in Excel is a warning dialog
followed by a guess at every column's type. openpyxl is already a dependency
(accounts/import_common.py reads uploads with it), so writing a real workbook
costs no new package. Written in write_only mode, which streams the rows out to
a temp file rather than holding eleven thousand of them in memory.

SAME ROWS AS THE LIST, BY CONSTRUCTION
`filter_queryset(get_queryset())` is the list endpoint's own pipeline — RBAC
scoping, DjangoFilterBackend, SearchFilter, the period window and the
filter_spec, in that order — rather than a second reading of the query params.
A criterion the list understands is a criterion the export understands, free.
An export resolving a wider set than the screen is a data leak with a filename
attached.

ADMIN ON TOP OF THE MODULE GATE, NOT INSTEAD OF IT
`get_permissions` APPENDS IsAdminRole. Spelling it `permission_classes` on the
action would REPLACE the viewset's crm_permission, which is the bug already
documented on `bulk_delete` in book_delegate/views.py.
"""
from datetime import timedelta, timezone
from io import BytesIO

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework.decorators import action

from .permissions import IsAdminRole

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# The zone the CRM is READ in. Timestamps are stored UTC (settings.TIME_ZONE)
# and every table renders them at +05:30 — see IST_OFFSET_MS in
# frontend/src/lib/helpers.js, which explains why the offset is fixed rather
# than looked up: India has run a single +05:30 with no DST since 1945. A
# workbook that wrote the UTC instant would put "Added Time" on the previous
# day for anything logged after 18:30, against a screen that says otherwise.
IST = timezone(timedelta(hours=5, minutes=30))

# Excel refuses a sheet name over 31 characters or holding : \ / ? * [ ].
_SHEET_BANNED = r':\/?*[]'


class AdminExportMixin:
    """
    Mixin for ViewSets. Adds `GET {resource}/export/`.

        export_columns  = (("event_code", "Event Code"), …)   # required
        export_values   = {"discount": lambda row: …}         # computed cells
        export_filename = "bookings"                          # ".xlsx" appended
        export_sheet_name = "Bookings"

    A column's first element is normally a field of the action's serializer.
    `export_values` overrides it, or supplies it outright, which is how a cell
    the table COMPUTES is exported as the table computes it — Bookings stores a
    discount of 0.20 and shows "20", so the workbook has to say 20 too or the
    column cannot be reconciled with the screen it came from.
    """

    export_columns = ()
    export_values = {}
    export_filename = None
    export_sheet_name = None

    def get_permissions(self):
        perms = super().get_permissions()
        if getattr(self, "action", None) == "export":
            perms.append(IsAdminRole())
        return perms

    def get_export_columns(self):
        return list(self.export_columns)

    def _sheet_name(self, filename):
        raw = self.export_sheet_name or filename.replace("-", " ").title()
        return "".join("-" if c in _SHEET_BANNED else c for c in raw)[:31]

    @action(detail=False, methods=["get"], url_path="export")
    def export(self, request):
        from openpyxl import Workbook

        columns = self.get_export_columns()
        if not columns:
            raise ImproperlyConfigured(
                f"{type(self).__name__} mixes in AdminExportMixin without "
                f"declaring export_columns. State the table's columns; there is "
                f"deliberately no 'export everything' default."
            )

        queryset = self.filter_queryset(self.get_queryset())

        # Instantiated once, over no instance: DRF resolves `fields` off the
        # class, so the columns can be validated before a single row is read.
        serializer = self.get_serializer_class()(context=self.get_serializer_context())
        unknown = [f for f, _ in columns
                   if f not in serializer.fields and f not in self.export_values]
        if unknown:
            raise ImproperlyConfigured(
                f"{type(self).__name__}.export_columns names "
                f"{unknown}, which {serializer.__class__.__name__} does not "
                f"report and export_values does not compute."
            )

        # `basename` is the router's name for this resource ("delegates",
        # "tickets"); it is None on a viewset called outside the router, which
        # is how every APIRequestFactory test calls one.
        filename = (self.export_filename
                    or getattr(self, "basename", None) or "export")

        book = Workbook(write_only=True)
        sheet = book.create_sheet(title=self._sheet_name(filename))
        sheet.append([label for _, label in columns])
        # A header that stays put while you scroll 11,000 rows. write_only
        # workbooks accept this before the rows are appended, not after.
        sheet.freeze_panes = "A2"

        for obj in queryset.iterator(chunk_size=500):
            row = serializer.to_representation(obj)
            sheet.append([self._cell(row, field) for field, _ in columns])

        buffer = BytesIO()
        book.save(buffer)

        response = HttpResponse(buffer.getvalue(), content_type=XLSX_MIME)
        response["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
        return response

    def _cell(self, row, field):
        compute = self.export_values.get(field)
        value = compute(row) if compute else row.get(field)
        # None writes an empty cell rather than the word None; everything else
        # is handed to openpyxl as-is so numbers stay numbers.
        return "" if value is None else _excel_value(value)


def _excel_value(value):
    """
    A REAL date cell where the serializer produced an ISO string.

    DRF renders dates and timestamps as text, and text is what a CSV would have
    to carry. In a workbook it is a waste: "2026-08-25T20:26:32.336950Z" cannot
    be sorted, filtered or formatted by Excel, and it is not what the column
    shows on screen either. Parsed here into a datetime/date, which openpyxl
    writes as a date-formatted cell.

    Timestamps are shifted to IST first, so the cell reads the day and time the
    table read. Plain dates are NOT shifted: a DateField holds a calendar day
    with no instant behind it, and moving it by five and a half hours would be
    inventing a timezone for a value that has none.
    """
    if not isinstance(value, str):
        return value
    stamp = parse_datetime(value)
    if stamp is not None:
        if stamp.tzinfo is not None:
            stamp = stamp.astimezone(IST)
        # Excel has no concept of an offset, and openpyxl refuses an aware
        # datetime outright. Microseconds go with it; no column shows them.
        return stamp.replace(tzinfo=None, microsecond=0)
    day = parse_date(value)
    return day if day is not None else value
