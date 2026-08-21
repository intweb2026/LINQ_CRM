// Real backend bulk-import endpoints (500 rows/call max — see backend
// events/views.py, book_event/views.py, ticket_central/views.py bulk_import
// actions). Field keys below are the exact backend field names each endpoint
// expects — not this UI's display field names.
import { http } from './client';

const BATCH_SIZE = 500;

// Entries are [backend_field, display_label] with an optional third element, a
// list of extra header spellings that should auto-map onto the field. Aliases
// exist for source columns whose wording does not resemble the backend name;
// see autoMap in components/ImportWizard.jsx.
export const TARGET_FIELDS = {
  // Every key below is read by events/views.py bulk_import. Fields DERIVED in
  // Event.save() are deliberately absent, because save() recomputes them from
  // the sources listed here and would discard anything imported into them:
  //   name, official_name    <- official_event_name (or event_code when blank)
  //   city, country, venue   <- location
  //   accepting_web_bookings <- web_bookings
  //   tele_marketing_team    <- telemarketing_team
  //   market_research_team   <- market_research_senior
  // bulk_import also accepts `name`, but save() overwrites it unconditionally,
  // so offering it would silently drop the column. Same for the two aliases it
  // reads, `accepting_web_bookings` and `tele_marketing_team`.
  //
  // Order is specific-before-generic — team_leader's 'Sales Team Leader' before
  // sales_team, website_live_date before website, vr1_sent_status before status. autoMap
  // resolves exact matches first, but a header with no exact match falls back to
  // a substring scan that takes the first hit in this order.
  events: [
    ['event_code', 'Event Code'],
    ['official_event_name', 'Official Event Name'],
    ['event_date', 'Start Date'],
    ['end_date', 'End Date'],
    ['location', 'Location'],
    ['event_type', 'Event Type'],
    ['vr1_sent_status', 'VR1 Sent Status'],
    ['status', 'Status'],
    ['website_live_date', 'Website Live Date'],
    ['website', 'Website'],
    ['web_bookings', 'Accepting Web Bookings', ['Web Bookings']],
    ['team_leader', 'Sales Team Leader'],
    // 'SCA' is the current sheet header; 'Sales Team' and 'Speaker Sales Team'
    // are the older ones, and the speaker column was folded into this one by
    // events migration 0017, so both still map here.
    ['sales_team', 'SCA', ['Sales Team', 'Speaker Sales Team']],
    ['sales_executive', 'Sales Executive (username/email)'],
    ['spex_team', 'SpEx Team'],
    ['telemarketing_team', 'Tele Marketing Team', ['Telemarketing Team']],
    ['market_research_senior', 'Market Research Senior', ['Market Research Team']],
    ['market_research_junior', 'Market Research Junior'],
    ['event_management_team', 'Event Management Team'],
    ['content_check', 'Content Check'],
    ['marketing_check', 'Marketing Check'],
    ['sales_check', 'Sales Check'],
    ['nearest_related_event', 'Nearest Related Event'],
    ['email_marketing_name', 'Email Marketing Name'],
    ['branding_name', 'Branding Name'],
    ['annualisation', 'Annualisation'],
    ['date_format', 'Date Format'],
    ['related_event_1', 'Related Event 1'],
    ['related_event_2', 'Related Event 2'],
    ['related_event_3', 'Related Event 3'],
    ['upcoming_event_1', 'Upcoming Event 1'],
    ['upcoming_event_2', 'Upcoming Event 2'],
    ['upcoming_event_3', 'Upcoming Event 3'],
  ],
  // Mirrors BOOKING_IMPORT_FIELDS in backend/book_event/views.py, which is what
  // invoices/import_schema/ publishes and what fetchTargetFields() prefers at
  // runtime. Held here as the offline fallback, and kept complete: this list used
  // to offer 17 of the 28 columns bulk_import reads, so a spreadsheet carrying
  // Currency, Position, Sales Executive or Delegate Count had nowhere to map them
  // and was imported without them — indistinguishable from a file that never had
  // those columns.
  //
  // total_amount and the other money columns are absent because bulk_import does
  // not read them. Offering a field the importer ignores is worse than omitting
  // it: the wizard would count the column as mapped.
  bookings: [
    ['invoice_number', 'Invoice Number', ['Invoice No', 'Invoice #']],
    ['event_code', 'Event Code'], ['event_name', 'Event Name'], ['booking_code', 'Booking Code'],
    ['edition', 'Edition', ['Year']],
    ['company_name', 'Company', ['Company Name', 'Organisation']],
    ['contact_name', 'Delegate Name', ['Name', 'Attendee', 'Full Name']],
    ['position', 'Job Title / Position', ['Designation', 'Job Title']],
    ['accounts_contact_email', 'Accounts Email', ['Accounts Contact Email']],
    ['contact_email', 'Email', ['Email Address']],
    ['contact_phone', 'Direct Line', ['Phone', 'Phone Number', 'Mobile']],
    ['request_date', 'Request Date'], ['invoice_date', 'Invoice Date'], ['payment_date', 'Payment Date'],
    ['payment_status', 'Payment Status', ['Status']],
    ['paid_or_free', 'Paid / Free', ['Paid or Free']],
    ['payment_type', 'Payment Type', ['Payment Method']],
    ['ticket_tier', 'Ticket Tier', ['Tier']],
    ['currency', 'Currency'], ['discount_code', 'Discount Code'], ['discount', 'Discount'],
    ['delegate_count', 'Delegate Count', ['No of Delegates']],
    ['attendance', 'Attendance', ['Attended', 'Confirmed']],
    ['add_ons', 'Add-Ons', ['Addons']],
    ['reference', 'Reference', ['Payment Reference']],
    ['notes', 'Notes', ['Comments', 'Remarks']],
    ['sales_executive', 'Sales Executive (username/email)', ['Sales Exec', 'Sales Rep', 'Sales Team']],
    ['created_at', 'Added Time', ['Created At', 'Created Time']],
  ],
  // Fallback only — tickets/import_schema/ derives the real list from the Ticket
  // model, which is the same allowlist _coerce_row filters against. This used to
  // be 15 hand-written entries against the ~40 the importer accepts: the entire
  // DMD result block, the LX-2 second pass, status and ticket_type were
  // unmappable, so a Zoho export imported as an empty shell of a ticket.
  tickets: [
    ['ticket_number', 'Ticket Number', ['Ticket #']], ['external_id', 'External ID'],
    ['event_code', 'Source Event'], ['event_name', 'Event Name'], ['status', 'Status'],
    ['purpose', 'Purpose'], ['type_of_ticket', 'Type of Ticket'], ['ticket_type', 'Ticket Type'],
    ['competitor_event_name', 'Competitor Event'], ['organizer', 'Organizer'],
    ['event_month_year', 'Event Month/Year'], ['event_location', 'Event Location'],
    ['relationship', 'Relationship'], ['priority', 'Priority'], ['estimate', 'Estimate'],
    ['assigned_mr', 'Assigned MR'], ['link_url', 'Link URL'],
    ['linkedin_keywords', 'LinkedIn Keywords'], ['duplicate_tickets', 'Duplicate Tickets'],
    ['mr_comments', 'MR Comments'],
    ['assign_name', 'Assign Name'], ['assign_date', 'Assign Date'],
    ['actual_number', 'Actual Number'], ['new_contacts_created', 'New Contacts Created'],
    ['mined_count', 'Mined Count'], ['complete_date', 'Complete Date'],
    ['hubspot_entry_date', 'HubSpot Entry Date'], ['dm_comments', 'DM Comments'],
    ['assign_name_lx2', 'Assign Name (LX-2)'], ['actual_count_lx2', 'Actual Count (LX-2)'],
    ['complete_date_lx2', 'Complete Date (LX-2)'], ['dm_comments_lx2', 'DM Comments (LX-2)'],
    ['added_user_text', 'Added User'],
    ['source_spreadsheet_id', 'Source Spreadsheet ID'], ['source_tab', 'Source Tab'],
    ['source_row_number', 'Source Row Number'], ['idempotency_key', 'Idempotency Key'],
    ['created_at', 'Added Time', ['Created At', 'Created Time']],
  ],
};

