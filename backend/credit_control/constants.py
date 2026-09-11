"""
credit_control/constants.py
────────────────────────────
The rules of the module, in one file, so a policy change is a diff here rather
than a hunt through the engine.

Everything in this file was a constant in the Google Apps Script build that
preceded this module, and the names are kept close to that build's names on
purpose: the sheet is the spec people already argued over, and a reader holding
both should be able to see they say the same thing.
"""
from datetime import timedelta

# The verdicts that withdraw an edition. Imported rather than repeated: the same
# three stop the Mining Matrix planning miners against it, and a second literal
# here is how two pages drift until one excludes a cancelled event and the other
# quietly does not. See events/verdicts.py.
from events.verdicts import EXCLUDED_VERDICTS  # noqa: F401

# ── Which events are chased ────────────────────────────────────────────────
#
# The Performance Matrix verdict decides. There is no point spending a shift
# chasing money for an edition that is not happening, and TBP means the
# decision has not been taken yet, so it waits with the others.
#
# Read LIVE on every refresh rather than copied onto the lead, so a verdict
# changed this morning drops or restores its invoices this evening. The four
# that stay in are Standby, Going Ahead, Needs a push and Full Efforts Req.
# The set itself is imported at the top of this file.

# ── Which bookings are chased ──────────────────────────────────────────────
#
# Everyone except SpEx, which has its own tab and its own process. Matched as
# lowercase substrings against the booking code, which against the 22 canonical
# codes in book_event/booking_code_canonical.py routes twelve away: GLD, PLT,
# PTN and SLV SpEx, the four "Speaker / X SpEx" combos, the three "Upgraded to
# X SpEx", and Speaker Table. The other ten are chased.
#
# Substring rather than an exact list because the canonical list grows: a new
# "XYZ SpEx" tier should leave the calling flow the day it is added, without
# anybody remembering to edit this file.
SPEX_BOOKING_MATCH = ("spex", "speaker table")

# Which bookings the invoice-type classifier has an opinion about. A speaker
# invoice is the only kind that can be blind, because a delegate always booked
# something. "spp" is the speaker package code.
SPEAKER_BOOKING_MATCH = ("speaker", "spp")

# Add-ons, for the aged debtor breakdown only. Tested BEFORE the speaker test,
# so a code that says add-on is never counted as a speaker there.
ADDONS_BOOKING_MATCH = ("add-on", "addon", "add on")

# ── The clock ──────────────────────────────────────────────────────────────
#
# Ageing runs from the invoice date. There is deliberately no due-date option:
# book_events.payment_due_date was dropped by migration 0027, so "overdue" can
# only mean days since the invoice was raised, and pretending otherwise would
# need a payment-terms concept the CRM does not have.
HANDOFF_AFTER_DAYS = 4

# The two ageing columns on the dashboard, in hours from 00:00 UTC on the
# invoice day, because an invoice date carries no time of day. Nested: past 72h
# is a subset of past 36h, which is a subset of the total.
AGE_HOUR_THRESHOLDS = (36, 72)

# Aged debtor buckets, in whole days, contiguous and exhaustive. The last is
# open-ended.
AGE_BUCKETS = ((0, 7), (8, 15), (16, 21), (22, 30), (31, 60), (61, 90), (91, None))

# ── The shift ──────────────────────────────────────────────────────────────
#
# The callers work 6:30pm to 3:30am IST. In UTC that is 13:00 to 22:00, so a
# shift that crosses midnight in IST sits entirely inside ONE UTC day. That is
# why there is no cutoff-hour constant here and no timezone conversion in the
# bucketing: the shift day IS the UTC date, which also equals the IST evening
# the shift began. Times are rendered to the callers in IST by the frontend.
#
# The predecessor build forced PST and carried a 4:30am cutoff to stitch the
# two halves of a night together. It only worked because it had the shift's
# timezone wrong in a way that happened not to matter, and it would have broken
# twice a year on North American daylight saving.
DISPLAY_TZ = "Asia/Kolkata"
SHIFT_START_UTC_HOUR = 13
SHIFT_END_UTC_HOUR = 22

# How many shift days the dashboard matrix shows.
ACTIVITY_DAYS = 7

# ── Dispositions ───────────────────────────────────────────────────────────
#
# Two independent classifications hang off one disposition, and keeping them
# apart is the point.
#
#   CATEGORY drives the ENGINE. Reached and Resolved both mean a human
#   conversation happened, and a lead that has been reached stays with whoever
#   reached it, permanently. Not Reached means the phone did not connect, and
#   those are what the day-4 handoff moves.
#
#   STATUS GROUP drives the DASHBOARD and nothing else. It exists because
#   "Reached" is too coarse to run a shift from: a callback request and a
#   dispute are both Reached, and a dead IVR and a voicemail are both Not
#   Reached, while a manager wants those told apart.
CATEGORY_REACHED = "Reached"
CATEGORY_RESOLVED = "Resolved"
CATEGORY_NOT_REACHED = "Not Reached"
CATEGORIES = (CATEGORY_REACHED, CATEGORY_RESOLVED, CATEGORY_NOT_REACHED)

