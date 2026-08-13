// Ported 1:1 from legacy-vanilla-js/js/01-data.js (domain constants section).
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
export const CRM_MODULES = [{ k: 'bookings', l: 'Bookings' }, { k: 'ticket_central', l: 'Ticket Central' }, { k: 'events', l: 'Events' }, { k: 'reports', l: 'Reports' }, { k: 'users', l: 'Users' }, { k: 'teams', l: 'Teams' }, { k: 'performance', l: 'Performance' }, { k: 'webhooks', l: 'Webhooks' }, { k: 'roles', l: 'Roles' }, { k: 'paper_review', l: 'Paper Review' }, { k: 'proposal_submission', l: 'Proposal Submission' }];
export const PERM_ACTIONS = ['view', 'create', 'update', 'delete'];
export const PAGE_SIZE = 50;
export const ALL_MODULES = CRM_MODULES.map((m) => m.k);

export const GSYNC_TYPE_LABEL = { bookings: 'Bookings', events: 'Events', full_sync: 'Full Sync', crm_mirror: 'CRM Data Sheet' };
export const GSYNC_TRIGGER_LABEL = { admin_manual: 'Manual', scheduler: 'Scheduler', system: 'System' };
export const GSYNC_STATUS_TONE = { pending: 'neutral', running: 'amber', success: 'green', failed: 'red', partial_success: 'amber' };

export const BOOKING_TEAM_TYPES = ['sales', 'spex', 'speaker_sales', 'telemarketing'];

export const YES_NO = ['Yes', 'No'];
export const VR1_STATUS = ['Not Sent', 'Sent', 'Opened', 'Clicked'];
export const SALES_CHECK_OPTIONS = ['Unassigned', 'Pending', 'Scheduled', 'Done'];

// ── Proposal Submission ──────────────────────────────────────────────────
// Option lists inferred from the reference screenshots — the exact allowed
// values are not confirmed against a backend yet. See PROPOSAL_SUBMISSION_BACKEND.md.
export const PARTICIPATION_TYPES = ['Speaker', 'Sponsor', 'Speaker & Sponsor', 'Panelist'];
export const QC_GRADES = ['A', 'B', 'C', 'D'];
export const QC_GRADE_TONE = { A: 'green', B: 'blue', C: 'amber', D: 'red' };
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
export const PAPER_GRADES = ['A', 'B', 'C', 'D'];
export const PAPER_GRADE_TONE = { A: 'green', B: 'blue', C: 'amber', D: 'red' };
export const PAPER_SESSION_OPTIONS = ['Day 1, Morning Session', 'Day 1, Afternoon Session', 'Day 2, Morning Session', 'Day 2, Afternoon Session'];
