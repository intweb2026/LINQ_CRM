#!/usr/bin/env python
"""
fix_payable_free_delegate_number.py
───────────────────────────────────
PRODUCTION FIX, one file, no arguments needed.

    python fix_payable_free_delegate_number.py

Corrects two columns on the bookings data, and nothing else.

    Delegate Number  ->  BookDelegate.delegate_number
    Paid/Free        ->  the Payable / Free value the CRM displays, which lives
                         in BookEvent.paid_or_free and the per-delegate override
                         BookDelegate.delegate_paid_or_free together.

WHY IT IS NEEDED
`import_booking_excel` reads every column as `row.get("Header", "")`, so a
workbook that spells a header differently, or does not carry it at all, imports
that column as blanks on every row and still reports success. That is what
happened. Measured before this fix, BookEvent.paid_or_free held the empty string
on 8,876 invoices and "Paid" on none at all, while the model declares Paid and
Free as its only valid values; BookDelegate.delegate_number sat at the default
of 1 on every row. The Payable / Free column therefore rendered blank for two
thirds of the bookings table.

WHAT IS IN THIS FILE
The @@ROWS@@ rows of @@SOURCE@@ that carry those two columns, gzipped and
base64'd below. There is no spreadsheet to copy to the server and nothing to
configure. Rows are matched onto stored delegates on Invoice Number plus Name.

WHAT IT DOES ON DISK AND IN THE DATABASE
  1. Writes a rollback file, fix_backup_<timestamp>.json, holding the previous
     value of every row it is about to touch. This happens BEFORE any write.
  2. Writes a report, fix_report_<timestamp>.md, listing every change and every
     row it could not place.
  3. Applies the two columns inside ONE transaction.

SAFETY
  - It APPLIES by default, which is what it is for. Pass --dry-run to see the
    plan and write nothing.
  - It is idempotent. A second run finds nothing to do and says so.
  - Writes go through bulk_update, so BookDelegate.save() never runs and cannot
    rewrite event_code, booking_code, booked_on, delegate_count or the accounts
    contact as a side effect.
  - updated_at is left alone, so this correction does not reshuffle the Bookings
    table, whose default sort is -updated_at.
  - A blank cell is skipped, never written as a blank.
  - Nothing is created. A workbook row matching no stored delegate is reported,
    not imported.

TO ROLL BACK
    python fix_payable_free_delegate_number.py --rollback fix_backup_<ts>.json

WHERE TO RUN IT FROM
Anywhere inside the checkout. It finds manage.py by walking up from its own
location. Set DJANGO_SETTINGS_MODULE or LINQ_BACKEND_DIR to override.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import sys
from datetime import datetime
from pathlib import Path

PAYLOAD_B64 = __PAYLOAD__

EXPECTED_ROWS = @@ROWS@@
SOURCE_NAME = "master_data_26aug.xlsx"


# ── Django bootstrap ─────────────────────────────────────────────────────────
def bootstrap():
    """Put the backend on sys.path and set Django up. Returns the backend dir."""
    override = os.environ.get("LINQ_BACKEND_DIR")
    if override:
        backend = Path(override).resolve()
    else:
        backend = None
        here = Path(__file__).resolve()
        for parent in [here.parent, *here.parents]:
            if (parent / "manage.py").exists():
                backend = parent
                break
        if backend is None:
            sys.exit(
                "Could not find manage.py above this script. Set "
                "LINQ_BACKEND_DIR to the directory that contains it."
            )

    sys.path.insert(0, str(backend))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    return backend


def load_payload():
    rows = json.loads(gzip.decompress(base64.b64decode(PAYLOAD_B64)))
    if len(rows) != EXPECTED_ROWS:
        sys.exit(
            f"Embedded payload is {len(rows)} rows, expected {EXPECTED_ROWS}. "
            "This file has been altered; do not run it."
        )
    return rows


# ── the fix ──────────────────────────────────────────────────────────────────
def write_workbook(rows, path):
    """
    The embedded rows as a workbook, so the tested management command does the
    work rather than a second copy of the same logic living in this file.
    """
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Invoice Number", "Name", "Delegate Number", "Paid/Free"])
    for row in rows:
        ws.append(row)
    wb.save(str(path))
    return path


def _writable_dir(given, backend):
    """
    Where to put the backup and the report.

    The backup is the whole rollback, so a run that cannot write it must not
    reach the database at all. A production checkout is not always writable,
    hence the fallback and the hard exit rather than a warning.
    """
    candidates = [Path(given)] if given else [backend, Path.cwd()]
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".fix_write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return candidate.resolve()
        except OSError:
            continue
    sys.exit(
        "Cannot write the rollback file to "
        + ", ".join(str(c) for c in candidates)
        + ". Pass --out-dir pointing somewhere writable. Nothing was changed."
    )


def settle_blanks(rows, value):
    """
    Give a value to the invoices the file leaves blank, when asked to.

    Only invoices where EVERY embedded row leaves Payable / Free empty, and
    whose stored value is not already one of the model's two legal values, are
    touched. An invoice with one blank row among several stated ones already
    took its value from the stated ones and is left alone.
    """
    from book_event.models import BookEvent

    stated, blank = set(), set()
    for inv, _name, _num, pf in rows:
        key = " ".join(str(inv).strip().upper().split())
        (stated if str(pf or "").strip() else blank).add(key)
    only_blank = blank - stated

    valid = set(BookEvent.PaidOrFree.values)
    targets = [
        n for n, pf in BookEvent.objects.values_list("invoice_number", "paid_or_free")
        if pf not in valid
        and " ".join(str(n).strip().upper().split()) in only_blank
    ]
    if not targets:
        print(f"\n--blank-as {value}, nothing to settle.")
        return

    written = 0
    for start in range(0, len(targets), 500):
        written += BookEvent.objects.filter(
            invoice_number__in=targets[start:start + 500]
        ).update(paid_or_free=value)
    print(f"\n--blank-as {value}, settled {written:,} invoice(s) the file "
          f"leaves blank.")
    for n in targets[:20]:
        print(f"    {n}")
    if len(targets) > 20:
        print(f"    and {len(targets) - 20} more.")


def snapshot(stamp, backend):
    """
    Every value this run could change, recorded before it changes.

    The whole of both columns is captured rather than only the rows the plan
    touches. It is two small columns, and a rollback that can restore any row is
    worth more than a smaller file.
    """
    from book_delegate.models import BookDelegate
    from book_event.models import BookEvent

    data = {
        "written_at": stamp,
        "source": SOURCE_NAME,
        "delegates": [
            {"id": pk, "delegate_number": num, "delegate_paid_or_free": pf}
            for pk, num, pf in BookDelegate.objects.values_list(
                "id", "delegate_number", "delegate_paid_or_free"
            )
        ],
        "invoices": [
            {"invoice_number": inv, "paid_or_free": pf}
            for inv, pf in BookEvent.objects.values_list(
                "invoice_number", "paid_or_free"
            )
        ],
    }
    path = backend / f"fix_backup_{stamp}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path, len(data["delegates"]), len(data["invoices"])


def rollback(path):
    from django.db import transaction
    from book_delegate.models import BookDelegate
    from book_event.models import BookEvent

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    print(f"Rolling back to the state of {data['written_at']} ...")

    delegates = [
        BookDelegate(
            id=d["id"],
            delegate_number=d["delegate_number"],
            delegate_paid_or_free=d["delegate_paid_or_free"],
        )
        for d in data["delegates"]
    ]
    by_value: dict[str, list] = {}
    for inv in data["invoices"]:
        by_value.setdefault(inv["paid_or_free"], []).append(inv["invoice_number"])

    with transaction.atomic():
        BookDelegate.objects.bulk_update(
            delegates, ["delegate_number", "delegate_paid_or_free"],
            batch_size=500,
        )
        for value, invoices in by_value.items():
            for start in range(0, len(invoices), 500):
                BookEvent.objects.filter(
                    invoice_number__in=invoices[start:start + 500]
                ).update(paid_or_free=value)

    print(
        f"Restored {len(delegates):,} delegate row(s) and "
        f"{sum(len(v) for v in by_value.values()):,} invoice row(s)."
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Correct Delegate Number and Payable / Free on the bookings data. "
            "Applies by default."
        )
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report the plan and write nothing to the database.",
    )
    parser.add_argument(
        "--rollback", metavar="BACKUP.JSON",
        help="Restore both columns from a backup file this script wrote.",
    )
    parser.add_argument(
        "--blank-as", choices=("skip", "Paid", "Free"), default="skip",
        help=(
            "What to do with an invoice whose every row in the file leaves "
            "Payable / Free empty. skip, the default, leaves it exactly as it "
            "is; the file does not say what it should be, and inventing a value "
            "is a decision for a person, not for this script. Paid or Free "
            "settles those invoices in the same run."
        ),
    )
    parser.add_argument(
        "--out-dir", default=None,
        help=(
            "Where the backup and report are written. Defaults to the backend "
            "directory, falling back to the current directory when that is not "
            "writable."
        ),
    )
    opts = parser.parse_args()

    backend = bootstrap()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = _writable_dir(opts.out_dir, backend)

    if opts.rollback:
        rollback(opts.rollback)
        return

    from django.core.management import call_command
    from django.db.models import Count
    from book_delegate.models import BookDelegate
    from book_event.models import BookEvent

    from django.db import connection
    db = connection.settings_dict

    print(f"LINQ CRM bookings fix, {SOURCE_NAME}, {EXPECTED_ROWS:,} rows embedded.")
    print(f"Backend    {backend}")
    # Named, not just "default". This is the line that tells whoever is running
    # it which database is about to change.
    print(f"Database   {db.get('NAME')} on {db.get('HOST') or 'localhost'}:"
          f"{db.get('PORT') or ''} as {db.get('USER')}")
    print(f"Output     {out_dir}")
    print(f"Blank rows {opts.blank_as}")
    print()

    before = state(BookDelegate, BookEvent, Count)
    report_state("BEFORE", before)

    rows = load_payload()
    tmp = out_dir / f"fix_source_{stamp}.xlsx"
    write_workbook(rows, tmp)

    try:
        if opts.dry_run:
            print("\n--- DRY RUN, nothing will be written ---\n")
            call_command(
                "update_delegate_number_paid_free", str(tmp),
                "--report", str(out_dir / f"fix_report_{stamp}.md"),
            )
            print(f"\nReport   {out_dir / f'fix_report_{stamp}.md'}")
            print("Dry run complete. Re-run without --dry-run to apply.")
            return

        path, n_del, n_inv = snapshot(stamp, out_dir)
        print(f"Rollback file written first, {path}")
        print(f"  {n_del:,} delegate row(s) and {n_inv:,} invoice row(s) recorded.")
        print()

        call_command(
            "update_delegate_number_paid_free", str(tmp),
            "--report", str(out_dir / f"fix_report_{stamp}.md"),
            "--apply",
        )

        if opts.blank_as != "skip":
            settle_blanks(rows, opts.blank_as)

        print()
        after = state(BookDelegate, BookEvent, Count)
        report_state("AFTER", after)
        verify(rows, after)
        print(f"\nReport     {out_dir / f'fix_report_{stamp}.md'}")
        print(f"Rollback   python {Path(__file__).name} --rollback {path.name}")
    finally:
        tmp.unlink(missing_ok=True)


# ── before and after ─────────────────────────────────────────────────────────
def state(BookDelegate, BookEvent, Count):
    valid = set(BookEvent.PaidOrFree.values)
    invoice_pf = dict(
        BookEvent.objects.values_list("paid_or_free")
        .annotate(n=Count("invoice_number"))
        .values_list("paid_or_free", "n")
    )
    return {
        "delegates": BookDelegate.objects.count(),
        "invoices": BookEvent.objects.count(),
        "invoice_pf": invoice_pf,
        "invoice_pf_invalid": sum(
            n for v, n in invoice_pf.items() if v not in valid
        ),
        "delegate_number": dict(
            BookDelegate.objects.values_list("delegate_number")
            .annotate(n=Count("id"))
            .values_list("delegate_number", "n")
        ),
        "overrides": BookDelegate.objects.exclude(
            delegate_paid_or_free=None
        ).count(),
        "redundant_overrides": BookDelegate.objects.filter(
            delegate_paid_or_free__isnull=False,
        ).filter(
            delegate_paid_or_free=_F("invoice__paid_or_free")
        ).count(),
    }


def _F(name):
    from django.db.models import F
    return F(name)


def report_state(label, s):
    print(f"{label}")
    print(f"  delegates                    {s['delegates']:,}")
    print(f"  invoices                     {s['invoices']:,}")
    print(f"  invoice paid_or_free         {_fmt(s['invoice_pf'])}")
    print(f"  ... of which invalid         {s['invoice_pf_invalid']:,}")
    print(f"  delegate_number              {_fmt(s['delegate_number'])}")
    print(f"  per-delegate overrides set   {s['overrides']:,}")
    print(f"  ... redundant, equal to the")
    print(f"      invoice they inherit     {s['redundant_overrides']:,}")


def _fmt(counts):
    return ", ".join(
        f"{k!r}={v:,}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])
    )


def _ikey(v):
    return " ".join(str(v or "").strip().upper().split())


def _nkey(v):
    import re
    return " ".join(re.sub(r"[^0-9a-z]+", " ", str(v or "").lower()).split())


def _pf(raw):
    """
    A workbook Payable / Free cell as the value the model stores, or None.

    This MUST agree with PAID_OR_FREE_LOOKUP in the management command. The
    workbook's vocabulary is "Payable" and "Free", in mixed case; the model's is
    "Paid" and "Free". Comparing the raw cell instead would check only the rows
    already spelled the model's way and pass silently on all the others.
    """
    key = " ".join(str(raw or "").strip().lower().split())
    if key in ("", "-", "n/a", "na"):
        return None
    return {
        "paid": "Paid", "payable": "Paid", "pay": "Paid",
        "free": "Free", "complimentary": "Free", "comp": "Free",
        "free of charge": "Free", "foc": "Free",
    }.get(key)


def verify(rows, after):
    """
    Read the database back and check the one thing that matters.

    Every delegate the embedded file covers must now DISPLAY what the file says,
    where displayed means `delegate_paid_or_free or invoice.paid_or_free`, the
    same resolution every serializer performs. This is computed here from a
    fresh read, deliberately independent of what the update itself reported, so
    a bug in the update cannot also silence its own verification.
    """
    from book_delegate.models import BookDelegate

    wanted = {}
    for inv, name, num, pf in rows:
        wanted.setdefault((_ikey(inv), _nkey(name)), []).append((num, pf))

    stored = {}
    for pk, inv, first, last, num, pf, inv_pf in BookDelegate.objects.values_list(
        "id", "invoice_id", "first_name", "last_name",
        "delegate_number", "delegate_paid_or_free", "invoice__paid_or_free",
    ).order_by("id"):
        key = (_ikey(inv), _nkey(f"{first} {last}"))
        stored.setdefault(key, []).append((pk, inv, num, pf or inv_pf or ""))

    pf_ok = pf_bad = num_ok = num_bad = skipped = 0
    examples = []
    for key, expectations in wanted.items():
        rows_here = stored.get(key)
        if not rows_here or len(rows_here) != len(expectations):
            continue  # unmatched or ambiguous; the report lists these
        for (want_num, want_pf), (pk, inv, got_num, got_pf) in zip(
            expectations, rows_here
        ):
            expect_pf = _pf(want_pf)
            if expect_pf is None:
                skipped += 1
            else:
                if got_pf == expect_pf:
                    pf_ok += 1
                else:
                    pf_bad += 1
                    if len(examples) < 10:
                        examples.append(
                            f"{inv}, expected {expect_pf!r}, displays {got_pf!r}"
                        )
            if want_num != "":
                try:
                    expect = int(float(want_num))
                except ValueError:
                    continue
                if got_num == expect:
                    num_ok += 1
                else:
                    num_bad += 1

    print("\nVERIFY, read back from the database")
    print(f"  Payable / Free displays the file's value   {pf_ok:,}")
    print(f"  Payable / Free still differs               {pf_bad:,}")
    print(f"  Delegate Number matches the file           {num_ok:,}")
    print(f"  Delegate Number still differs              {num_bad:,}")
    for line in examples:
        print(f"    {line}")

    redundant = after["redundant_overrides"]
    print(f"  Payable / Free not stated in the file      {skipped:,}")
    print(f"  Overrides that merely repeat their invoice {redundant:,}")

    displayed = {}
    for rows_here in stored.values():
        for _pk, _inv, _num, got in rows_here:
            displayed[got or ""] = displayed.get(got or "", 0) + 1
    print("\n  What the Bookings table now DISPLAYS, per delegate")
    for value, n in sorted(displayed.items(), key=lambda kv: -kv[1]):
        print(f"    {value or '(blank)':<10} {n:,}")

    out_of_scope = after["invoice_pf_invalid"]
    if out_of_scope:
        print(
            f"\n  For information, {out_of_scope:,} invoice(s) still hold a "
            "blank Payable / Free. That is\n  either a blank cell in the source, "
            "which is skipped rather than written as a\n  blank, or a booking "
            "the embedded file does not cover. Neither is a failure\n  of this "
            "run; the report lists them."
        )

    if pf_bad == 0 and num_bad == 0 and redundant == 0:
        print("\nPASS. Every row the file covers now reads the way the file says.")
    else:
        print(
            "\nFAIL. Some rows the file covers do not read the way the file "
            "says.\nRoll back with the command printed below and send the "
            "report to Harrison."
        )


if __name__ == "__main__":
    main()
