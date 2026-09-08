# Pre-Event Docs — handoff

Written 2026-09-05, at the end of the session that built the module. A new chat
starts with no memory of any of this, so everything needed to continue is here:
what was built, every decision the user made, every bug found, what is still
open, and the traps that cost real time.

Read this first. Then read the module docstrings, which carry the reasoning for
individual rules and are deliberately long.

---

## 1. What this is

The module replaces a Google Sheets workbook, **PRE EVENT DOCS - LinQ**, that
runs one IQ Hub event from booking through the badge table, the check-in desk and
the speed networking session.

Sheet id `14eMXunPQGmOWuLPpfd4Lzrqny4ih28Brz0_fesqZQIE`, owned by
arthur.pina@iq-hub.com, 11 tabs. It **was read in full**, values and formulas,
using the repo's own read-only Sheets service account
(`config/credentials/google-sheets.json`, scope `spreadsheets.readonly`, via
`reports/services/connector.py`). Pass `valueRenderOption=FORMULA` to get the
formulas rather than the rendered values.

**The finding that shaped everything: 12 of the 13 things in that workbook were
already in the CRM.** Muster and SMZoho are the same rows as `book_events` and
`book_delegates`. So this is a projection build, not a data build, and the only
genuinely new state is the badge log and the networking draw.

---

## 2. The four reports, and their exact rules

Names are fixed by the user and must not drift.

| Report | Population | Columns | Sort |
|---|---|---|---|
| **Name Badges** | all bookings, cancelled INCLUDED, TBA excluded | Full Name, Company Name | Company |
| **Check-In Sheet** | filtered, see `at_desk()` | Full Name, Company Name, IN?, Booking Code, Payment Status, Upcoming Events ×3 | Company |
| **Additional Name Badges** | badge list minus cancelled | Full Name, Company Name, Remarks | Company |
| **Speed Networking** | desk population, TBA excluded | Name, Company, Round 1-3 | company grouping |

Plus **Cancellations**, which lives UNDER Name Badges, not on the Additional tab.

### The three memberships, and why they are separate

This is the single most important thing in the module. Getting it wrong caused
the worst bug of the session.

- `on_badge_list(d)` — not TBA. What Name Badges shows and what a freeze captures.
- `needs_a_badge_printed(d)` — on the badge list AND not cancelled. What
  Additional Name Badges is built from.
- `at_desk(d)` — payment status in Paid / Paid (Transferred) / Pending, not
  iQ-Hub, not one of four booking codes. The Check-In Sheet only.

**A diff must be taken against the population that was frozen.** The user froze
Name Badges while the code compared against the Check-In Sheet, and the 27-row
gap between the two populations was reported as change: 31 entries where one
booking had been edited. Pinned by
`test_the_diff_is_against_the_frozen_list_not_the_check_in_sheet`.

### Additional Name Badges lifecycle

1. Fresh event, nothing frozen → **empty**. There is no "since" yet. Pinned by
   `test_the_lifecycle_a_first_time_event_actually_goes_through`.
2. Freeze → still empty.
3. A booking changes in Bookings → that one row appears.

Remarks are `New Badge`, `Name Change`, `Company Change`, `Name & Company
Change`. **`Remove` no longer exists**; removals are Cancellations.

### Cancelled bookings

On Name Badges, off Additional, and **not treated as a cancellation either**.
The user asked to keep cancelled on the badge list, so flagging the same badge
for removal would be a print-it-and-pull-it contradiction. Consequence:
Cancellations only catches a booking that left the badge list entirely, meaning
deleted or reverted to TBA. **This is question 3 in section 8 — the user has not
confirmed they want it this way.**

### TBA

Dropped from Name Badges entirely, not blanked. The workbook blanks the name and
keeps the row; the user asked for it dropped. A placeholder returns as a
`New Badge` through Additional the moment it gets a real name.

Matched as a **whole word at the start** of the name OR the company. Tighter than
any of the workbook's three spellings of this rule, which would each wrongly drop
a real person called Tbarak at Mitbar Holdings.

