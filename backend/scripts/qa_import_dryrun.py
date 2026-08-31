#!/usr/bin/env python
"""
qa_import_dryrun.py
────────────────────
READ-ONLY. Runs a spreadsheet through the fixed import path's coercion and
prints what it would do, per column. Writes nothing, touches no table, opens no
transaction.

    cd backend
    python scripts/qa_import_dryrun.py data_imports/master_data_26aug.xlsx

WHAT IT IS FOR
This is the QA check for the whole coercion change in one command. It reproduces
exactly what the Smart Import wizard now shows on its review step — the same
`accounts.booking_coercion.column_report` the endpoint calls — so a spreadsheet
can be judged before anybody imports it, and so the fix can be demonstrated
against the file that caused the incident.

Run it against `data_imports/master_data_26aug.xlsx` and the Payable/Free line
should read 15,175 accepted where the 26 August import silently discarded 11,210
of those values. That difference IS the fix.

WHAT THE COLUMNS MEAN
  auto-mapped   the wizard resolves this header to a field. A header shown under
                UNMAPPED is one the import would SKIP; that is now reported on
                screen instead of passing for a clean import.
  accepted      cells that will be stored as a real value
  blank         genuinely empty cells, stored as blank or left to the model's own
                default. Never a coerced value.
  rejected      cells with content that cannot be read. Each one FAILS ITS ROW —
                nothing partial is written for that row — and is listed with the
                offending value so the sheet can be corrected.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

import openpyxl  # noqa: E402

from accounts.booking_coercion import RULES, coerce_row, column_report  # noqa: E402
from book_event.views import BOOKING_IMPORT_FIELDS  # noqa: E402

SKIP = None


def nrm(s):
    return "".join(c for c in str(s).lower() if c.isalnum())


def auto_map(header):
    """The wizard's autoMap, ported. See ImportWizard.jsx for the original."""
    norm = nrm(header)
    for key, label, aliases in BOOKING_IMPORT_FIELDS:
        if norm in {nrm(key), nrm(label)} | {nrm(a) for a in aliases}:
            return key
    for key, _, _ in BOOKING_IMPORT_FIELDS:
        kn = nrm(key)
        if kn in norm or norm in kn:
            return key
    return SKIP


def read_sheet(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    it = ws.iter_rows(values_only=True)
    headers = [str(h).strip() if h is not None else "" for h in next(it)]
    rows = []
    for raw in it:
        if all(c is None or str(c).strip() == "" for c in raw):
            continue
        rows.append(dict(zip(headers, raw)))
    return headers, rows


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}")
        return 2

    print(f"Reading {path} ...")
    headers, sheet_rows = read_sheet(path)
    print(f"  {len(sheet_rows):,} data rows, {len(headers)} columns\n")

    mapping = {h: auto_map(h) for h in headers}
    mapped = {h: k for h, k in mapping.items() if k}
    unmapped = [h for h, k in mapping.items() if not k]

    print("-- COLUMN MAPPING ----------------------------------------------")
    for h in headers:
        target = mapping[h]
        print(f"  {h:22} -> {target if target else '*** UNMAPPED, WOULD BE SKIPPED ***'}")
    print()
    if unmapped:
        print(f"  {len(unmapped)} column(s) would be SKIPPED: {', '.join(unmapped)}")
        print("  The wizard now names these on the review step before any write.")
    else:
        print("  Every column in this file maps to a field. Nothing would be skipped.")
    print()

    # Rows in the shape bulk_import receives them.
    rows = [
        {target: r.get(h) for h, target in mapped.items()}
        for r in sheet_rows
    ]

    print("-- PER-COLUMN OUTCOME ------------------------------------------")
    print(f"  {'column':22} {'accepted':>10} {'blank':>9} {'rejected':>9}")
    print(f"  {'-' * 22} {'-' * 10} {'-' * 9} {'-' * 9}")
    report = column_report(rows)
    for c in report:
        print(f"  {c['label']:22} {c['accepted']:>10,} {c['blank']:>9,} "
              f"{c['rejected']:>9,}" + ("   <-- REJECTED" if c["rejected"] else ""))
    print()

    worst = [c for c in report if c["rejected"]]
    if worst:
        print("-- VALUES THAT CANNOT BE READ ----------------------------------")
        for c in worst:
            print(f"  {c['label']}:")
            for ex in c["examples"]:
                print(f"      {ex['value']!r}  on {ex['rows']:,} row(s)")
            if c["allowed"]:
                print(f"      accepted values: {', '.join(c['allowed'])}")
        print()

    bad_rows = 0
    for r in rows:
        if coerce_row(r)[1]:
            bad_rows += 1
    print("-- ROW OUTCOME -------------------------------------------------")
    print(f"  rows that would import        : {len(rows) - bad_rows:,}")
    print(f"  rows that would be REPORTED   : {bad_rows:,}")
    print()

    # Columns the file does not carry at all, so nothing is written to them and
    # a stored value is never overwritten with a default.
    absent = [RULES[k].label for k in RULES if k not in mapped.values()]
    if absent:
        print(f"  Not in this file, so left untouched: {', '.join(sorted(absent))}")
    print()
    print("Nothing was written. This script cannot write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
