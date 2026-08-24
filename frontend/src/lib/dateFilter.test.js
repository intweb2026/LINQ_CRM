/**
 * lib/dateFilter.test.js
 * ──────────────────────
 * What each date operator does with the date the user picked.
 *
 * WHY THIS IS TESTED AND THE REST OF lib/ IS NOT
 * Every other failure in the Advanced Filter panel is visible: a broken control
 * does not open, a broken chip reads wrong. An off-by-one at a boundary is not.
 * It returns rows, the count looks plausible, and the single day it wrongly
 * included or dropped is only ever noticed by someone reconciling two screens.
 * Every operator here turns on exactly such a boundary — "before the 25th" is
 * the 24th and earlier, "between" includes both ends — so each is pinned.
 */
import {
  dateCondActive, dateCondBound, dateCondPasses, dateCondText, dateCondWindow,
  dateForOp, dateValueLabel, fmtISO, fmtWindow, inMonth, isCompleteDate, iso,
  monthGrid, monthLabel, monthOf, parseISO, rowDateISO, shiftMonth,
} from './dateFilter';

const cond = (op, date) => ({ key: 'd', op, values: [], date });
const exact = (date) => ({ mode: 'exact', date });
const range = (from, to) => ({ mode: 'range', from, to });
const row = (d) => ({ d });

describe('the window an operator covers', () => {
  test('a single picked date is a window of one whole day', () => {
    // Load-bearing for DateTimeField columns: the criterion built from this
    // covers the whole of the day rather than the instant of midnight.
    expect(dateCondWindow(cond('Is', exact('2026-08-25'))))
      .toEqual({ from: '2026-08-25', to: '2026-08-25' });
  });

  test('Between spans both picked dates', () => {
    expect(dateCondWindow(cond('Between', range('2026-08-01', '2026-08-31'))))
      .toEqual({ from: '2026-08-01', to: '2026-08-31' });
  });

  test('a backwards range is read as the range the user drew', () => {
    expect(dateCondWindow(cond('Between', range('2026-08-31', '2026-08-01'))))
      .toEqual({ from: '2026-08-01', to: '2026-08-31' });
  });

  test('a half-filled range narrows nothing until both dates are picked', () => {
    const half = cond('Between', range('2026-08-01', ''));
    expect(dateCondWindow(half)).toBeNull();
    expect(dateCondActive(half)).toBe(false);
  });

  test('the emptiness operators are active with no date at all', () => {
    expect(dateCondActive(cond('Is Empty', {}))).toBe(true);
    expect(dateCondActive(cond('Is Not Empty', {}))).toBe(true);
    expect(dateCondActive(cond('Is', exact('')))).toBe(false);
  });
});

