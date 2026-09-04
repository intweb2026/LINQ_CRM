/**
 * api/bookings.blankTier.test.js
 * ──────────────────────────────
 * Clearing Ticket Tier and Payment Type on an EXISTING booking.
 *
 * Both cells offer a blank entry (DelegateTable's BLANK_FIRST) and neither blank
 * could be saved. splitPersonLevel read "shared and empty" as "nothing to push
 * up", so the PATCH carried delegate_ticket_tier: null and no invoice-level
 * ticket_tier at all — the invoice kept its old tier and the resolved value
 * (`override or invoice`) read straight back off it. The request answered 200,
 * the modal said "updated", and the tier was back on the next refetch.
 *
 * Asserting the WIRE PAYLOAD, like the invoiceDates suite beside this one: the
 * endpoint has always accepted the '' it was never sent, so a test that went
 * through the API would pass on the bug.
 */
const mockPatch = jest.fn();

jest.mock('./client', () => ({
  http: {
    patch: (...args) => mockPatch(...args),
    post: jest.fn(),
  },
  fetchAllPages: jest.fn(),
  fetchPage: jest.fn(),
  assertIdArray: jest.fn(),
  chunk: jest.fn(),
  mapLimit: jest.fn(),
  bulkUpdate: jest.fn(),
  fetchBulkUpdateSchema: jest.fn(),
}));

const { saveInvoiceDelegates } = require('./bookings');

const META = { invoice_number: 'INV-2026001', event_code: 'AIU', event_name: 'AIU 2026' };

const row = (over = {}) => ({
  id: 11, name: 'Jane Doe', email: 'jane@acme.test', company_name: 'Acme',
  payment_status: 'Pending', booking_code: 'Delegate',
  ticket_tier: 'SEB', payment_type: 'Stripe', ...over,
});

beforeEach(() => {
  mockPatch.mockReset().mockResolvedValue({ data: {} });
});

const patchedBody = () => mockPatch.mock.calls[0][1];

test('a tier every row has cleared reaches the invoice as an empty string', async () => {
  await saveInvoiceDelegates('INV-2026001', META, [
    row({ ticket_tier: '' }), row({ id: 12, email: 'sam@acme.test', ticket_tier: '' }),
  ], 7);
  // '' and not null: the invoice column is a non-null CharField and the endpoint
  // rejects a null outright. No row has a tier left to override with, so every
  // override goes out null and each one resolves onto the invoice's blank.
  expect(patchedBody()).toHaveProperty('ticket_tier', '');
  expect(patchedBody().delegates.map((d) => d.delegate_ticket_tier)).toEqual([null, null]);
});

test('a cleared payment type follows the same rule', async () => {
  await saveInvoiceDelegates('INV-2026001', META, [row({ payment_type: '' })], 7);
  expect(patchedBody()).toHaveProperty('payment_type', '');
  expect(patchedBody().delegates[0].delegate_payment_type).toBeNull();
});

test('a tier every row shares still travels on the invoice', async () => {
  await saveInvoiceDelegates('INV-2026001', META, [
    row({ ticket_tier: 'EB' }), row({ id: 12, email: 'sam@acme.test', ticket_tier: 'EB' }),
  ], 7);
  expect(patchedBody().ticket_tier).toBe('EB');
  expect(patchedBody().delegates.map((d) => d.delegate_ticket_tier)).toEqual([null, null]);
});

test('ONE row of a group booking cleared: the blank goes on the invoice, the rest keep theirs', async () => {
  // The reported case. A blank override would read as "inherit" and resolve
  // straight back onto the invoice's tier, so the only way to express it is the
  // blank on the invoice and an override on every row that still has a tier.
  await saveInvoiceDelegates('INV-2026001', META, [
    row({ ticket_tier: '' }),
    row({ id: 12, email: 'sam@acme.test', ticket_tier: 'SEB' }),
    row({ id: 13, email: 'lee@acme.test', ticket_tier: 'SEB' }),
  ], 7);
  expect(patchedBody()).toHaveProperty('ticket_tier', '');
  expect(patchedBody().delegates.map((d) => d.delegate_ticket_tier))
    .toEqual([null, 'SEB', 'SEB']);
});

test('one cleared payment type on a group booking behaves the same', async () => {
  await saveInvoiceDelegates('INV-2026001', META, [
    row({ payment_type: '' }), row({ id: 12, email: 'sam@acme.test', payment_type: 'Bank' }),
  ], 7);
  expect(patchedBody()).toHaveProperty('payment_type', '');
  expect(patchedBody().delegates.map((d) => d.delegate_payment_type)).toEqual([null, 'Bank']);
});

test('rows that DIFFER with nothing blank keep their own tier and leave the invoice alone', async () => {
  await saveInvoiceDelegates('INV-2026001', META, [
    row({ ticket_tier: 'EB' }), row({ id: 12, email: 'sam@acme.test', ticket_tier: 'Regular' }),
  ], 7);
  expect(patchedBody().delegates.map((d) => d.delegate_ticket_tier)).toEqual(['EB', 'Regular']);
  expect(patchedBody()).not.toHaveProperty('ticket_tier');
});