---

## 3. Files

### Created

```
backend/pre_event_docs/                     the app
  models.py           BadgeIssue, NetworkingPlan
  services.py    839  the queryset, memberships, four projections, change detection
  networking.py       the seating optimiser
  views.py       240  one read endpoint, four writes
  serializers.py  42  NetworkingPlan only; projections are plain dicts
  urls.py         11
  admin.py            read-only, so the badge log can be inspected
  tests_pre_event_docs.py   680  +3 for the seats input
  tests_networking.py       210  +4 for the seats input
  management/commands/import_badge_log.py   189
  migrations/0001_initial.py, 0002_remove_networkingplan_per_table.py

backend/book_delegate/effective.py    58   the delegate-override rule, extracted
backend/accounts/migrations/0032_add_pre_event_docs_module.py

frontend/src/api/preEventDocs.js     107
frontend/src/lib/exportSheet.js      164   xlsx write + print helper; the
                                          delivered layout lives here, section 11
frontend/src/lib/exportSheet.test.js  136  9 tests pinning that layout
frontend/src/pages/PreEventDocsPage.jsx   411
frontend/src/pages/preEventDocs/
  BadgeRunModal.jsx   126  the freeze
  EventPicker.jsx     131  searchable overlay, not a dropdown
  NetworkingTab.jsx   265
  RunHistory.jsx      189  frozen snapshots with drift
```

### Modified

```
backend/config/settings.py        INSTALLED_APPS + 3 PRE_EVENT_DOCS_* settings
backend/config/urls.py            one include
backend/accounts/models.py        CRM_MODULES += pre_event_docs
backend/book_delegate/filters.py  now calls effective.effective_q
backend/book_event/models.py      "Unpaid" removed from PaymentStatus
backend/config/views.py           dead Unpaid dashboard bucket removed
backend/sync/events_sync.py       stopped reading two removed Event fields
frontend/src/App.jsx              lazy import + 2 routes
frontend/src/lib/nav.js           one nav entry
frontend/src/lib/constants.js     CRM_MODULES row (lost to an import once, restored)
frontend/src/index.css            11 CSS map rows
frontend/src/styles/components.css  12 tagged sections
```

---

## 4. API

```
GET    /api/pre-event-docs/events/                 the picker, with name/location/dates
GET    /api/pre-event-docs/docs/?event_code=&edition=   THE WHOLE PAGE, one request
GET    /api/pre-event-docs/run/<run_id>/           one frozen snapshot, row by row
POST   /api/pre-event-docs/badge-run/              freeze a run
DELETE /api/pre-event-docs/badge-run/<run_id>/     undo one
POST   /api/pre-event-docs/draw/                   draw a networking plan
       body: event_code, edition, tables, per_table, rounds
```

`docs` returns everything in one response, costing 5 queries. All actions gate on
the `pre_event_docs` module through `crm_permission`, resolved by
`crm_permissions`' HTTP-method fallback, which is correct for all of them.

---

## 5. Where the badge log lives

**Table** `pre_event_docs_badge_issues`. **Admin** `/admin/pre_event_docs/badgeissue/`,
read-only on purpose. **In the app**, the Frozen badge runs block on the
Additional tab, expandable per run with a drift status per row and a per-run
Excel export.

`BadgeIssue` stores the name and company **as printed**, never re-read from the
delegate; that is the whole basis of the diff. Delegate FK is `SET_NULL` so a
deleted booking still names whose badge to pull.

One `run_id` per freeze. `run_id` has `default=uuid.uuid4`, so **any bulk_create
must pass it explicitly** — not doing so gave 3,423 imported badges 3,423
separate run ids and made the history 177 one-badge runs per event.

### The workbook history is imported

`python manage.py import_badge_log` reads Database_Sent from the sheet directly.
Idempotent. Of 5,257 distinct rows, **3,423 matched a delegate** and were
imported as 65 runs, one per event. Imported runs carry no `issued_by`, which is
what marks them as history rather than something printed here.