const ENDPOINT = { events: 'events/bulk_import/', bookings: 'invoices/bulk_import/', tickets: 'tickets/bulk_import/' };

// Resources that publish their accepted columns. `events` is absent on purpose:
// its importer reads a hand-listed set of keys and TARGET_FIELDS.events already
// covers every one of them (verified by diffing the two), so there is nothing for
// an endpoint to correct.
const SCHEMA_ENDPOINT = {
  bookings: 'invoices/import_schema/',
  tickets: 'tickets/import_schema/',
};

/**
 * The fields Smart Import may map to, from the SERVER where it publishes them.
 *
 * WHY ASK THE SERVER
 * TARGET_FIELDS is a hand-written list and it had drifted: 17 of 28 accepted
 * columns for bookings, 15 of ~40 for tickets. A missing field is invisible in
 * this UI — the column simply has nothing to map onto, so it is skipped, and a
 * skipped column looks exactly like a column that was not in the file. Reading
 * the importer's own registry is what stops that recurring.
 *
 * Falls back to TARGET_FIELDS on any failure rather than leaving the mapping step
 * empty: an unreachable schema must not make the wizard unusable, and the static
 * list is complete as of this commit even if it cannot stay so by itself.
 */
export async function fetchTargetFields(kind) {
  const fallback = TARGET_FIELDS[kind] || TARGET_FIELDS.bookings;
  const url = SCHEMA_ENDPOINT[kind];
  if (!url) return fallback;
  try {
    const { data } = await http.get(url);
    const fields = (data && data.fields) || [];
    if (!fields.length) return fallback;
    // Aliases from the static list are merged in: the server publishes the
    // canonical spellings, and these are the extra header wordings this UI has
    // learned to recognise. Losing them would make auto-mapping worse than before.
    const aliasFor = new Map(fallback.map(([key, , aliases]) => [key, aliases || []]));
    return fields.map((f) => [f.key, f.label, [...(f.aliases || []), ...(aliasFor.get(f.key) || [])]]);
  } catch {
    return fallback;
  }
}

