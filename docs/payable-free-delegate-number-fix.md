# Payable / Free and Delegate Number, production fix

Owner, Harrison Peck. Written 2026-08-27, revised 2026-08-28, against branch
`main`.

Production runs one file, `backend/scripts/fix_payable_free_delegate_number.py`,
with no arguments. The whole sequence, apply, verify and rollback, has been
rehearsed locally against the full dataset.

Source of truth, `Master Data to Fancy Google Sheet 26 Aug (1).xlsx`, kept in
the repo as `backend/data_imports/master_data_26aug.xlsx`. It carries 15,180
rows across 11,288 invoices, an exact row-for-row match to the database, and
every one of its rows matched a stored delegate.

---

## 1. What was wrong

Two columns in the Bookings table did not hold what the source workbook says,
for two different reasons.

**Payable / Free.** The value the CRM displays is resolved as
`BookDelegate.delegate_paid_or_free or BookEvent.paid_or_free`. Before the fix
neither column carried a usable value for most bookings.

| Stored `BookEvent.paid_or_free` | Invoices |
|---|---|
| `''`, empty string | 8,876 |
| `Free` | 2,412 |
| `Paid` | **0** |

`BookEvent.PaidOrFree` declares `Paid` and `Free` as its only valid values, so
8,876 invoices held a value the model does not allow, and the Payable / Free
cell rendered blank for every delegate on them. The per-delegate override was
`NULL` on all 15,180 delegates, so nothing rescued the display.

**Delegate Number.** `BookDelegate.delegate_number` was `1` on all 15,180 rows,
which is the model default. The workbook carries `0` on 4,636 of them.
`delegate_count` was also `1` on all 15,180 rows, so the value was not hiding in
the sibling column either.

---

## 2. Why it happened

The two faults have separate causes. Both come down to the workbook and the
model not agreeing on a name.

**Payable / Free, a value vocabulary mismatch.** The workbook's column is spelled
`Payable/Free` and its values read `Payable` and `Free`. The model's vocabulary
is `Paid` and `Free`. `Free` is a legal value and was stored intact; `Payable` is
not, and was stored as the empty string. The arithmetic proves it. Taking the
first row of each invoice, the workbook holds `Payable` on 8,871 invoices, `Free`
on 2,412 and blank on 5. The database held `''` on 8,876 and `Free` on exactly
2,412, and 8,871 + 5 = 8,876. Every `Free` survived and every `Payable` was
discarded.

**Delegate Number, a header mismatch.** The workbook spells the 0/1 flag
`Delegate Count`. Every importer here reads it as
`row.get("Delegate Number", "")`, so the column was absent as far as the import
was concerned and every row took the model default of 1. A blank column and an
absent column are indistinguishable once stored, and the run reported success.

One aggravating factor sits behind both. `bulk_create` does not enforce
`choices`, so an illegal `''` was stored in a column whose only legal values are
`Paid` and `Free`, and nothing complained.

For the record, an earlier draft of this document attributed the Payable / Free
fault to the same header mismatch as Delegate Number. The per-invoice arithmetic
above shows that column was in fact read; the fault is the value vocabulary, not
the header.

---

## 3. What changed in the code

**`backend/book_event/management/commands/update_delegate_number_paid_free.py`**,
new. Reconciles the two columns from an Excel workbook, matching on Invoice
Number plus Name. Dry run by default. It accepts both header spellings and
prints which column it took each field from on every run, so a mapping is never
a silent guess. It normalises the vocabulary, `Payable` means `Paid` and a
lower-case `free` means `Free`. Writes through `bulk_update`, so
`BookDelegate.save()` never runs and cannot rewrite `event_code`,
`booking_code`, `booked_on`, `delegate_count` or the accounts contact as a side
effect. Leaves `updated_at` alone unless asked, so a bulk correction does not
reshuffle a table sorted on last modified.