The other **1,834 are not imported** and that is deliberate: those events hold
more logged badges than the CRM holds delegates (PSE - MP has 121 against 37), so
they are a CRM data gap rather than cancellations, and importing them would put a
permanent false "Booking removed" on each event. `--include-unmatched` if wanted.

---

## 6. Speed networking

The user's requirements: **best matchup possible, new people every round, three
rounds, completely random, the table count is an input, and so is how many
people sit at each table.**

`build_plan(people, tables=None, per_table=None, rounds=3, attempts=60, seed=None)`.

**TWO INPUTS, BECAUSE A VENUE QUOTES TWO NUMBERS.** `tables` is how many tables
the room has and is honoured exactly. `per_table` is how many people can sit at
one, a MAXIMUM. Added 2026-09-08 on the user's instruction; they had asked for
it, and the first build had only the count.

- `tables` alone, the table size follows from the count.
- `per_table` alone, the count follows, `ceil(attendees / per_table)`, never
  below MIN_TABLES. Rounded UP, not to nearest: the workbook's own suggestion
  rounds, and on 170 people at 6 chairs that gives 28 tables whose largest
  holds seven, one more chair than was asked for.
- BOTH, the count still wins and the seats are checked against it. A room that
  cannot hold the list raises `ValueError` naming both numbers that would work,
  and the view returns that message as a 400 verbatim.
- `per_table` is a CEILING, NOT A TARGET. 45 people over 6 tables sit 8, 8, 8,
  7, 7, 7 whatever ten chairs would allow, because spreading a room over the
  tables it has is what makes people meet, and filling four tables to ten to
  leave two empty is the opposite.

The count being honoured exactly is still the rule, and the two are named
separately for exactly that reason: the single field WAS people-per-table, the
count was derived from it, and asking for 6 produced 28 tables on a 170 person
event. That was the user's first bug report. Two named fields cannot be
confused for one another.

On screen the pair is LINKED THROUGH THE HEAD COUNT, so editing either
recomputes the other and every pair the page can send is a pair that draws.
`docs` sends `networking_attendees` for that, the desk population, so the
arithmetic works before any draw exists. THE OPTIMISER IS UNTOUCHED by all of
it: sizes still come from `table_sizes(attendees, tables)`, so `floor_repeats`
stays correct with no change at all.

Randomised best-of-N greedy fill, then a first-improvement swap repair on every
attempt. Cost weights: a repeat costs more than three colleague clashes, so the
optimiser will seat colleagues together before repeating a pairing.

### The bound, which took two attempts to get right

`floor_repeats()` takes the LARGER of two lower bounds:

- **counting** — rounds × pair slots versus distinct pairs available
- **pigeonhole** — after round one the room is partitioned; a table of `size`
  refilled from `groups` tables must repeat when `size > groups`

The first version had only the counting bound. It said 18 people at tables of six
could be repeat-free, the optimiser kept landing on 18 repeats, and the instinct
was to optimise harder. **The bound was wrong, not the optimiser**: six people
drawn from three earlier tables must include two who already met. Read
`test_the_pigeonhole_bound_is_what_counting_alone_misses`.

`optimal` is `repeats <= floor`. The page must read that flag, never
`repeat_pairs == 0`, because with a fixed table count zero is often unavailable.

### Budgets

`SEAT_BUDGET` and `REPAIR_BUDGET` are charged in pair operations, not attempts or
swaps, because the cost of either scales with table size. Values chosen from a
measurement table recorded in the source. Without them a 400 person draw over 6
tables ran for nine seconds; with them it is under two. A big room of few tables
settles about 7% above its floor as a result, which is a `ponytail:` marker.

---

## 7. Bugs found, and who owned them

### In the workbook, worth telling the user again

- **`Report_CheckIn` never excludes the upgrade lines.** The clause is
  `SMZoho!N:N <> "Upgraded*"`, and Sheets does not expand wildcards in `<>`, so it
  compares against the literal nine characters and matches nothing. Every
  "Upgraded to … SpEx" line has been printing on the real check-in sheet as a
  delegate. Fixed here rather than copied.
