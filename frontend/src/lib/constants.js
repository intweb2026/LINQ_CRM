// Ported 1:1 from legacy-vanilla-js/js/01-data.js (domain constants section).
export const PAYMENT_STATUSES = ['Pending', 'Paid', 'Unpaid', 'Cancelled', 'Refunded', 'Free', 'Credit Pending (Free)', 'Credit Pending (Paid)', 'Credit Transferred', 'Paid (Transferred)'];
export const STATUS_TONE = { Paid: 'green', Pending: 'amber', Unpaid: 'red', Cancelled: 'red', Refunded: 'slate', Free: 'blue', 'Credit Pending (Free)': 'violet', 'Credit Pending (Paid)': 'violet', 'Credit Transferred': 'cyan', 'Paid (Transferred)': 'green' };
export const PAYMENT_TYPES = ['Stripe', 'Bank'];
export const TICKET_TIERS = ['SEB', 'EB', 'Regular'];
export const ATTENDANCE = ['Pending', 'Confirmed', 'No-show', 'Cancelled'];
export const ATT_TONE = { Confirmed: 'green', Pending: 'amber', 'No-show': 'red', Cancelled: 'slate' };
export const DISCOUNTS = ['0%', '10%', '20%', '25%', '30%', '50%'];
export const EVENT_STATUSES = ['Draft', 'Upcoming', 'Live', 'Completed', 'Cancelled', 'Postponed', 'TBP'];
export const EV_TONE = { Draft: 'neutral', Upcoming: 'blue', Live: 'green', Completed: 'slate', Cancelled: 'red', Postponed: 'amber', TBP: 'violet' };
export const TK_STATUS = { draft: { l: 'Draft', t: 'neutral' }, mr_submitted: { l: 'MR Submitted', t: 'blue' }, completed: { l: 'Completed', t: 'green' }, returned: { l: 'Returned', t: 'red' } };
export const TK_PRIORITY = { AS: 'neutral', AD: 'blue', SPEX: 'violet', DD: 'amber', ASSOC: 'green', MEDIA: 'amber', AB: 'red' };
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