describe('condition evaluation', () => {
  test('Is matches that day and nothing either side of it', () => {
    const c = cond('Is', exact('2026-08-25'));
    expect(dateCondPasses(row('2026-08-25'), c)).toBe(true);
    expect(dateCondPasses(row('2026-08-24'), c)).toBe(false);
    expect(dateCondPasses(row('2026-08-26'), c)).toBe(false);
  });

  test('Is matches a timestamp anywhere in that day, not just midnight', () => {
    const c = cond('Is', exact('2026-08-25'));
    expect(dateCondPasses(row('2026-08-25T00:00:00Z'), c)).toBe(true);
    expect(dateCondPasses(row('2026-08-25T23:59:00Z'), c)).toBe(true);
    expect(dateCondPasses(row('2026-08-26T00:00:00Z'), c)).toBe(false);
  });

  test('Before and After are strict, so the picked day is in neither', () => {
    const before = cond('Before', exact('2026-08-25'));
    const after = cond('After', exact('2026-08-25'));
    expect(dateCondPasses(row('2026-08-24'), before)).toBe(true);
    expect(dateCondPasses(row('2026-08-25'), before)).toBe(false);
    expect(dateCondPasses(row('2026-08-25'), after)).toBe(false);
    expect(dateCondPasses(row('2026-08-26'), after)).toBe(true);
  });

  test('Between includes both of the days that were picked', () => {
    const c = cond('Between', range('2026-08-01', '2026-08-31'));
    expect(dateCondPasses(row('2026-08-01'), c)).toBe(true);
    expect(dateCondPasses(row('2026-08-31'), c)).toBe(true);
    expect(dateCondPasses(row('2026-07-31'), c)).toBe(false);
    expect(dateCondPasses(row('2026-09-01'), c)).toBe(false);
  });

  test('a year boundary is just another comparison', () => {
    const c = cond('Between', range('2025-12-30', '2026-01-02'));
    ['2025-12-30', '2025-12-31', '2026-01-01', '2026-01-02'].forEach((d) => {
      expect(dateCondPasses(row(d), c)).toBe(true);
    });
    expect(dateCondPasses(row('2025-12-29'), c)).toBe(false);
    expect(dateCondPasses(row('2026-01-03'), c)).toBe(false);
  });

  test('an undated row: empty passes, is-not passes, everything else fails', () => {
    // "is not" returning the undated row is not a preference — it is what
    // Django's negated lookup does on a nullable column, and the two evaluators
    // must not disagree. See dateCondPasses' docstring.
    expect(dateCondPasses(row(null), cond('Is Empty', {}))).toBe(true);
    expect(dateCondPasses(row(null), cond('Is Not Empty', {}))).toBe(false);
    expect(dateCondPasses(row(null), cond('Is Not', exact('2026-08-25')))).toBe(true);
    expect(dateCondPasses(row(null), cond('Is', exact('2026-08-25')))).toBe(false);
    expect(dateCondPasses(row(null), cond('Before', exact('2026-08-25')))).toBe(false);
  });

  test('Is Not is the exact complement of Is on a dated row', () => {
    const c = exact('2026-08-25');
    ['2026-08-24', '2026-08-25', '2026-08-26'].forEach((d) => {
      expect(dateCondPasses(row(d), cond('Is Not', c)))
        .toBe(!dateCondPasses(row(d), cond('Is', c)));
    });
  });

  test('an incomplete condition narrows nothing rather than everything', () => {
    // Between the moment a user checks the field and the moment they pick a
    // date, the table must not empty itself.
    expect(dateCondPasses(row('2026-08-25'), cond('Is', exact('')))).toBe(true);
    expect(dateCondPasses(row('2026-08-25'), cond('Before', exact('')))).toBe(true);
  });
});

describe('reading a row cell', () => {
  test('the shapes a date cell arrives in', () => {
    expect(rowDateISO('2026-08-25')).toBe('2026-08-25');
    expect(rowDateISO('2026-08-25T18:30:00Z')).toBe('2026-08-25');
    expect(rowDateISO(null)).toBeNull();
    expect(rowDateISO('')).toBeNull();
    expect(rowDateISO('—')).toBeNull();
  });

  test('a bare timestamp is read as UTC, not as browser-local time', () => {
    // `new Date('2026-08-25T23:30:00')` is LOCAL by the language spec, and in
    // any timezone east of UTC that lands the row on the 26th.
    expect(rowDateISO('2026-08-25T23:30:00')).toBe('2026-08-25');
    expect(rowDateISO('2026-08-25 00:15:00')).toBe('2026-08-25');
  });

  test('parseISO and iso round-trip a picked date without leaving UTC', () => {
    expect(iso(parseISO('2028-02-29'))).toBe('2028-02-29');
    expect(iso(parseISO('2026-01-01'))).toBe('2026-01-01');
    expect(parseISO('not a date')).toBeNull();
  });
});

describe('switching operator keeps the filter meaning something', () => {
  test('a picked date survives every single-date operator', () => {
    const c = cond('Is', exact('2026-08-25'));
    ['Is Not', 'Before', 'After'].forEach((op) => {
      expect(dateForOp(c, op)).toEqual({ mode: 'exact', date: '2026-08-25' });
    });
  });

  test('moving to Between starts the range at the date already picked', () => {
    expect(dateForOp(cond('Is', exact('2026-08-25')), 'Between'))
      .toEqual({ mode: 'range', from: '2026-08-25', to: '2026-08-25' });
  });

  test('moving back off Between keeps the start date', () => {
    expect(dateForOp(cond('Between', range('2026-08-01', '2026-08-31')), 'Before'))
      .toEqual({ mode: 'exact', date: '2026-08-01' });
  });

  test('a date survives a round trip through the emptiness operators', () => {
    const c = cond('Is', exact('2026-08-25'));
    const kept = dateForOp(c, 'Is Empty');
    expect(dateForOp({ ...c, op: 'Is Empty', date: kept }, 'Is'))
      .toEqual({ mode: 'exact', date: '2026-08-25' });
  });
});