- **SMZoho's Direct line column is corrupt**: all 4,333 phone numbers stored as
  `=+33782811518`. The CRM copy is clean, so it dies with the sheet.
- **`Input` was showing a different event.** Zero of its 55 names appear on
  DLG - VV's badge or check-in lists; it is hand-pasted and never cleared when
  Control moved. Immune here, since a draw is keyed on event code.
- **TBA is spelled three different ways** across three tabs, so one delegate can
  be a placeholder on one and a person on another.
- **Muster and SMZoho disagree**: 63 rows versus 75 for DLG - VV. SMZoho has no
  formulas at all; it is a separate paste, and every report tab reads it.

### In the CRM, found while working

- **`sync/events_sync.py` read two fields that `events.0019` removed.** An
  `AttributeError` on the Events sheet push, reachable from Google Sync,
  `sync_to_sheets` and `crm_mirror`. **This would have broken production.** Fixed,
  along with the two matching header entries so the columns stay aligned.
- **`Event.save()` overwrites `name`** with `official_event_name`, or with
  `event_code` when that is blank. Assigning `name` is silently discarded.
- **`Event.save()` copies `location` into city, country AND venue.** All 28 events
  with a city have `city == country`, both holding a full place string, so joining
  them printed it twice.
- **`frontend/src/lib/constants.js` lost its `pre_event_docs` CRM_MODULES row** to
  an import. Backend gate fine, nav fine, routes fine, but the Permissions grid
  rendered no row so the module could never be granted. Silent.

### Mine, for the record

- compared the diff against the wrong population (the 31-entry report)
- people-per-table instead of table count
- badge log with one run id per row
- Additional Name Badges listing everybody on a fresh event
- claimed the check-in tick was wired when only the endpoint existed
- an optimistic tick that closed over stale state inside a `useMemo`
- the seed reproduction bug, and the wrong bound described above

---

## 8. Still open

1. ~~**Demo export file**~~ **DONE 2026-09-08.** The user supplied three real
   files. See section 11; the Excel layout now reproduces two of them cell for
   cell, and the third turned out to be a report this module does not have.
2. **Speed networking population** — never answered, and NOT the same thing as
   the people-per-table field, which is now built. Is networking opt-in? The
   workbook's Input tab settles nothing: 75 badges, 45 at the desk, 55 in Input.
   Proposal was select-then-draw with everything ticked by default.
3. **Should a cancelled-but-already-printed badge appear under Cancellations?**
   Currently no; see section 2. One line either way.
4. **The 1,834 unimported badge rows** — available behind `--include-unmatched`.
5. **The published artifact is badly stale.** It still describes the old design
   with no Additional Name Badges, Cancellations or Freeze. Offered to rewrite,
   not yet done. It is at
   `https://claude.ai/code/artifact/2abf0e7c-47f9-4814-b0ee-47f2eed86843` and the
   source was in the session scratchpad, which will not survive.
6. **Never opened in a browser.** The user caught a real picker bug from a
   screenshot that no test could have caught. This is the highest-value remaining
   check.
7. **The SpEx sponsor sheet cannot be built yet.** It is the third delivered
   file and it needs four things, of which the CRM holds one usable. See
   section 11.
8. **84 of 241 events show only their code**, because the delegates table holds a
   bare family code while the catalogue holds a per-edition one. The user
   **explicitly rejected** family matching, because it means choosing an edition
   on their behalf. `events.0018` has since added a real `base_code` column, which
   would be a non-guessing join; not touched, needs their approval.

### Explicitly closed, do not reopen

- **IN? column stays unwired.** The user has their own check-in function, and
  `PATCH /api/delegates/{id}/update_attendance/` already owns that write.
- **Audit cuts** — `net: -325 lines, -2 deps` repo-wide plus 20 in this module.
  The user said leave them.
- **Permissions** — the user said this is on test, do not worry who sees it.
  Every team is backfilled all-false; only the HP account gets in.

---

## 9. Traps that cost real time