function chunk(arr, size) {
  const out = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

/**
 * `rows` are already mapped to backend field names (see TARGET_FIELDS).
 *
 * `onProgress` is called after every batch lands, with the running totals so
 * far: { imported, skipped, errors, sent, total, batch, totalBatches }. It is
 * called once with zeroes before the first request, so a caller can render the
 * scale of the job (total rows, total batches) before anything has been written.
 *
 * WHY: rows go up 500 at a time, sequentially, so a 20,000-row file is 40 round
 * trips and can sit for minutes. Without this the caller has one await and no
 * way to tell a slow import from a hung one. `imported` is what actually
 * reached the database; `sent` is what has been processed including rows the
 * server skipped as duplicates, which is the honest basis for "how far along".
 *
 * Reported BEFORE a batch can throw, so the counts a caller holds after a
 * failure describe the batches that were really written rather than resetting
 * to nothing.
 */
export async function run(kind, rows, onProgress) {
  const url = ENDPOINT[kind] || ENDPOINT.bookings;
  const batches = chunk(rows, BATCH_SIZE);
  let imported = 0, skipped = 0, sent = 0;
  const errors = [];

  const report = (batch) => onProgress?.({
    imported, skipped, errors: errors.length, sent,
    total: rows.length, batch, totalBatches: batches.length,
  });
  report(0);

  for (let i = 0; i < batches.length; i++) {
    const body = kind === 'tickets'
      ? { rows: batches[i], duplicate_mode: 'allow_all', batch_number: i + 1, total_batches: batches.length }
      : { rows: batches[i], duplicate_strategy: 'skip', batch_number: i + 1 };
    const { data } = await http.post(url, body);
    imported += data.inserted ?? data.updated ?? 0;
    skipped += data.skipped_duplicates ?? (data.skipped_rows || []).length ?? 0;
    errors.push(...(data.errors || []));
    sent += batches[i].length;
    report(i + 1);
  }
  return { imported, skipped, errors };
}
