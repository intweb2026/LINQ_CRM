// Ported 1:1 from legacy-vanilla-js/js/01-data.js (domain constants section).

/**
 * The one account the UI treats as a named owner rather than a role.
 *
 * Mirrors dapi_USERNAME in backend/accounts/permissions.py, which IsHPAccount
 * reads. It lives here, in a module with no React and no siblings, because more
 * than one part of the UI now asks the question: the clear-all button on five
 * pages, and the Data API Keys entry in the rail, the command palette and the
 * page itself. It previously lived in ClearAllButton.jsx, which nav.js must not
 * import — nav.js is read outside a component and has to stay React-free.
 *
 * A second copy of this literal would be a second chance to mistype it, and a
 * mistyped copy fails silently in the dangerous direction on the show side: the
 * control appears for someone whose click can only ever answer 403.
 */
export const HP_USERNAME = 'HP';
/**
 * Selectable payment statuses, in the order the Bookings tab presents them.
 *
 * 'Unpaid' and 'Free' were dropped from this list deliberately: no invoice in the
 * database carries 'Unpaid' and exactly one delegate override carries 'Free', so
 * neither is worth offering. Both remain declared on the model
 * (book_event/models.py PaymentStatus) and keep their STATUS_TONE entry below, so
 * the one row holding 'Free' still renders as itself rather than as an unstyled
 * unknown — this list governs what can be CHOSEN, not what can be displayed.
 */
export const PAYMENT_STATUSES = ['Pending', 'Paid', 'Cancelled', 'Refunded', 'Credit Pending (Free)', 'Credit Pending (Paid)', 'Credit Transferred', 'Paid (Transferred)', 'IQ Staff'];
export const STATUS_TONE = { Paid: 'green', Pending: 'amber', Unpaid: 'red', Cancelled: 'red', Refunded: 'slate', Free: 'blue', 'Credit Pending (Free)': 'violet', 'Credit Pending (Paid)': 'violet', 'Credit Transferred': 'cyan', 'Paid (Transferred)': 'green', 'IQ Staff': 'blue' };

/**
 * Booking codes, as a closed list rather than the free text the column used to be.
 *
 * The four entries marked below are not from the requested list — they are values
 * the live data already holds (82 rows between them), and 'Advisory Board Member'
 * is the stored spelling of what the request called "Advisor Board Member".
 * Omitting them would have left those rows unable to round-trip their own value
 * through the editor. Any OTHER off-list value a row happens to hold is appended
 * to its own dropdown at render time (see DelegateTable), so no stored code is
 * ever silently replaced by a blank.
 */
export const BOOKING_CODES = [
  'Add-Ons',
  'Advisory Board Member',        // stored spelling; 6 rows
  'Complimentary',
  'Delegate',
  'GLD SpEx',
  'Group Pass',
  'Media',
  'PLT SpEx',
  'PTN SpEx',
  'SLV SpEx',
  'Speaker',
  'Speaker / GLD SpEx',
  'Speaker / Group Pass',
  'Speaker / PLT SpEx',
  'Speaker / PTN SpEx',           // in data, not in the requested list; 1 row
  'Speaker / SLV SpEx',           // in data, not in the requested list; 73 rows
  'Speaker Table',
  'SPP',
  'SPP / Group Pass',
  'Upgraded to GLD SpEx',         // in data, not in the requested list; 2 rows
  'Upgraded to PLT SpEx',
  'Upgraded to SLV SpEx',
];

/**
 * Which delegate on the invoice this row is. Offered as a picker because the
 * column is an ordinal, not free text — every row in the database currently
 * holds 1. A stored value outside this range is appended to its own dropdown
 * rather than dropped, exactly as with BOOKING_CODES.
 */
export const DELEGATE_NUMBERS = [1, 2, 3, 4];
export const PAYMENT_TYPES = ['Stripe', 'Bank'];
export const TICKET_TIERS = ['SEB', 'EB', 'Regular'];
export const ATTENDANCE = ['Pending', 'Confirmed', 'No-show', 'Cancelled'];
export const ATT_TONE = { Confirmed: 'green', Pending: 'amber', 'No-show': 'red', Cancelled: 'slate' };
/**
 * Discount options, as percentages.
 *
 * The database stores a FRACTION — 0.2 for 20% — on both book_delegates.discount
 * and book_events.discount, and every non-zero value in the export is one of
 * 0.1/0.2/0.25/0.3/0.5, an exact match for this list. api/bookings.js converts in
 * both directions (discountToPercentLabel / discountToFraction); nothing else may
 * assume the stored number is already a percentage.
 */