**The test database race.** Another session runs `manage.py test` against this
same checkout. Both default to `test_linq_crm`, so they fight and the loser
reports ~168 `setUpClass` errors that look exactly like a repo-wide regression.
Every affected module passes in isolation. Give your run its own database:

```
DB_NAME=linq_crm_ped python manage.py test --noinput
```

`config.settings` reads `DB_NAME` through decouple, which checks `os.environ`
first, so the source database named need not exist.

**Never pipe a test run through `grep` or `head`.** SIGPIPE kills the run
mid-suite and leaves the test database behind, which then breaks the other
session's run too. Redirect to a file and grep the file.

**The suite is GREEN: 2,312 tests, `OK`.** Any failure is real. The 12-to-15
failures older notes mention are gone; they were source-file-scanning tests that
were correctly reporting conflict markers left in the tree by a `git stash pop`.
If one fails, check for markers before assuming it is stale:

```
grep -rn '^<<<<<<<\|^>>>>>>>' --include=*.js --include=*.jsx --include=*.css frontend/src
```

**Bash heredocs break on apostrophes** in this environment, even quoted ones.
Write a script file with the Write tool and run it.

**The delegate/catalogue event code join.** `BookDelegate.save` strips the
trailing year out of `event_code` into `edition`, so a delegate carries
`("ACU", 2025)` while the catalogue carries `ACU25`. Filtering delegates by a
catalogue code returns nothing, silently.

**Migrations lie about indexes.** Verify against `pg_indexes` rather than
trusting `showmigrations`.

---

## 10. How to verify a change

```
# the module
DB_NAME=linq_crm_ped python manage.py test pre_event_docs --noinput

# everything
DB_NAME=linq_crm_ped python manage.py test --noinput      # expect 2312, OK

# the frontend
cd frontend && npx cross-env INLINE_RUNTIME_CHUNK=false GENERATE_SOURCEMAP=false react-scripts build
```

The session also used a wiring check that resolved every URL the api module
calls and asserted every key the JSX reads exists in the real payload. It found
two genuine breaks that tests and the build both missed. It lived in the
scratchpad and is gone; it is worth rebuilding if this module grows.

53 tests cover the module: the exclusions, TBA, the role column, the effective
payment rule, the full Additional lifecycle, the cancelled asymmetry, the frozen
snapshot, the query count, and the optimiser at every size from 6 to 198.

---

## 11. The three delivered files, 2026-09-08

The user supplied the three workbooks that really go out, which closed open
item 1 and opened a new one. Two days before an event these three are shared;
a frozen badge list goes out 14 days before.

```
WSE 26 - Checkin Sheet.xlsx          the Check-In Sheet, 49 delegates
WSE 26 - Additional Name Badge.xlsx  Additional Name Badges, 7 rows
WSE - SpEx CheckIn Sheet.xlsx        A SPONSOR SHEET THIS MODULE DOES NOT HAVE
```

### The layout, which is now matched exactly

Both delegate files share one shape, and `lib/exportSheet.js` reproduces it:

```
rows 1-2   the title, merged over every column, "WSE - MP 2026: Check-In Sheet"
row 3      Additional only, merged the same way, "TO PRINT (New, Name Changes,
           Company Changes)"
row 4      the headers; on the check-in sheet "Upcoming Events" is merged over
           its three columns with the event codes on the row below
row 5+     the data, sorted by company
```

Verified by feeding each delivered file's own rows back through `buildSheet`
and diffing the result against the file, cell by cell and merge by merge.
**Both came out identical**, `MERGES MATCH` and `CELLS MATCH`, 0 differing.
That harness lived in the scratchpad; `lib/exportSheet.test.js` pins the same
shape permanently in 9 tests.

Three things the diff caught:

- The group label was being written into every cell of its merged range, so
  "Upcoming Events" sat in G3 and H3 underneath the merge. Excel keeps only
  the top-left when a range is merged by hand, and so does the real file.
- **Upcoming Events is always three columns, named or not.** The catalogue
  names none for WSE - MP, and the export was building the columns from that
  list, so the file would have gone out with five columns instead of eight and
  the desk with nowhere to write. Padded to three.
