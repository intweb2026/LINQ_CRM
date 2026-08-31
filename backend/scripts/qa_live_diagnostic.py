#!/usr/bin/env python
"""
qa_live_diagnostic.py
──────────────────────
READ-ONLY. Measures the state of the stored booking data against the eight
findings of the 26 August review. Runs SELECTs only; there is no write path in
this file and no transaction is opened.

    cd backend
    python scripts/qa_live_diagnostic.py

WHAT IT IS FOR, AND WHAT IT IS NOT
The code fixes stop the importer LOSING these columns from now on. They do not
go back and repair rows that were already damaged — that is a separate job, and
the review's own steps 1 to 3 come before it. This script is how QA tells the two
apart: it says what the data looks like today, so a cell that still reads wrongly
can be attributed to the old import rather than mistaken for a fix that did not
work.

Section 1 answers finding F1, which the review said to settle first because it
changes the shape of the repair. Everything after it is a damage measurement.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.db.models import Count, Q  # noqa: E402

from book_delegate.models import BookDelegate  # noqa: E402
from book_event.models import BookEvent  # noqa: E402


def rule(title):
    print()
    print(f"-- {title} " + "-" * max(0, 62 - len(title)))


def line(label, value, note=""):
    shown = f"{value:,}" if isinstance(value, int) else str(value)
    print(f"  {label:46} {shown:>10}" + (f"   {note}" if note else ""))


def main():
    invoices = BookEvent.objects.count()
    delegates = BookDelegate.objects.count()
    print("LINQ CRM -- booking data diagnostic (read-only)")
    line("invoices", invoices)
    line("delegates", delegates)
    if not delegates:
        print("\n  No booking data stored. Nothing to measure.")
        return 0

    # ── 1. F1, the question the review said to answer first ──────────────────
    rule("1. F1  did Delegate Email and Delegate Company land?")
    blank_email = BookDelegate.objects.filter(Q(email="") | Q(email__isnull=True)).count()
    blank_company = BookDelegate.objects.filter(company_name_raw="").count()
    placeholder = BookDelegate.objects.filter(email__endswith="@import.local").count()
    line("delegates with NO email", blank_email,
         "<-- identity key missing" if blank_email else "good")
    line("delegates with NO company", blank_company,
         "<-- Delegate Company did not land" if blank_company else "good")
    line("delegates on a dup-xxxxxxxx@import.local address", placeholder,
         "<-- created by the collision F1 describes" if placeholder else "good")
    print()
    if blank_email or placeholder:
        print("  READ THIS AS: the Delegate Email column did not map on the import that")
        print("  wrote these rows, so they were deduplicated on invoice number plus an")
        print("  empty string. Matching them back to the file needs Invoice Number plus")
        print("  Name, not email. The header now maps, so new imports are unaffected.")
    else:
        print("  READ THIS AS: the identity key is present, so every stored delegate can")
        print("  be matched back to its file row on Invoice Number plus Email.")

    # ── 2. Payable/Free, the headline finding ────────────────────────────────
    rule("2. F2/F8  Payable/Free as it is stored today")
    for value, count in (BookEvent.objects
                         .values_list("paid_or_free")
                         .annotate(n=Count("pk")).order_by("-n")):
        line(f"invoices with paid_or_free = {value!r}", count)
    print()
    for value, count in (BookDelegate.objects
                         .values_list("delegate_paid_or_free")
                         .annotate(n=Count("pk")).order_by("-n")):
        label = "inherits the invoice" if value is None else repr(value)
        line(f"delegates whose override is {label}", count)
    print()
    blank_pof = BookEvent.objects.filter(paid_or_free="").count()
    if blank_pof:
        line("invoices with NO Payable/Free at all", blank_pof,
             "<-- reads blank in the Bookings table")
        print("  READ THIS AS: the word 'Payable' was rejected value by value on import.")
        print("  It is accepted now, so a re-import of the same file stores it -- but these")
        print("  stored rows stay blank until the repair runs.")

    # ── 3. Mixed invoices, which the flattening made unrepresentable ─────────
    rule("3. F3  invoices whose delegates disagree")
    # Compared on the RESOLVED value, `delegate_x or invoice.x`, which is what
    # every serializer returns and what the Bookings table renders. Comparing the
    # raw override column instead would count "one delegate has an override and
    # the other inherits" as a disagreement when both resolve to the same value,
    # which is not a mixed invoice at all.
    # (delegate column, invoice column, label, what the review measured in the
    # file). The expected figure is the number of invoices the 26 August file
    # states a per-delegate difference for; the stored figure is how many still
    # carry one. The gap is what the flattening destroyed and what a repair would
    # have to restore.
    COLUMNS = (
        ("delegate_paid_or_free",   "invoice__paid_or_free",   "Payable / Free", 903),
        ("booking_code",            None,                      "Booking Code",   868),
        ("delegate_payment_type",   "invoice__payment_type",   "Payment Type",   None),
        ("delegate_ticket_tier",    "invoice__ticket_tier",    "Ticket Tier",    None),
        ("delegate_payment_date",   "invoice__payment_date",   "Payment Date",   None),
        ("delegate_payment_status", "invoice__payment_status", "Payment Status", None),
        ("discount",                None,                      "Discount",       None),
    )

    multi = list(BookDelegate.objects.values("invoice_id")
                 .annotate(n=Count("pk")).filter(n__gt=1)
                 .values_list("invoice_id", flat=True))
    line("invoices with more than one delegate", len(multi))
    print()
    print(f"  {'column':22} {'invoices differ':>16} {'file stated':>13}")
    print(f"  {'-' * 22} {'-' * 16} {'-' * 13}")

    for delegate_col, invoice_col, label, expected in COLUMNS:
        fields = ["invoice_id", delegate_col] + ([invoice_col] if invoice_col else [])
        differ = 0
        for start in range(0, len(multi), 2000):
            chunk = multi[start:start + 2000]
            seen: dict[str, set] = {}
            for values in (BookDelegate.objects.filter(invoice_id__in=chunk)
                           .values_list(*fields)):
                inv, own = values[0], values[1]
                resolved = own if own not in (None, "") else (
                    values[2] if invoice_col else None)
                seen.setdefault(inv, set()).add(resolved if resolved is not None else "")
            differ += sum(1 for v in seen.values() if len(v) > 1)
        gap = ""
        if expected is not None:
            gap = "  <-- still flattened" if differ < expected * 0.5 else "  looks restored"
        print(f"  {label:22} {differ:>16,} {(expected if expected else '-'):>13}{gap}")

    print()
    print("  READ THIS AS: the right-hand column is what the FILE says, measured by the")
    print("  review. Where 'invoices differ' is far below it, the stored data is still")
    print("  flattened -- one row's value was applied to everybody and the difference the")
    print("  file stated is gone. It cannot come back on its own; only a repair restores")
    print("  it. What the code fix guarantees is that a mixed invoice imported FROM NOW")
    print("  ON keeps every delegate's own value, which the wizard walkthrough in the QA")
    print("  plan checks directly. A dash means the review published a row count rather")
    print("  than an invoice count for that column, so there is no invoice figure to")
    print("  compare against -- judge those by the walkthrough, not by this number.")

    # ── 4. The silent defaults ───────────────────────────────────────────────
    rule("4. F5/F6/F7  the columns that were defaulted")
    zero_discount = BookDelegate.objects.filter(discount=0).count()
    line("delegates with a discount of exactly 0", zero_discount,
         "some of these were '20%' in the file")
    for value, count in (BookDelegate.objects.values_list("delegate_count")
                         .annotate(n=Count("pk")).order_by("-n")):
        line(f"delegates with delegate_count = {value}", count)
    print()
    for value, count in (BookDelegate.objects.values_list("attendance")
                         .annotate(n=Count("pk")).order_by("-n")):
        line(f"delegates with attendance = {value!r}", count)

    # ── 5. Attribution, which fix 6 adds ─────────────────────────────────────
    rule("5. F6/fix 6  can a row be traced to the import that wrote it?")
    inv_no_batch = BookEvent.objects.filter(import_batch_id__isnull=True).count()
    del_no_batch = BookDelegate.objects.filter(import_batch_id__isnull=True).count()
    line("invoices with NO import_batch_id", inv_no_batch,
         "<-- unattributable" if inv_no_batch else "")
    line("delegates with NO import_batch_id", del_no_batch,
         "<-- unattributable" if del_no_batch else "")
    batches = (BookEvent.objects.exclude(import_batch_id__isnull=True)
               .values_list("import_batch_id").annotate(n=Count("pk")).order_by("-n")[:10])
    if batches:
        print()
        print("  Known import batches, largest first:")
        for batch, count in batches:
            print(f"      {batch}   {count:,} invoice(s)")
    print()
    print("  READ THIS AS: rows written BEFORE fix 6 carry no batch id and never will;")
    print("  that is the reason scoping the 26 August clean-up is awkward. Every import")
    print("  from now on is listable from its id alone. Confirm by running one import")
    print("  through the wizard and querying the reference it shows on the done screen.")

    print()
    print("No writes were issued. This script contains no write path.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