**`backend/book_event/management/commands/import_booking_excel.py`**, guarded. A
mapped column that is absent is now a `CommandError` naming the column and
listing the headers found, and nothing is written; `--allow-missing-columns` is
the deliberate escape hatch. A `Paid/Free` value outside `Paid` and `Free` is
reported with a count, in the console and in `import_issues.md`. Either guard on
its own would have caught this.

**`backend/scripts/fix_payable_free_delegate_number.py`**, the one file
production runs. It carries the source rows inside itself, writes its own
rollback file before touching anything, calls the tested command above rather
than reimplementing it, and then verifies the result from an independent read of
the database. **`backend/scripts/build_fix_script.py`** regenerates it from any
newer export.

**Tests.** `book_delegate.tests_update_delegate_number_paid_free`, 26 tests;
`book_event.tests_import_booking_excel_columns`, 8 tests. Both pass, along with
`book_event.tests_import_schema`, `book_event.tests_load_zoho_export` and
`book_delegate.tests_delegate_number_backfill`, 116 tests together.

---

## 4. How Payable / Free is written, and why it matters

Payable / Free lives in two columns, and only their combination is the answer.
Writing one and not the other produces a booking that answers the question
differently depending on who asks; the Bookings table reads the resolved value,
while `BookEventFilter`, the read-only `paid_or_free` serializer field, the sync
export and the parent half of the bulk-update spec all read the invoice column
directly.

The command therefore defaults to `--paid-free-target sync`, which is the rule
the CRM's own booking modal already follows, stated in
`frontend/src/api/bookings.js`.

- Where every delegate on an invoice agrees, the value goes on the **invoice**
  and the per-delegate overrides are cleared.
- An override survives only to carry a genuine per-delegate difference. After
  this run 953 overrides exist, and none of them merely repeats its invoice.
- An invoice is moved only when the workbook accounts for **every** delegate on
  it. This workbook accounts for all of them, so that guard never had to fire.

---

## 5. Run it in production

One file. No workbook to copy to the server, no flags, nothing to configure. The
15,180 source rows are gzipped and embedded in the script itself.

```bash
cd backend
python scripts/fix_payable_free_delegate_number.py
```

The first thing it prints is the database it is about to change, by name, host
and user. Read that line before letting it continue.

```
Database   linq_crm on 127.0.0.1:5432 as postgres
Output     /path/to/backend
Blank rows skip
```

It applies by default, which is what it is for. In order it

1. prints the BEFORE state of both columns;
2. writes `fix_backup_<timestamp>.json`, the full previous value of both columns
   for every row, before touching anything;
3. applies the change inside one transaction;
4. writes `fix_report_<timestamp>.md`, every change and every row it could not
   place;
5. reads the database back and verifies, independently of what the update
   reported, that every delegate the file covers now displays what the file
   says.

If the backup cannot be written, it exits before touching the database rather
than warning and carrying on. `--out-dir` puts the backup and report somewhere
else when the checkout is not writable.

It is idempotent. A second run reports `Nothing to write` and changes nothing.
Pass `--dry-run` first if you want to see the plan without writing.

### 5.1 What a good run looks like

Measured on a full local rehearsal against 15,180 delegates and 11,288 invoices.

```
  15,180 data rows read.
  Invoice number   <- column 1, 'Invoice Number'
  Name             <- column 2, 'Name'
  Delegate Number  <- column 3, 'Delegate Number'
  Payable / Free   <- column 4, 'Paid/Free'
  15,180 stored delegates indexed.

Matched on invoice plus name : 15,180
Unmatched workbook rows      : 0
Ambiguous workbook rows      : 0

AFTER
  invoice paid_or_free         'Paid'=8,665, 'Free'=2,618, ''=5
  delegate_number              1=10,544, 0=4,636
  per-delegate overrides set   953
  ... redundant                0

VERIFY, read back from the database
  Payable / Free displays the file's value   15,175
  Payable / Free still differs               0
  Delegate Number matches the file           15,180
  Delegate Number still differs              0
  Payable / Free not stated in the file      5
  Overrides that merely repeat their invoice 0

  What the Bookings table now DISPLAYS, per delegate
    Paid       11,205
    Free       3,970
    (blank)    5

PASS. Every row the file covers now reads the way the file says.
```