GROUP_NOT_ATTEMPTED = "Not Attempted"
GROUP_ATTEMPTED = "Attempted"
GROUP_ONGOING = "Ongoing"
GROUP_TECHNICAL = "Technical Issues"
STATUS_GROUPS = (GROUP_NOT_ATTEMPTED, GROUP_ATTEMPTED, GROUP_ONGOING, GROUP_TECHNICAL)

# The groups that pin a lead to whoever holds it, permanently.
#
# STATUS GROUP AND NOT CATEGORY, and the difference will matter later even
# though it does not today. In the seed list every Ongoing disposition happens
# to be Reached or Resolved, so the two tests currently select the same rows.
# They are not the same rule: Ongoing means a case is in flight with this
# caller, which is the thing that must not be handed to somebody else, whereas
# Category answers the narrower question of whether the phone connected. The
# day somebody adds a disposition that reached the contact but leaves nothing in
# flight, the group is the answer that stays right.
#
# Named here rather than tested inline because the engine, the dashboard and
# the tests all ask this question and must never disagree.
STICKY_STATUS_GROUPS = frozenset({GROUP_ONGOING})

# The seed list, in display order. Written to the database once, then owned by
# whoever edits the Dispositions screen; this list is not consulted again
# except to seed a fresh install.
#
# GROUP_NOT_ATTEMPTED never appears here. It is what a lead with no disposition
# at all reports as, so no row can carry it.
DISPOSITION_SEED = (
    ("VM", CATEGORY_NOT_REACHED, GROUP_ATTEMPTED),
    ("OOO", CATEGORY_NOT_REACHED, GROUP_ATTEMPTED),
    ("Ringing", CATEGORY_NOT_REACHED, GROUP_ATTEMPTED),
    ("Callback Requested", CATEGORY_REACHED, GROUP_ONGOING),
    ("To Pay - Committed a Date", CATEGORY_REACHED, GROUP_ONGOING),
    ("To Pay - Will pay soon", CATEGORY_REACHED, GROUP_ONGOING),
    ("To Pay - With accounts team", CATEGORY_REACHED, GROUP_ONGOING),
    ("To Pay - Already paid", CATEGORY_RESOLVED, GROUP_ONGOING),
    ("Already in communication with the team", CATEGORY_REACHED, GROUP_ONGOING),
    ("Disputing Invoice", CATEGORY_REACHED, GROUP_ONGOING),
    ("NC - Incorrect Number", CATEGORY_NOT_REACHED, GROUP_TECHNICAL),
    ("NC - No Number", CATEGORY_NOT_REACHED, GROUP_TECHNICAL),
    ("NC - IVR Dead End", CATEGORY_NOT_REACHED, GROUP_TECHNICAL),
    ("NC - HubSpot Restrictions", CATEGORY_NOT_REACHED, GROUP_TECHNICAL),
    ("Out of Time Zone", CATEGORY_NOT_REACHED, GROUP_TECHNICAL),
)

# Values that used to exist and no longer do, mapped to what they became. A
# stored disposition is migrated on read, so a lead worked last month still
# reports a live value and still counts in the right group.
#
# "To Pay - Already paid" is emphatically NOT here. It records that somebody
# SAID they had paid, which is not the same as the source confirming payment,
# so it stays an active, sticky lead until Date Paid appears or the status
# leaves Pending.
RETIRED_DISPOSITIONS = {
    "Out of TimeZone": "Out of Time Zone",
    "Not Interested": "Disputing Invoice",
}

# ── Invoice type ───────────────────────────────────────────────────────────
INVOICE_TYPE_BLIND = "Blind Invoice"
INVOICE_TYPE_REQUESTED = "Requested"

# How sure the classifier has to be before its verdict is stored. Below this a
# speaker row stays unclassified and visible, which is the honest outcome and
# the reason Unclassified is a row of its own on the dashboard rather than
# something folded into Delegates.
CLASSIFIER_MIN_CONFIDENCE = 0.75

# The rubric's version. Bumping it is what makes already-classified rows
# eligible for reclassification; a verdict records the version that produced
# it, so a rubric change is auditable rather than silent.
CLASSIFIER_PROMPT_VERSION = "v2"

# Per-run ceilings, so one command cannot spend an afternoon or a fortune.
CLASSIFIER_MAX_PER_RUN = 60
CLASSIFIER_EVIDENCE_EMAILS = 10
CLASSIFIER_EVIDENCE_CHARS = 1200

# ── Freshness ──────────────────────────────────────────────────────────────
#
# How stale a refresh may be before the UI says so. Not a cache TTL; the engine
# runs on cron and this is only what the page prints beside the timestamp.
REFRESH_STALE_AFTER = timedelta(hours=12)
