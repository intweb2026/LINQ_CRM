/**
 * api/bookings.invoiceDates.test.js
 * ─────────────────────────────────
 * Request Date and Invoice Date, per delegate, on the wire.
 *
 * TWO BUGS, ONE AFTER THE OTHER
 * Both dates started as invoice columns shown as an editable cell on every
 * delegate row. First, nothing carried the typed value out of the grid at all;
 * the pair was in neither OVERRIDE_FIELDS nor delegateToBackend, so the PATCH
 * went out without them, answered 200, and the old date came back on the next
 * refetch. Then, carried as one shared invoice value, editing one delegate's
 * date moved every delegate on the invoice with it.
 *
 * Both are silent by construction. No request fails, no console error, and the
 * modal closes on "updated" either way, so asserting the WIRE PAYLOAD is the
 * only thing that keeps them fixed. A test that went through the API would pass
 * on the first bug, because the endpoint always accepted the field it was never
 * sent.
 *
 * THE RULE THIS PINS is the one the five payment overrides already follow.
 * Delegates that AGREE put the value on the invoice and clear their overrides,
 * so the invoice keeps carrying the shared date every invoice-level read wants.
 * Delegates that DIFFER each keep their own override, so one person's date can
 * be corrected without touching anybody else's.
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

const { saveInvoiceDelegates, createInvoice, fromApi } = require('./bookings');

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

test('an edited request date is PATCHed onto the invoice when every row agrees', async () => {
  await saveInvoiceDelegates('INV-2026001', META, [
    row(), row({ id: 12, email: 'sam@acme.test' }),
  ], 7);
  expect(mockPatch).toHaveBeenCalledWith('invoices/7/', expect.anything());
  expect(patchedBody().request_date).toBe('2026-03-04');
  expect(patchedBody().invoice_date).toBe('2026-03-05');
  // Agreed, so the invoice carries it and no row shadows it.
  expect(patchedBody().delegates.map((d) => d.delegate_request_date)).toEqual([null, null]);
  expect(patchedBody().delegates.map((d) => d.delegate_invoice_date)).toEqual([null, null]);
});

test('two delegates on one invoice keep DIFFERENT request dates', async () => {
  // The whole point of the override. Changing one person's date must not move
  // the other person's, and the invoice's shared column is left alone because
  // neither date is the booking's one value any more.
  await saveInvoiceDelegates('INV-2026001', META, [
    row({ request_date: '2026-03-04' }),
    row({ id: 12, email: 'sam@acme.test', request_date: '2026-05-20' }),
  ], 7);
  expect(patchedBody().delegates.map((d) => d.delegate_request_date))
    .toEqual(['2026-03-04', '2026-05-20']);
  expect(patchedBody()).not.toHaveProperty('request_date');
});

test('two delegates keep different invoice dates, independently of the request date', async () => {
  await saveInvoiceDelegates('INV-2026001', META, [
    row({ invoice_date: '2026-03-05' }),
    row({ id: 12, email: 'sam@acme.test', invoice_date: '2026-06-01' }),
  ], 7);
  expect(patchedBody().delegates.map((d) => d.delegate_invoice_date))
    .toEqual(['2026-03-05', '2026-06-01']);
  // The request date still agrees, so it still travels on the invoice.
  expect(patchedBody().request_date).toBe('2026-03-04');
});

test('one row cleared nulls the invoice date and leaves the other row its own', async () => {
  // A blank cell is only expressible with the invoice column NULL: the resolved
  // value is `override or invoice`, so a row that clears its date while the
  // invoice keeps one would read straight back off the invoice.
  await saveInvoiceDelegates('INV-2026001', META, [
    row({ request_date: '' }),
    row({ id: 12, email: 'sam@acme.test', request_date: '2026-05-20' }),
  ], 7);
  expect(patchedBody()).toHaveProperty('request_date', null);
  expect(patchedBody().delegates.map((d) => d.delegate_request_date))
    .toEqual([null, '2026-05-20']);
});

test('a new booking is created with the date typed in the grid', async () => {
  // NewBookingModal used to pin request_date and invoice_date to today in
  // `meta`, and meta outranks the rows, so a new booking could only ever be
  // raised with today's date however the cell was filled in.
  await createInvoice(META, [row({ id: null, request_date: '2026-01-09' })]);
  expect(postedBody().request_date).toBe('2026-01-09');
});

test('the table reads the RESOLVED date, not the invoice column', async () => {
  // fromApi feeds every Bookings row and both modals. Reading `request_date`
  // here would show the invoice's shared date on a delegate that carries its
  // own, and the next save would then write that shared date back over it.
  const resolved = fromApi({
    id: 11, request_date: '2026-03-04', effective_request_date: '2026-05-20',
    invoice_date: '2026-03-05', effective_invoice_date: '2026-06-01',
  });
  expect(resolved.request_date).toBe('2026-05-20');
  expect(resolved.invoice_date).toBe('2026-06-01');

  // A payload from before the override columns existed still reads correctly.
  const legacy = fromApi({ id: 11, request_date: '2026-03-04', invoice_date: '2026-03-05' });
  expect(legacy.request_date).toBe('2026-03-04');
  expect(legacy.invoice_date).toBe('2026-03-05');
});