### 5.2 The 5 rows the file leaves blank

15,175 of the 15,180 rows state a Payable / Free value; 5 leave the cell empty,
and each of those 5 is the only delegate on its invoice, so there is no sibling
row to inherit from. A blank cell is skipped rather than written as a blank, so
by default those 5 invoices keep the empty value they already hold. That is not
a legal value for the column, so the Bookings table shows 5 blank cells.

| Invoice | Delegate | Payment Status |
|---|---|---|
| `SFIL27CHI-6824` | Paige Sawyer | Pending |
| `SFIL27CHI-6823` | Jen Cote | Pending |
| `BIU27CAL-1455` | Rahul Tholath Mathew | Pending |
| `BIU27CAL-1456` | Christian Alexander Mayer | Pending |
| `Inv-19836` | Christy Spackman | Paid (Transferred) |

To settle them in the same run, name the value.

```bash
python scripts/fix_payable_free_delegate_number.py --blank-as Paid
```

This touches only invoices where EVERY row in the file leaves the cell empty and
whose stored value is not already `Paid` or `Free`. An invoice with one blank row
among several stated ones already took its value from the stated ones and is left
alone. The default is `skip`, because the file does not say what these should be
and inventing a value is a decision for a person.

Local was run with `--blank-as Paid`, so the local database now holds zero
illegal values, `Paid` on 8,670 invoices and `Free` on 2,618. Confirm that is the
right call for these 5 before doing the same in production.

### 5.3 Rollback

```bash
python scripts/fix_payable_free_delegate_number.py --rollback fix_backup_<timestamp>.json
```

Rehearsed locally. It restored all 15,180 delegate rows and 11,288 invoice rows
to the exact pre-fix state, `''` on 8,876 invoices and `delegate_number` of 1
everywhere, and the fix then re-applied cleanly.

### 5.4 Regenerating the file for a different export

```bash
python scripts/build_fix_script.py <new_export.xlsx> \
    scripts/fix_payable_free_delegate_number.py
```

The generator reads Invoice Number, Name, and the Delegate Number and
Payable / Free columns under either spelling, and embeds them. It prints which
pair it used. Everything else in the workbook is ignored.

---

## 6. One figure to confirm

The run agrees with the workbook exactly, and the workbook is internally
consistent, 11,205 + 3,970 + 5 = 15,180 and 4,636 + 10,544 = 15,180. Two figures
quoted separately during review differ slightly.

| | This export | Quoted | Delta |
|---|---|---|---|
| Payable, displayed as Paid | 11,205 | 11,163 | 42 |
| Free | 3,970 | 3,970 | 0 |
| Blank | 5 | 5 | 0 |
| `delegate_number` of 0 | 4,636 | 4,635 | 1 |

Free and blank agree exactly. The 42 is not a payment-status filter; no status
subset of that size exists in the file. The likeliest explanation is that the
quoted numbers came from the live Google Sheet, or from
`Event Bookings Report (1).xlsx`, which holds 15,112 rows with 11,162 Paid and
4,632 zeros, rather than from this 15,180-row export.

Confirm which file is authoritative. If it is not this one, regenerate with
section 5.4 and re-run; no code change is needed.

---

## 7. Do not do these

- Do not run `import_booking_excel` to fix this. It wipes every `BookEvent` and
  `BookDelegate` first, discarding every edit made in the CRM since the last
  load.
- Do not run `import_remaining_bookings` to fix Delegate Number. Its default
  sends the column to `delegate_count`, which the UI does not show.
- Do not call the underlying management command by hand with
  `--paid-free-target delegate` or `invoice`. The one-file script uses the
  correct target; section 4 says why the others are wrong here.
- Do not delete `fix_backup_<timestamp>.json` until the result has been checked
  in the app. It is the whole rollback.
- Do not edit the one-file script to change the embedded data. It checks its own
  row count on startup and refuses to run if it has been altered; regenerate it
  with `build_fix_script.py` instead.