export const DISCOUNTS = ['0%', '10%', '20%', '25%', '30%', '50%'];
export const EVENT_STATUSES = ['Draft', 'Upcoming', 'Live', 'Completed', 'Cancelled', 'Postponed', 'TBP'];
export const EV_TONE = { Draft: 'neutral', Upcoming: 'blue', Live: 'green', Completed: 'slate', Cancelled: 'red', Postponed: 'amber', TBP: 'violet' };
export const TK_STATUS = { draft: { l: 'Draft', t: 'neutral' }, mr_submitted: { l: 'MR Submitted', t: 'blue' }, completed: { l: 'Completed', t: 'green' }, returned: { l: 'Returned', t: 'red' } };
export const TK_PRIORITY = { AS: 'neutral', AD: 'blue', SPEX: 'violet', DD: 'amber', ASSOC: 'green', MEDIA: 'amber', AB: 'red' };
/**
 * Ticket picklists, as the STORED spellings rather than the model's enum labels.
 *
 * ticket_central.models declares TypeOfTicket as code→label pairs ("BX" → "Blue"),
 * but the column is a plain CharField (D4) and every one of the 35,691 rows in it
 * holds the joined form the Zoho form writes — "Blue - BX", and "Comp.-CX" with no
 * spaces around the dash. extract_type_code() parses the code back out of exactly
 * this shape, so the picker has to offer these strings verbatim: offering "Blue"
 * would write a value no existing row shares and no ticket number could be built
 * from. Counts as of 2026-08-13, most used first.
 *
 * TK_TICKET_TYPES / TK_RELATIONSHIPS are the complete set of non-blank values
 * those two columns hold (Simple 8,509 / Complex 837; Direct 1,567 / Indirect 215).
 * Any off-list value a row already carries is appended to its own dropdown by the
 * form, so nothing stored is ever silently replaced by a blank.
 */
export const TK_TYPES = ['LinkedIn - LX', 'Comp.-CX', 'White - WH', 'Blue - BX', 'Green - GR', 'Yellow - YL', 'Platinum - PX', 'Gold - GX', 'ZID'];
export const TK_TICKET_TYPES = ['Simple', 'Complex'];
export const TK_RELATIONSHIPS = ['Direct', 'Indirect'];
export const WH_STATUS = { received: 'blue', processing: 'amber', success: 'green', failed: 'red', duplicate: 'slate' };
export const TEAM_ROLES = ['admin', 'sales', 'market_research', 'data_mining', 'telemarketing', 'speaker_sales', 'spex', 'operations'];
export const ROLE_LABEL = { admin: 'Admin', sales: 'Sales', market_research: 'MR', data_mining: 'DMD', telemarketing: 'Tele', speaker_sales: 'Spkr Sales', spex: 'SpEx', operations: 'Ops' };
export const ROLE_FULL = { admin: 'Administrator', sales: 'Sales', market_research: 'Market Research', data_mining: 'Data Mining', telemarketing: 'Telemarketing', speaker_sales: 'Speaker Sales', spex: 'SpEx', operations: 'Operations' };
export const ROLE_TONE = { admin: 'slate', sales: 'teal', market_research: 'blue', data_mining: 'amber', telemarketing: 'violet', speaker_sales: 'green', spex: 'cyan', operations: 'neutral' };
// MUST hold every key in CRM_MODULES in backend/accounts/models.py, in the same
// order. This is the permission grid, and savePermissions sends the WHOLE grid to
// an endpoint that deletes the team's rows and rebuilds them from the payload —
// so a module missing here is a module the grid can never grant AND one that every
// save of any team's grid silently revokes.
//
// 'google_sync' was missing exactly that way after the backend split it out of
// 'webhooks' (models.py:377-381, migration 0027). The grid had no Google Sync row
// to tick, and saving any team's permissions deleted whatever google_sync row it
// had, so the page was unreachable for everyone but the all-access Admin team and
// no amount of clicking in the UI could change that.
export const CRM_MODULES = [{ k: 'bookings', l: 'Bookings' }, { k: 'ticket_central', l: 'Ticket Central' }, { k: 'events', l: 'Events' }, { k: 'users', l: 'Users' }, { k: 'teams', l: 'Teams' }, { k: 'performance', l: 'Performance' }, { k: 'webhooks', l: 'Webhooks' }, { k: 'roles', l: 'Permissions' }, { k: 'google_sync', l: 'Google Sync' }, { k: 'paper_review', l: 'Paper Review' }, { k: 'proposal_submission', l: 'Proposal Submission' }];
export const PERM_ACTIONS = ['view', 'create', 'update', 'delete'];
export const PAGE_SIZE = 50;
export const ALL_MODULES = CRM_MODULES.map((m) => m.k);

export const GSYNC_TYPE_LABEL = { bookings: 'Bookings', events: 'Events', full_sync: 'Full Sync', crm_mirror: 'CRM Data Sheet', sheet_target: 'Sheet Push' };
export const GSYNC_TRIGGER_LABEL = { admin_manual: 'Manual', scheduler: 'Scheduler', system: 'System' };
export const GSYNC_STATUS_TONE = { pending: 'neutral', running: 'amber', success: 'green', failed: 'red', partial_success: 'amber' };