describe('how the filter reads back', () => {
  test('the chip says what the filter means', () => {
    expect(dateCondText(cond('Is', exact('2026-08-25')))).toBe('is 25 Aug 2026');
    expect(dateCondText(cond('Is Empty', {}))).toBe('is empty');
    expect(dateCondText(cond('Before', exact('2026-08-25')))).toBe('before 25 Aug 2026');
    expect(dateCondText(cond('Between', range('2026-08-01', '2026-08-31'))))
      .toBe('between 1 Aug → 31 Aug 2026');
  });

  test('a range spanning two years names both of them', () => {
    expect(fmtWindow({ from: '2025-12-30', to: '2026-01-02' }))
      .toBe('30 Dec 2025 → 2 Jan 2026');
  });

  test('an unset value prompts rather than reading as a date', () => {
    expect(dateValueLabel(cond('Is', exact('')))).toBe('Select a date…');
    expect(dateValueLabel(cond('Between', range('', '')))).toBe('Select a range…');
  });
});

describe('a year still being typed is not a date', () => {
  /**
   * The reported bug, pinned at every keystroke.
   *
   * A native date input reports a COMPLETE, well-formed value after every
   * keystroke in the year segment, so typing 2026 walks through 0002, 0020 and
   * 0202. Each is a real date. Two separate faults followed from that. The
   * filter committed each one, refetching against a year nobody had asked for;
   * and Date.UTC maps a year of 0-99 onto 1900-1999, so 0002 was printed back
   * as 1902 while the user was still typing.
   */
  const typingTheYear = ['0002-08-25', '0020-08-25', '0202-08-25'];

  test('Date.UTC would remap a two-digit year; parseISO must not', () => {
    // new Date(Date.UTC(2, 7, 25)) is 1902-08-25. This is the whole bug.
    expect(iso(parseISO('0002-08-25'))).toBe('0002-08-25');
    expect(iso(parseISO('0020-08-25'))).toBe('0020-08-25');
    expect(fmtISO('0002-08-25')).toBe('25 Aug 2');
  });

  test('none of the intermediate years counts as a finished date', () => {
    typingTheYear.forEach((d) => expect(isCompleteDate(d)).toBe(false));
    expect(isCompleteDate('2026-08-25')).toBe(true);
    expect(isCompleteDate('1000-01-01')).toBe(true);
    expect(isCompleteDate('')).toBe(false);
  });

  test('the filter stays inactive until the year is finished', () => {
    typingTheYear.forEach((d) => {
      const c = cond('Is', exact(d));
      expect(dateCondActive(c)).toBe(false);
      expect(dateCondWindow(c)).toBeNull();
      // Inactive means narrowing NOTHING; the table must not empty itself
      // between the first digit of the year and the last.
      expect(dateCondPasses(row('2026-08-25'), c)).toBe(true);
    });
    const done = cond('Is', exact('2026-08-25'));
    expect(dateCondActive(done)).toBe(true);
    expect(dateCondPasses(row('2026-08-25'), done)).toBe(true);
  });

  test('Before and After ignore a half-typed year too', () => {
    typingTheYear.forEach((d) => {
      expect(dateCondBound(cond('Before', exact(d)))).toBeNull();
      expect(dateCondActive(cond('After', exact(d)))).toBe(false);
      expect(dateCondPasses(row('2026-08-25'), cond('Before', exact(d)))).toBe(true);
    });
  });

  test('a range waits for BOTH years before it means anything', () => {
    const half = cond('Between', range('0002-08-01', '2026-08-31'));
    expect(dateCondWindow(half)).toBeNull();
    expect(dateCondActive(half)).toBe(false);
    const whole = cond('Between', range('2026-08-01', '2026-08-31'));
    expect(dateCondWindow(whole)).toEqual({ from: '2026-08-01', to: '2026-08-31' });
  });

  test('nothing reads back as a year the user never typed', () => {
    // The visible symptom: "25 Aug 1902" appearing under the field.
    typingTheYear.forEach((d) => {
      expect(dateValueLabel(cond('Is', exact(d)))).toBe('Select a date…');
      expect(dateCondText(cond('Is', exact(d)))).not.toMatch(/19\d\d/);
    });
    expect(dateCondText(cond('Is', exact('2026-08-25')))).toBe('is 25 Aug 2026');
  });
});

