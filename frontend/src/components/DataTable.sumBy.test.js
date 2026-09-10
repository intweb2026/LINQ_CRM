/**
 * The de-duplicating sum row.
 *
 * Pinned because the naive version was wrong in a way that looked right: the
 * Mining Matrix has one row per EVENT but gathers its figures per Ticket
 * Central purpose CODE, and two editions of one event share a code. Adding the
 * column up counted the same audience twice and disagreed with the page's own
 * KPI strip by exactly the duplicated codes.
 *
 * The logic is exercised directly rather than through a render, because what
 * matters is the arithmetic and rendering DataTable needs a router, a session
 * and a toast provider around it.
 */

/** The same reduction `sums` performs, extracted so it can be asserted. */
function sumRows(rows, keys, sumBy) {
  const out = {};
  keys.forEach((k) => { out[k] = 0; });
  const seen = sumBy ? new Set() : null;
  rows.forEach((r) => {
    if (seen) {
      const key = r[sumBy];
      if (key != null && key !== '') {
        if (seen.has(key)) return;
        seen.add(key);
      }
    }
    keys.forEach((k) => { if (typeof r[k] === 'number') out[k] += r[k]; });
  });
  return out;
}

const ROWS = [
  { event_code: 'DIU - JS', canonical_code: 'DIU', mailable: 74950, links: 15 },
  { event_code: 'APR2027_DIU-JS', canonical_code: 'DIU', mailable: 74950, links: 15 },
  { event_code: 'CFS - JS', canonical_code: 'CFS', mailable: 41387, links: 6 },
];

test('a plain sum double-counts an audience two rows share', () => {
  expect(sumRows(ROWS, ['mailable'], null).mailable).toBe(191287);
});

test('sumBy counts each distinct code once', () => {
  expect(sumRows(ROWS, ['mailable'], 'canonical_code').mailable).toBe(116337);
});

test('it de-duplicates every summed column at once, not just one', () => {
  expect(sumRows(ROWS, ['mailable', 'links'], 'canonical_code')).toEqual({
    mailable: 116337, links: 21,
  });
});

test('a row with no key is counted rather than dropped', () => {
  // It cannot be a duplicate of anything, and omitting it would understate the
  // total — which is the opposite mistake and just as wrong.
  const rows = [...ROWS, { event_code: 'Orphan', canonical_code: '', mailable: 100 }];
  expect(sumRows(rows, ['mailable'], 'canonical_code').mailable).toBe(116437);
});

test('two rows with no key both count', () => {
  const rows = [
    { canonical_code: null, mailable: 5 },
    { canonical_code: null, mailable: 7 },
  ];
  expect(sumRows(rows, ['mailable'], 'canonical_code').mailable).toBe(12);
});
