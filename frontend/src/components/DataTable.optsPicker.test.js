/**
 * components/DataTable.optsPicker.test.js
 * ───────────────────────────────────────
 * Typing a value into a closed-list column's filter.
 *
 * A column with `opts` — payment status, grade, ticket priority — used to be
 * filterable ONLY by ticking one of its boxes. Typing is now allowed alongside
 * them, and the one place that can quietly go wrong is what the typed text
 * becomes: a value typed out in full has to resolve to the stored option, not
 * to the label the user happened to read off the screen, or the filter goes to
 * the server as a string its choice vocabulary has never heard of and matches
 * nothing at all.
 */
import { commitOptValue } from './DataTable';

// Stored values on the left, what the user reads on the right.
const OPTS = ['paid', 'part_paid', 'cancelled'];
const LABEL = (o) => ({ paid: 'Paid', part_paid: 'Part Paid', cancelled: 'Cancelled' })[o];

test('a typed label resolves to the stored option, whatever the casing', () => {
  expect(commitOptValue(OPTS, 'part paid', [], LABEL)).toEqual(['part_paid']);
  expect(commitOptValue(OPTS, '  PAID  ', [], LABEL)).toEqual(['paid']);
});

test('a value the list does not offer is taken at face value', () => {
  expect(commitOptValue(OPTS, 'refunded', ['paid'], LABEL)).toEqual(['paid', 'refunded']);
});

test('a partial match is NOT resolved — "paid" must not swallow "Part Paid"', () => {
  expect(commitOptValue(OPTS, 'pa', [], LABEL)).toEqual(['pa']);
});

test('committing the same value twice does not duplicate it', () => {
  expect(commitOptValue(OPTS, 'Paid', ['paid'], LABEL)).toEqual(['paid']);
});

test('empty or whitespace-only text commits nothing', () => {
  const values = ['paid'];
  expect(commitOptValue(OPTS, '   ', values, LABEL)).toBe(values);
});