- **The freeze modal was exporting a worse file than the tab's own button.**
  It wrote Name and Company for both run types, so a top-up run reached the
  printer with no Remarks column, meaning nothing said which card was new and
  which replaced one already on the table. The delivered file has it.

### What the data comparison proved

The local snapshot holds 35 delegates for WSE - MP against the sheet's 49, and
only 8 reach `at_desk` because 26 of the 35 are `Cancelled` in this snapshot.
Of the 7 rows both hold, **every field agreed**: company, the role column, and
the payment note. So `role_of` and the Pending-only payment sentence are
confirmed against a real deliverable, and the row-count gap is snapshot age.
None of the 7 Additional names exist locally at all. A production dump would
let the 49 be reconciled end to end; it is the only thing blocking that.

Two details of the real file worth keeping:

- Row 37 is a **blank name with a company and a SpEx code**, so the check-in
  sheet does keep TBA rows with the name cleared. Ours does too, and the
  screen renders "— write at the desk —" there. TBA is dropped from Name
  Badges only, which is the instruction.
- The Additional file is **grouped by remark, not sorted by company**:
  both Company Changes, then the five New Badges, and the New Badges are not
  alphabetical. Ours sorts by company throughout. **Not changed, not asked.**

### The styling ceiling

The delivered files are branded: navy title band, pale blue header, zebra
striped rows, Google Sans, thin borders. **None of that is reproduced**, and
it cannot be with the library in the tree: the community build of `xlsx`
writes values, merges and widths and silently ignores cell styles, which are a
paid feature. The structure is what the desk and the printer read off.

Colour needs one of two things, neither taken without being asked:

- `xlsx-js-style` in place of `xlsx`, a drop-in fork, same API, one more
  megabyte in the bundle unless `lib/importParse.js` moves to it too.
- The Excel write moved to the server, where openpyxl is already a dependency
  and can do all of it. Note that `accounts/spreadsheet_export.py` says in its
  own docstring that Pre-Event Docs writing its workbooks in the BROWSER is a
  deliberate rule, so moving it is a decision, not a refactor. That file is
  also `write_only`, which cannot merge cells, so it is not the vehicle.

### The SpEx sponsor sheet, and why it is blocked

A different report altogether, keyed on **company** rather than delegate:

```
A1 "WSE 26"    C1 "TOTAL TABLES REQUIRED: 2"     both 21pt, boxed
row 3 headers  Company | Level | Agenda Based Addons | Booth Number |
               Additional Requirements
rows 4-6       Ott / Silver / / 1
               Amiblu / Add-on / Speaking Slot / No Booth / Break Sponsor
               Wioniq Benelux B.V / Gold / Speaking Slot / 2 / Late sponsor
row 12         D "3,4,5,6,7,8"  E "TO REMOVE"     spare booths
```

Checked against the database rather than guessed at:

| Column | CRM |
|---|---|
| Company | `company` / `company_name_raw`, fine |
| Level | `book_delegate.sponsorship_level` exists and is **EMPTY IN ALL 11,128 ROWS** |
| Agenda Based Addons | `add_ons` holds **a money amount**, "545.00", not "Speaking Slot" |
| Booth Number | **no column anywhere** |
| Additional Requirements | **no column**; `notes` is the nearest and is empty here |
| TOTAL TABLES REQUIRED | derivable, but only once booth numbers exist |

Two of the three sponsors on that sheet, Ott and Wioniq, have **no delegate
rows for the event locally at all**. So this is not an export layout job; it
needs somewhere to enter sponsorship level, agenda add-on, booth number and
requirements per sponsor per event, which is a data-entry feature and a
decision about where that state lives. Not started, deliberately.

### Also fixed in passing

Two pieces of rot in `PreEventDocsPage.jsx`: the `REMARK_TONE` comment still
described three tones and a removal remark, and the Freeze button was gated on
`additional.some(r => r.remark !== 'Remove')`, a condition that has been
always-true since `Remove` became a cancellation.
