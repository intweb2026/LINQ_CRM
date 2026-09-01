#!/usr/bin/env python
"""
make_qa_import_fixture.py
──────────────────────────
Writes `qa_import_fixture.xlsx`, a ten-row workbook for walking the Smart Import
wizard by hand. Every row is engineered so that one of the eight fixes produces a
visible, checkable outcome, and the expected result is printed when this runs.

    cd backend
    python scripts/make_qa_import_fixture.py

WHY A PURPOSE-BUILT FILE
The real 26 August workbook is 15,180 rows and proves the coercion (see
scripts/qa_import_dryrun.py), but it is useless for checking the SCREENS: nobody
can eyeball 15,180 rows to see whether one mixed invoice kept both its values.
This file is small enough to check every cell by eye and is deliberately shaped
like the failures rather than like typical data.

The headers are the real ones from master_data_26aug.xlsx, so importing this file
also exercises the alias fix -- "Delegate Company", "Delegate Email" and "Ref"
all have to resolve on their own, with no hand-correction on the mapping step.

SAFE TO IMPORT: every invoice number begins "QA-", so the rows are trivial to
find and delete afterwards. Import it into a test environment, not Live.
"""
from __future__ import annotations

from pathlib import Path

import openpyxl

HEADERS = [
    "Payment Status", "Event Code", "Booking Code", "Request Date",
    "Invoice Date", "Invoice Number", "Name", "Delegate Company",
    "Delegate Email", "Direct Line", "Accounts Contact", "Delegate Count",
    "Payable/Free", "Payment Date", "Payment Type", "Ticket Tier",
    "Discount", "Add-Ons", "Ref", "Event Name", "Attendance - IN?",
    # NOT a real column. Present so the review step has something to report as
    # unmapped: a header that resolves to nothing must be named on screen before
    # any write, which is the second half of fix 1.
    "Sponsor Lanyard Colour",
]


def r(**kw):
    row = {h: "" for h in HEADERS}
    row.update({
        "Payment Status": "Paid",
        "Event Code": "QA-TEST-26",
        "Event Name": "QA Test Event",
        "Request Date": "01/08/2026",
        "Invoice Date": "02/08/2026",
        "Delegate Company": "QA Analytical Engines",
        "Direct Line": "+44 20 7000 0000",
        "Delegate Count": 1,
        "Sponsor Lanyard Colour": "teal",
    })
    row.update(kw)
    return row


ROWS = [
    # 1-2  fix 2 + fix 3. One invoice, two delegates, differing on FOUR columns.
    r(**{"Invoice Number": "QA-MIX-1", "Name": "Ada Lovelace",
         "Delegate Email": "qa.ada@example.test", "Payable/Free": "Payable",
         "Booking Code": "Speaker", "Payment Type": "Bank", "Ticket Tier": "SEB",
         "Payment Date": "03/08/2026"}),
    r(**{"Invoice Number": "QA-MIX-1", "Name": "Alan Turing",
         "Delegate Email": "qa.alan@example.test", "Payable/Free": "Free",
         "Booking Code": "Delegate", "Payment Type": "Stripe", "Ticket Tier": "EB",
         "Payment Date": "04/08/2026", "Payment Status": "Free"}),

    # 3-4  fix 5. The same discount written both ways, on two invoices.
    r(**{"Invoice Number": "QA-PCT-1", "Name": "Grace Hopper",
         "Delegate Email": "qa.grace@example.test", "Payable/Free": "Payable",
         "Discount": "20%"}),
    r(**{"Invoice Number": "QA-PCT-2", "Name": "Katherine Johnson",
         "Delegate Email": "qa.katherine@example.test", "Payable/Free": "Payable",
         "Discount": "0.2"}),

    # 5  fix 4/F6. A stated zero, which the old floor rewrote as one.
    r(**{"Invoice Number": "QA-ZERO-1", "Name": "Edsger Dijkstra",
         "Delegate Email": "qa.edsger@example.test", "Payable/Free": "Free",
         "Delegate Count": 0}),

    # 6-7  fix 4/F7. Attendance translated rather than fallen into.
    r(**{"Invoice Number": "QA-ATT-1", "Name": "Barbara Liskov",
         "Delegate Email": "qa.barbara@example.test", "Payable/Free": "Payable",
         "Attendance - IN?": "false"}),
    r(**{"Invoice Number": "QA-ATT-2", "Name": "Donald Knuth",
         "Delegate Email": "qa.donald@example.test", "Payable/Free": "Payable",
         "Attendance - IN?": "true"}),

    # 8-10  fix 4. Three cells with content that cannot be read. Each must FAIL
    # ITS OWN ROW and be named; the seven rows above must import regardless.
    r(**{"Invoice Number": "QA-BAD-1", "Name": "Bad Pof",
         "Delegate Email": "qa.bad1@example.test", "Payable/Free": "Sponsored"}),
    r(**{"Invoice Number": "QA-BAD-2", "Name": "Bad Discount",
         "Delegate Email": "qa.bad2@example.test", "Payable/Free": "Payable",
         "Discount": "ask Steve"}),
    r(**{"Invoice Number": "QA-BAD-3", "Name": "Bad Tier",
         "Delegate Email": "qa.bad3@example.test", "Payable/Free": "Payable",
         "Ticket Tier": "Platinum"}),
]

