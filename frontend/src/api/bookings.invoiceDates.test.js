/**
 * api/bookings.invoiceDates.test.js
 * ─────────────────────────────────
 * Request Date and Invoice Date have to REACH THE SERVER.
 *
 * THE BUG THIS PINS
 * Both are invoice columns (BookEvent.request_date / .invoice_date) with no
 * per-delegate override behind them, and both are shown as an editable cell on
 * every delegate row in the booking modals. Nothing carried the typed value out
 * of the grid. The pair was in neither OVERRIDE_FIELDS nor delegateToBackend, so
 * the PATCH went out without them, answered 200, and the old date came back on
 * the next refetch. Nobody could change a Request Date, on any booking.
 *
 * It is a silent failure by construction. No request fails, no console error,
 * and the modal closes on "updated". Asserting the WIRE PAYLOAD is the only way
 * it stays fixed; a test that went through the API would pass on the old code
 * too, because the endpoint always accepted the field it was never sent.
 */
const mockPatch = jest.fn();
const mockPost = jest.fn();

jest.mock('./client', () => ({
  http: {
    patch: (...args) => mockPatch(...args),
    post: (...args) => mockPost(...args),
  },
  fetchAllPages: jest.fn(),
  fetchPage: jest.fn(),
  assertIdArray: jest.fn(),
  chunk: jest.fn(),
  mapLimit: jest.fn(),
  bulkUpdate: jest.fn(),
  fetchBulkUpdateSchema: jest.fn(),
}));

const { saveInvoiceDelegates, createInvoice } = require('./bookings');

const META = { invoice_number: 'INV-2026001', event_code: 'AIU', event_name: 'AIU 2026' };

const row = (over = {}) => ({
  id: 11, name: 'Jane Doe', email: 'jane@acme.test', company_name: 'Acme',
  request_date: '2026-03-04', invoice_date: '2026-03-05',
  payment_status: 'Pending', booking_code: 'Delegate', ...over,
});

beforeEach(() => {
  // mockReset, not mockClear: create-react-app's jest config sets
  // resetMocks:true, which strips the implementation before every test — so the
  // resolved value has to be handed back here or http.patch returns undefined.
  mockPatch.mockReset().mockResolvedValue({ data: {} });
  mockPost.mockReset().mockResolvedValue({ data: {} });
});

const patchedBody = () => mockPatch.mock.calls[0][1];
const postedBody = () => mockPost.mock.calls[0][1];

test('an edited request date is PATCHed onto the invoice', async () => {
  await saveInvoiceDelegates('INV-2026001', META, [row()], 7);
  expect(mockPatch).toHaveBeenCalledWith('invoices/7/', expect.anything());
  expect(patchedBody().request_date).toBe('2026-03-04');
  expect(patchedBody().invoice_date).toBe('2026-03-05');
});

test('two rows edited together send the one agreed date', async () => {
  await saveInvoiceDelegates('INV-2026001', META, [
    row(), row({ id: 12, email: 'sam@acme.test' }),
  ], 7);
  expect(patchedBody().request_date).toBe('2026-03-04');
});

test('a cleared request date is sent as null, not dropped', async () => {
  // The only way the column can be emptied. undefined would be deleted from the
  // payload by invoiceToBackend, and the stored date would survive the save.
  await saveInvoiceDelegates('INV-2026001', META, [row({ request_date: '' })], 7);
  expect(patchedBody()).toHaveProperty('request_date', null);
});

test('rows that never carried the field leave the stored date alone', async () => {
  const { request_date, invoice_date, ...noDates } = row();
  await saveInvoiceDelegates('INV-2026001', META, [noDates], 7);
  expect(patchedBody()).not.toHaveProperty('request_date');
  expect(patchedBody()).not.toHaveProperty('invoice_date');
});

test('a new booking is created with the date typed in the grid', async () => {
  // NewBookingModal used to pin request_date and invoice_date to today in
  // `meta`, and meta outranks the rows, so a new booking could only ever be
  // raised with today's date however the cell was filled in.
  await createInvoice(META, [row({ id: null, request_date: '2026-01-09' })]);
  expect(postedBody().request_date).toBe('2026-01-09');
});