describe('the calendar grid', () => {
  /**
   * The reported bug was that navigating to another month selected a date and
   * applied the filter. The component-level guarantee is structural — nothing
   * but a day cell's click handler can reach onChange — so what is worth
   * pinning here is the arithmetic navigation depends on. A month step that
   * lands on the wrong month is the other way a user ends up looking at days
   * they did not ask for.
   */
  test('a month is exactly six weeks, so the rows never shift as you navigate', () => {
    // A grid that changes height between months moves the row under the cursor,
    // which is its own way of clicking a day nobody meant to click.
    ['2026-02', '2026-08', '2028-02', '2026-11'].forEach((ym) => {
      expect(monthGrid(ym)).toHaveLength(42);
    });
  });

  test('the grid starts on the Monday on or before the first of the month', () => {
    // 1 Aug 2026 is a Saturday, so the grid opens on Monday 27 July.
    const aug = monthGrid('2026-08');
    expect(aug[0]).toBe('2026-07-27');
    expect(aug[5]).toBe('2026-08-01');
    // 1 Jun 2026 is itself a Monday; that month must not gain a blank week.
    expect(monthGrid('2026-06')[0]).toBe('2026-06-01');
  });

  test('every day of the month is present exactly once', () => {
    const feb = monthGrid('2028-02').filter((d) => inMonth(d, '2028-02'));
    expect(feb).toHaveLength(29);                     // 2028 is a leap year
    expect(new Set(feb).size).toBe(29);
    expect(monthGrid('2027-02').filter((d) => inMonth(d, '2027-02'))).toHaveLength(28);
    expect(monthGrid('2026-09').filter((d) => inMonth(d, '2026-09'))).toHaveLength(30);
  });

  test('the grid is contiguous, with no day repeated or skipped', () => {
    const g = monthGrid('2026-08');
    for (let i = 1; i < g.length; i += 1) {
      const prev = parseISO(g[i - 1]);
      const step = new Date(prev.getTime() + 86400000);
      expect(g[i]).toBe(iso(step));
    }
  });

  test('stepping months and years crosses the boundary correctly', () => {
    expect(shiftMonth('2026-12', 1, 0)).toBe('2027-01');
    expect(shiftMonth('2026-01', -1, 0)).toBe('2025-12');
    expect(shiftMonth('2026-08', 0, 1)).toBe('2027-08');
    expect(shiftMonth('2026-08', 0, -1)).toBe('2025-08');
    // Twelve single steps land where one year step does.
    let ym = '2026-03';
    for (let i = 0; i < 12; i += 1) ym = shiftMonth(ym, 1, 0);
    expect(ym).toBe(shiftMonth('2026-03', 0, 1));
  });

  test('the calendar opens on the selected date, or on this month when empty', () => {
    expect(monthOf('2026-08-25')).toBe('2026-08');
    // A half-typed or absent value must not send the calendar to the year 2.
    expect(monthOf('0002-08-25')).toBe(monthOf(''));
    expect(monthOf('')).toMatch(/^\d{4}-\d{2}$/);
  });

  test('the header names the month being viewed, not the one selected', () => {
    expect(monthLabel('2026-08')).toBe('August 2026');
    expect(monthLabel('2027-01')).toBe('January 2027');
    expect(monthLabel('nonsense')).toBe('');
  });

  test('navigating cannot change the condition, because a view is not a value', () => {
    // The structural guarantee, stated where it can be checked: the month on
    // screen is a separate thing from the picked date, and moving one leaves
    // the other exactly as it was.
    const c = cond('Is', exact('2026-08-25'));
    let view = monthOf(c.date.date);
    ['next', 'next', 'prev'].forEach((d) => { view = shiftMonth(view, d === 'next' ? 1 : -1, 0); });
    expect(view).toBe('2026-09');
    expect(c.date.date).toBe('2026-08-25');
    expect(dateCondWindow(c)).toEqual({ from: '2026-08-25', to: '2026-08-25' });
    expect(dateCondText(c)).toBe('is 25 Aug 2026');
  });
});
