/**
 * components/DataTable.storedSort.test.js
 * ───────────────────────────────────────
 * Retiring a stored sort when a page changes its default.
 *
 * THE BUG THIS PINS, AND WHY IT NEEDS PINNING AT ALL
 * Bookings moved its default sort from Request Date to Modified Time. Backend
 * default, ordering_fields, the column's serverOrdering and the page's
 * defaultSort were all correct, the whole suite was green, and the table still
 * opened on Request Date for every single user. DataTable persists each table's
 * sort per browser, and a stored sort outranks defaultSort by design — correct
 * for a sort someone chose, wrong for a default they had never seen. Merely
 * visiting the page writes that blob, so nobody who had ever opened Bookings got
 * the new default.
 *
 * That failure is completely silent. Nothing throws, no request 400s, and the
 * table looks exactly like one whose default was never changed. The only way it
 * gets caught is by asserting it here.
 *
 * WHAT MUST NOT REGRESS ALONGSIDE IT: a generation bump is a SORT reset, not a
 * state reset. Filters people built by hand and their hidden-column choices have
 * to survive it, and "I cycled sort off" has to stay off.
 */
import { readStored, writeStored } from './DataTable';

const TABLE = 'test_table';
const SORT = { key: 'modified_time', dir: 'desc' };
const OLD_SORT = { key: 'request_date', dir: 'desc' };
const CONDS = [{ key: 'event_code', op: 'Contains', values: ['AA'] }];
const HIDDEN = ['discount', 'add_ons'];

beforeEach(() => window.localStorage.clear());

describe('a page that has never bumped its sort generation', () => {
  test('the stored sort still wins, exactly as before', () => {
    // Generation 0 on both sides is the default, so every other table in the app
    // is unaffected by the mechanism existing.
    writeStored(TABLE, { conds: CONDS, sort: OLD_SORT, hidden: HIDDEN });
    const back = readStored(TABLE);
    expect(back.sort).toEqual(OLD_SORT);
    expect(back.sortStale).toBe(false);
  });

  test('sort cycled OFF stays off across a reload', () => {
    // A null sort is a choice, not an absence, and must not fall back to the
    // page's default.
    writeStored(TABLE, { conds: [], sort: null, hidden: [] });
    const back = readStored(TABLE);
    expect(back.sort).toBeNull();
    expect(back.sortStale).toBe(false);
  });
});

describe('a page that has bumped', () => {
  test('a sort stored under the OLD generation is retired', () => {
    // THE ACTUAL BUG. Written before the bump, read after it.
    writeStored(TABLE, { conds: CONDS, sort: OLD_SORT, hidden: HIDDEN }, 0);
    const back = readStored(TABLE, 1);
    expect(back.sortStale).toBe(true);
    expect(back.sort).toBeNull();
  });

  test('retiring the sort keeps the filters and the hidden columns', () => {
    // The reason STORE_VERSION was not simply bumped: that discards the whole
    // blob on every table, so a moved sort default would also throw away filter
    // sets built by hand.
    writeStored(TABLE, { conds: CONDS, sort: OLD_SORT, hidden: HIDDEN }, 0);
    const back = readStored(TABLE, 1);
    expect(back.conds).toHaveLength(1);
    expect(back.conds[0].key).toBe('event_code');
    expect(back.conds[0].values).toEqual(['AA']);
    expect(back.hidden).toEqual(HIDDEN);
  });

  test('the reset happens ONCE, and the next choice sticks', () => {
    // Someone who re-picks the old column after the bump must keep it forever.
    // The generation goes with the write, which is what makes that true.
    writeStored(TABLE, { conds: CONDS, sort: OLD_SORT, hidden: HIDDEN }, 0);
    expect(readStored(TABLE, 1).sortStale).toBe(true);

    writeStored(TABLE, { conds: CONDS, sort: OLD_SORT, hidden: HIDDEN }, 1);
    const back = readStored(TABLE, 1);
    expect(back.sortStale).toBe(false);
    expect(back.sort).toEqual(OLD_SORT);
  });

  test('a sort stored AT the current generation is left alone', () => {
    writeStored(TABLE, { conds: [], sort: SORT, hidden: [] }, 1);
    const back = readStored(TABLE, 1);
    expect(back.sortStale).toBe(false);
    expect(back.sort).toEqual(SORT);
  });

  test('sort cycled off AFTER the bump stays off', () => {
    // The one case a naive "null means retired" reading would break.
    writeStored(TABLE, { conds: [], sort: null, hidden: [] }, 1);
    const back = readStored(TABLE, 1);
    expect(back.sort).toBeNull();
    expect(back.sortStale).toBe(false);
  });
});

describe('the shapes storage can be in', () => {
  test('a browser with nothing stored yields null, so defaultSort applies', () => {
    expect(readStored(TABLE, 1)).toBeNull();
  });

  test('a table with no id neither reads nor writes', () => {
    expect(readStored(null, 1)).toBeNull();
    expect(() => writeStored(null, { conds: [], sort: SORT, hidden: [] }, 1)).not.toThrow();
    expect(window.localStorage.length).toBe(0);
  });

  test('corrupt JSON is null rather than a thrown render', () => {
    window.localStorage.setItem('iqhub.table.' + TABLE, '{not json');
    expect(readStored(TABLE, 1)).toBeNull();
  });

  test('a blob from an older STORE_VERSION is discarded whole', () => {
    // That mechanism is still there for real schema changes; this only checks
    // the sort generation did not accidentally replace it.
    window.localStorage.setItem('iqhub.table.' + TABLE, JSON.stringify({
      version: 0, sortVersion: 1, sort: SORT, conds: CONDS, hidden: HIDDEN,
    }));
    expect(readStored(TABLE, 1)).toBeNull();
  });
});
