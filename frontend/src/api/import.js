// Real backend bulk-import endpoints (500 rows/call max — see backend
// events/views.py, book_event/views.py, ticket_central/views.py bulk_import
// actions). Field keys below are the exact backend field names each endpoint
// expects — not this UI's display field names.
import { http } from './client';

const BATCH_SIZE = 500;

export const TARGET_FIELDS = {
  events: [
    ['event_code', 'Event Code'], ['official_event_name', 'Official Event Name'], ['event_date', 'Start Date'],
    ['end_date', 'End Date'], ['location', 'Location'], ['status', 'Status'], ['event_type', 'Event Type'],
    ['website_live_date', 'Website Live Date'], ['sales_team', 'Sales Team'], ['team_leader', 'Sales Team Leader'],
    ['website', 'Website'], ['sales_executive', 'Sales Executive (username/email)'],
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
