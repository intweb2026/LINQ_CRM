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
  // Order is specific-before-generic — speaker_sales_team before sales_team,
  // website_live_date before website, vr1_sent_status before status. autoMap
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
    ['speaker_sales_team', 'Speaker Sales Team'],
    ['sales_team', 'Sales Team'],
    ['team_leader', 'Sales Team Leader'],
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
  bookings: [
    ['invoice_number', 'Invoice Number'], ['event_code', 'Event Code'], ['event_name', 'Event Name'],
    ['booking_code', 'Booking Code'], ['contact_name', 'Delegate Name'], ['company_name', 'Company'],
    ['contact_email', 'Email'], ['accounts_contact_email', 'Accounts Email'], ['contact_phone', 'Direct Line'],
    ['request_date', 'Request Date'], ['invoice_date', 'Invoice Date'], ['payment_status', 'Payment Status'],
    ['payment_type', 'Payment Type'], ['ticket_tier', 'Ticket Tier'], ['discount', 'Discount'],
    ['attendance', 'Attendance'], ['reference', 'Reference'],
  ],
  tickets: [
    ['ticket_number', 'Ticket #'], ['external_id', 'External ID'], ['event_code', 'Source Event'],
    ['purpose', 'Purpose'], ['type_of_ticket', 'Type of Ticket'], ['competitor_event_name', 'Competitor Event'],
    ['organizer', 'Organizer'], ['event_month_year', 'Event Month/Year'], ['event_location', 'Event Location'],
    ['relationship', 'Relationship'], ['priority', 'Priority'], ['estimate', 'Estimate'],
    ['assigned_mr', 'Assigned MR'], ['link_url', 'Link URL'], ['mr_comments', 'MR Comments'],
  ],
};

const ENDPOINT = { events: 'events/bulk_import/', bookings: 'invoices/bulk_import/', tickets: 'tickets/bulk_import/' };

function chunk(arr, size) {
  const out = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

// `rows` are already mapped to backend field names (see TARGET_FIELDS).
export async function run(kind, rows) {
  const url = ENDPOINT[kind] || ENDPOINT.bookings;
  const batches = chunk(rows, BATCH_SIZE);
  let imported = 0, skipped = 0;
  const errors = [];

  for (let i = 0; i < batches.length; i++) {
    const body = kind === 'tickets'
      ? { rows: batches[i], duplicate_mode: 'allow_all', batch_number: i + 1, total_batches: batches.length }
      : { rows: batches[i], duplicate_strategy: 'skip', batch_number: i + 1 };
    const { data } = await http.post(url, body);
    imported += data.inserted ?? data.updated ?? 0;
    skipped += data.skipped_duplicates ?? (data.skipped_rows || []).length ?? 0;
    errors.push(...(data.errors || []));
  }
  return { imported, skipped, errors };
}