export const BOOKING_TEAM_TYPES = ['sales', 'spex', 'speaker_sales', 'telemarketing'];

/**
 * Dashboard date-range filter. `k` is sent verbatim as ?period= and must match a
 * key in PERIOD_DAYS (backend/config/views.py), which 400s on anything else
 * rather than silently answering for all time.
 *
 * Windows are ROLLING and include today. The labels say "30 days" and "12
 * months" rather than "month" and "year" on purpose: a rolling window labelled
 * "last month" reads as "the previous calendar month" and would be wrong by up
 * to 30 days. The active window's real dates are shown next to the control.
 */
export const DASH_PERIODS = [
  { k: 'all', l: 'All time' },
  { k: 'last_7_days', l: 'Last 7 days' },
  { k: 'last_30_days', l: 'Last 30 days' },
  { k: 'last_12_months', l: 'Last 12 months' },
];
export const DASH_PERIOD_LABEL = Object.fromEntries(DASH_PERIODS.map((p) => [p.k, p.l]));

export const YES_NO = ['Yes', 'No'];
export const VR1_STATUS = ['Not Sent', 'Sent', 'Opened', 'Clicked'];
export const SALES_CHECK_OPTIONS = ['Unassigned', 'Pending', 'Scheduled', 'Done'];

// ── Proposal Submission ──────────────────────────────────────────────────
// Option lists inferred from the reference screenshots — the exact allowed
// values are not confirmed against a backend yet. See PROPOSAL_SUBMISSION_BACKEND.md.
export const PARTICIPATION_TYPES = ['Speaker', 'Sponsor', 'Speaker & Sponsor', 'Panelist'];
// A-D was inferred from screenshots; the Zoho export carries B+ and E as well,
// and 'B+' is the third most common. Listing only A-D meant the column filter and
// the form's dropdown could neither show nor select a grade that a third of the
// imported rows actually hold.
export const QC_GRADES = ['A', 'B', 'B+', 'C', 'D', 'E'];
export const QC_GRADE_TONE = { A: 'green', B: 'blue', 'B+': 'blue', C: 'amber', D: 'red', E: 'red' };
export const SPEAKER_SLOT_STATUSES = ['Pending', 'Confirmed', 'Declined', 'Waitlisted'];
export const SPEAKER_SLOT_TONE = { Pending: 'amber', Confirmed: 'green', Declined: 'red', Waitlisted: 'slate' };
export const SPONSORSHIP_STATUSES = ['Pending', 'Confirmed', 'Declined', 'Not Applicable'];
export const SPONSORSHIP_TONE = { Pending: 'amber', Confirmed: 'green', Declined: 'red', 'Not Applicable': 'slate' };
export const REVENUE_POSSIBILITY = ['Low', 'Medium', 'High'];
export const REVENUE_TONE = { Low: 'slate', Medium: 'amber', High: 'green' };

// ── Paper Review ──────────────────────────────────────────────────────────
// Scoring rubric ported from the reference screenshots — six weighted
// criteria that sum to a 0-45 "Proposal Score", which is what the reference
// data's Grade appears to be derived from (one example: 9+2+9+1+1+5=27 → B).
// The exact score→grade bands are inferred, not confirmed — see
// PAPER_REVIEW_BACKEND.md. Session/location options are placeholders pending
// confirmation of the real picklist values.
export const PAPER_REVIEW_CRITERIA = [
  { key: 'closeness_to_topic', label: 'Closeness to Topic', max: 10 },
  { key: 'closeness_to_region', label: 'Closeness to Region', max: 5 },
  { key: 'clear_solution_to_challenges', label: "Clear Solution to Challenges", max: 10 },
  { key: 'case_study_results_examples', label: 'Case Study, Results, Examples', max: 5 },
  { key: 'not_obvious_sales_pitch', label: "Not an obvious 'Sales Pitch'", max: 5 },
  { key: 'company_profile_score', label: 'Company Profile', max: 10 },
];
export const PAPER_REVIEW_MAX_SCORE = PAPER_REVIEW_CRITERIA.reduce((s, c) => s + c.max, 0);
// Same correction as QC_GRADES above; the export's real vocabulary is
// A, B, B+, C, D, E, not A-D.
export const PAPER_GRADES = ['A', 'B', 'B+', 'C', 'D', 'E'];
export const PAPER_GRADE_TONE = { A: 'green', B: 'blue', 'B+': 'blue', C: 'amber', D: 'red', E: 'red' };
export const PAPER_SESSION_OPTIONS = ['Day 1, Morning Session', 'Day 1, Afternoon Session', 'Day 2, Morning Session', 'Day 2, Afternoon Session'];