EXPECTED = """
EXPECTED RESULT, to check against the screens
=============================================

MAP FIELDS step
  21 of 22 columns map with no hand-correction. "Delegate Company",
  "Delegate Email" and "Ref" must resolve on their own -- before the fix the
  first two resolved to nothing and were skipped in silence.

REVIEW step
  * "Sponsor Lanyard Colour" is named as a column that will not be imported.
  * The per-column table reports exactly this. Verified against
    scripts/qa_import_dryrun.py, which calls the same column_report() the
    endpoint does, so the screen and the command agree cell for cell:
        Payable / Free    9 accepted,  0 blank,  1 not recognised  ('Sponsored')
        Ticket Tier       2 accepted,  7 blank,  1 not recognised  ('Platinum')
        Discount          2 accepted,  7 blank,  1 not recognised  ('ask Steve')
        Delegate Count   10 accepted,  0 blank,  0 not recognised
        Attendance        2 accepted,  8 blank,  0 not recognised
        Payment Status   10 accepted,  0 blank,  0 not recognised
        Booking Code      2 accepted,  8 blank,  0 not recognised
  * "3 of 10 rows hold a value that cannot be read."
  * The import can still be cancelled from here.

AFTER IMPORT
  7 imported, 3 errors. The three QA-BAD invoices do NOT exist -- not even
  partially. Each error names its row, its column and the offending value.

  Bookings table, invoice QA-MIX-1, two delegate rows:
        Ada Lovelace   Payable   Speaker    Bank     SEB   03/08/2026
        Alan Turing    Free      Delegate   Stripe   EB    04/08/2026
    Both rows keep their OWN value on all five columns. Before the fix one
    row's values were written to the invoice and shown for both people, and
    which row won depended on the order they sat in the file.

  QA-PCT-1 and QA-PCT-2 both show a 20% discount. Before the fix QA-PCT-1
  imported as no discount at all.

  QA-ZERO-1 shows Delegate Number 0, not 1.
  QA-ATT-1 shows Pending. QA-ATT-2 shows Confirmed.

  The done screen shows an "Import reference" UUID. Every row above is
  listable from it alone:
        SELECT invoice_number FROM book_events  WHERE import_batch_id = '<ref>';
        SELECT email          FROM book_delegates WHERE import_batch_id = '<ref>';

RE-IMPORT THE SAME FILE
  Choose "upsert" and import it again. Nothing changes: same 7 rows, same
  values, no duplicates. That is the review's own verification step, and it is
  the test that the fix is real rather than merely different.

CLEAN UP
        DELETE FROM book_delegates WHERE invoice_number LIKE 'QA-%';
        DELETE FROM book_events    WHERE invoice_number LIKE 'QA-%';
"""


def main():
    out = Path(__file__).resolve().parent.parent / "data_imports" / "qa_import_fixture.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "QA"
    ws.append(HEADERS)
    for row in ROWS:
        ws.append([row[h] for h in HEADERS])
    wb.save(out)
    print(f"Wrote {out}")
    print(f"  {len(ROWS)} rows, {len(HEADERS)} columns")
    print(EXPECTED)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
