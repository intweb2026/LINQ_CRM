"""
credit_control/data/build_payment_sheet.py
───────────────────────────────────────────
Reduce the Payment Collection workbook to the rows and columns
`import_payment_sheet` actually reads, so the copy committed beside it carries
the migration and nothing else.

WHY A TRIM AND NOT THE FILE AS SENT. The workbook is 7,017 rows over 17 tabs.
The import reads 1,029 of them. The rest is `_Contacts` with 604 contact
records, `_Calls` with 1,075 calls, `Done` with 890 settled bookings, and
`_Ledger` and `_Snapshot` with 1,019 each; committing those would put roughly
seven times the personal data into git history than the migration needs, and
`.gitignore` blocks *.xlsx repo-wide precisely because spreadsheets are the
largest body of personal data that can reach a commit.

WHAT SURVIVES is the four caller tabs and `_Activity`, and within the caller
tabs only the eleven columns the import touches. Phones, job titles, tiers,
discounts, references, EPM status and the rest are dropped: every one of them is
already live in the CRM, which is the same reason the import does not write
them.

Email survives, and has to. It is the join key `_remap` follows through
book_delegates to find a re-invoiced booking, and without it five invoices go
unplaced.

    python credit_control/data/build_payment_sheet.py <source.xlsx>

Re-runnable, and kept in the repo rather than thrown away, so the trim is
reproducible when the sheet is re-exported rather than being a file nobody can
account for.
"""
import sys
from pathlib import Path

from openpyxl import Workbook, load_workbook

OUT = Path(__file__).resolve().parent / "payment_collection.xlsx"

CALLER_TABS = ("Bruce", "Ben", "Derek", "Devin")

# Exactly what Command._read_tabs asks for by name, in the sheet's own order.
# A column not in this list is one the import never looks at.
KEEP = (
    "Invoice Number",
    "Delegate Name",
    "Email",
    "Invoice Type",
    "Invoice Type Notes",
    "Calling Disposition",
    "Remark",
    "Next Action",
    "Callback Date",
    "Last Updated",
)

# _Activity keeps its own four, matched by name for the same reason.
ACTIVITY_TAB = "_Activity"
ACTIVITY_KEEP = ("Timestamp", "Rep", "Key", "Disposition")


def copy_tab(source, target, name, keep):
    """One tab, reduced to `keep`, header row first."""
    rows = source[name].iter_rows(values_only=True)
    header = [str(c).strip() if c is not None else "" for c in next(rows)]
    missing = [column for column in keep if column not in header]
    if missing:
        raise SystemExit(f"{name} is missing {missing}; found {header}")
    positions = [header.index(column) for column in keep]

    sheet = target.create_sheet(name)
    sheet.append(list(keep))
    written = 0
    for row in rows:
        values = [row[p] if p < len(row) else None for p in positions]
        if not any(v is not None and str(v).strip() for v in values):
            continue
        sheet.append(values)
        written += 1
    return written


def main(path):
    source = load_workbook(path, data_only=True)
    target = Workbook()
    # Workbook() ships with one empty sheet, and leaving it would mean the
    # committed file opens on a blank tab.
    target.remove(target.active)

    total = 0
    for name in CALLER_TABS + (ACTIVITY_TAB,):
        if name not in source.sheetnames:
            raise SystemExit(f"{path} has no tab named {name}")
        keep = ACTIVITY_KEEP if name == ACTIVITY_TAB else KEEP
        written = copy_tab(source, target, name, keep)
        print(f"  {name:12} {written:5} rows, {len(keep)} columns")
        total += written

    target.save(OUT)
    print(f"{total} rows written to {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
