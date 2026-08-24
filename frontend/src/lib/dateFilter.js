/**
 * lib/dateFilter.js
 * ─────────────────
 * The vocabulary Advanced Filters speak about DATE columns, in one place.
 *
 * WHY A SEPARATE MODULE
 * A date filter is not a text filter with a different keyboard. "Contains 2026"
 * is a substring match that happens to look like a year; "Before 1 Aug 2026" is
 * an ordinal comparison the server has a real operator for. Those are different
 * enough that mixing them into DataTable's text-condition code would leave every
 * operator list, every chip and every server criterion carrying a branch. They
 * live here instead, and DataTable asks this module three questions: is this
 * condition active, what does it read as, and which rows does it pass.
 *
 * THE VALUE IS ALWAYS A DATE THE USER PICKED
 * There are no relative presets. Every operator takes calendar dates off a date
 * input, one for Is, Is Not, Before and After, two for Between, and the operator
 * does the rest. Nothing is derived from the clock, so a filter set today still
 * means the same dates tomorrow, and the dates in the chip are the dates in the
 * query.
 *
 * EVERYTHING IS UTC
 * A picked date is a plain calendar date with no hour in it, and a row's cell is
 * read as its UTC date, because settings.TIME_ZONE is "UTC" and every other date
 * in the CRM is already reckoned that way. Reading a timestamp as browser-local
 * would land a row on a different day for anyone east of Greenwich, which is the
 * drift the DateRangeFilter component refuses to reintroduce by recomputing the
 * server's window locally.
 */

// ── Operators ────────────────────────────────────────────────────────────────
// Order is the order they are offered in. `Is` first because it is what most
// date filters want, and the two emptiness tests sit next to it because they
// are the ones that need no value at all.
export const DATE_OPS = ['Is', 'Is Not', 'Is Empty', 'Is Not Empty', 'Before', 'After', 'Between'];

/** Operators that take no value; a condition using one is active immediately. */
export const DATE_NO_VALUE_OPS = ['Is Empty', 'Is Not Empty'];

/** Operators whose value is a single calendar date. */
export const DATE_EXACT_OPS = ['Is', 'Is Not', 'Before', 'After'];

/** Operators whose value is a pair of calendar dates. */
export const DATE_RANGE_OPS = ['Between'];

export function isDateOp(op) {
  return DATE_OPS.includes(op);
}

export function dateOpLabel(op) {
  return {
    Is: 'is', 'Is Not': 'is not', Before: 'before', After: 'after',
    Between: 'between', 'Is Empty': 'is empty', 'Is Not Empty': 'is not empty',
  }[op] || String(op).toLowerCase();
}

// ── UTC date arithmetic ──────────────────────────────────────────────────────
/**
 * A UTC-midnight Date, with the two-digit-year trap disarmed.
 *
 * ECMA-262 says Date.UTC maps a year of 0-99 onto 1900-1999, so Date.UTC(2, 7,
 * 25) is 1902-08-25, not the year 2. That is not a curiosity here: a date input
 * mid-edit holds "0002-08-25" after the first digit of the year is typed, and
 * rendering it through the naive constructor put "25 Aug 1902" on screen while
 * the user was still typing "2026". setUTCFullYear has no such remapping, so it
 * is used for every year the shortcut would have moved.
 */
function u(y, m, d) {
  const t = new Date(Date.UTC(y, m, d));
  if (y >= 0 && y <= 99) t.setUTCFullYear(y, m, d);
  return t;
}

/** 'YYYY-MM-DD' for a UTC-midnight Date. */
export function iso(d) { return d.toISOString().slice(0, 10); }

/**
 * The earliest year a picked date may carry.
 *
 * Not a validation rule about what dates are reasonable; a guard against
 * READING A DATE THE USER HAS NOT FINISHED TYPING. A native date input reports
 * a complete, well-formed value after every keystroke in the year segment, so
 * typing 2026 walks through 0002, 0020 and 0202 — each of them a real date the
 * filter would otherwise commit, refetch on, and print back as a year nobody
 * asked for. No column in this CRM holds a date before the first millennium,
 * so treating a year under four digits as "still typing" costs nothing and
 * makes the control behave the way it looks like it should.
 */
export const MIN_YEAR = 1000;

/** Is this a date the user has finished typing? See MIN_YEAR. */
export function isCompleteDate(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(s || '').trim());
  return !!m && Number(m[1]) >= MIN_YEAR;
}

/** The value for a date input's `min`, so the browser constrains it too. */
export const DATE_INPUT_MIN = `${MIN_YEAR}-01-01`;

/** A 'YYYY-MM-DD' string back as a UTC-midnight Date, or null. */
export function parseISO(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(s || '').trim());
  if (!m) return null;
  const d = u(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return Number.isNaN(d.getTime()) ? null : d;
}

// ── Calendar grid ────────────────────────────────────────────────────────────
/**
 * The month grid the picker draws, computed here rather than in the component.
 *
 * WHY THERE IS A CALENDAR IN THIS CODEBASE AT ALL
 * These fields used `<input type="date">`, whose calendar is a BROWSER-NATIVE
 * popup drawn outside the page and outside React's control. Two faults followed
 * from that and neither was fixable from our side. It reports a complete value
 * after every keystroke in the year segment, so typing 2026 committed 0002,
 * 0020 and 0202 on the way. And when anything re-rendered the filter panel,
 * React wrote `input.value` back to a node whose picker was open; Chrome resets
 * the picker to that value, which is why navigating to another month appeared
 * to select a date on its own and apply the filter. A calendar we render is a
 * calendar that changes nothing until a day is clicked.
 *
 * A month is addressed as 'YYYY-MM', a prefix of the ISO dates everything else
 * here speaks, so moving between the two is a slice rather than a conversion.
 */
export const WEEKDAY_INITIALS = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];
export const WEEKDAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday',
  'Friday', 'Saturday', 'Sunday'];
const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'];

/** Today as 'YYYY-MM-DD'. Used ONLY to highlight the day and to jump the view. */
export function todayISO() {
  const n = new Date();
  return iso(u(n.getUTCFullYear(), n.getUTCMonth(), n.getUTCDate()));
}

/** The month a date belongs to, or this month when there is no date yet. */
export function monthOf(isoDate) {
  return isCompleteDate(isoDate) ? isoDate.slice(0, 7) : todayISO().slice(0, 7);
}

function ymParts(ym) {
  const m = /^(\d{4})-(\d{2})$/.exec(String(ym || ''));
  if (!m) return null;
  return { y: Number(m[1]), m: Number(m[2]) - 1 };
}

/** 'YYYY-MM' moved by whole months or years, normalised across the boundary. */
export function shiftMonth(ym, months, years) {
  const p = ymParts(ym) || ymParts(todayISO().slice(0, 7));
  const d = u(p.y + (years || 0), p.m + (months || 0), 1);
  return iso(d).slice(0, 7);
}

/** 'August 2026' for the picker's header. */
export function monthLabel(ym) {
  const p = ymParts(ym);
  if (!p) return '';
  return `${MONTH_NAMES[p.m]} ${p.y}`;
}

/**
 * The 42 ISO dates a month grid shows, Monday first.
 *
 * Always six weeks, never five: a grid that changes height between months makes
 * the row under the cursor move as you navigate, which is its own way of
 * clicking a day nobody meant to click.
 */
export function monthGrid(ym) {
  const p = ymParts(ym);
  if (!p) return [];
  const first = u(p.y, p.m, 1);
  // getUTCDay() is Sunday-based; +6 %7 rotates it to a Monday-based offset.
  const offset = (first.getUTCDay() + 6) % 7;
  const out = [];
  for (let i = 0; i < 42; i += 1) out.push(iso(u(p.y, p.m, 1 - offset + i)));
  return out;
}

/** Does this ISO date fall inside the month the grid is showing? */
export function inMonth(isoDate, ym) {
  return String(isoDate).slice(0, 7) === ym;
}

// ── The condition's own shape ────────────────────────────────────────────────
/**
 * A date condition is `{ key, op, values: [], date: {...} }`.
 *
 * `values` stays present and empty so every piece of code that reaches for
 * `cond.values` — persistence, the chip row, condActive — keeps working on a
 * date condition instead of throwing on an undefined array. The dates live in a
 * separate `date` object rather than in that list because a range is an ordered
 * PAIR, and two entries in a list of match values would be indistinguishable
 * from "either of these two days".
 *
 *   { mode: 'exact', date: '2026-08-24' }                     Is / Is Not / Before / After
 *   { mode: 'range', from: '2026-08-01', to: '2026-08-24' }   Between
 */
export function emptyDateCond(key, op) {
  return { key, op: op || 'Is', values: [], date: { mode: 'exact', date: '' } };
}

/**
 * The date payload the operator needs, carried over rather than blanked.
 *
 * Switching operator must not silently empty the filter: choosing "Before"
 * after "Is 24 Aug" keeps the 24th, and choosing "Between" after it starts the
 * range there. Re-picking the same date every time the operator changes reads
 * as the control losing it.
 */
export function dateForOp(cond, op) {
  const cur = (cond && cond.date) || {};
  if (DATE_NO_VALUE_OPS.includes(op)) return cur;

  if (DATE_RANGE_OPS.includes(op)) {
    if (cur.mode === 'range') return cur;
    return { mode: 'range', from: cur.date || '', to: cur.date || '' };
  }

  if (cur.mode !== 'range') return { mode: 'exact', date: cur.date || '' };
  // Coming back from a range, the START date is the one kept: it is the edge
  // every remaining operator is about, and it is the one the user picked first.
  return { mode: 'exact', date: cur.from || '' };
}

/**
 * The inclusive [from, to] window a condition covers, or null when it has none.
 *
 * Defined for Is, Is Not and Between. Is and Is Not cover a single whole day,
 * which is a window of one, and saying so here is what lets a DateTimeField be
 * matched across the whole of that day rather than at midnight. Before and
 * After are half-open by nature; ask dateCondBound() for their single edge.
 */
export function dateCondWindow(cond) {
  const d = cond && cond.date;
  if (!d) return null;
  if (d.mode === 'range') {
    // isCompleteDate, not merely a truthiness test: a half-typed year is a
    // valid-looking date the filter must not act on. See MIN_YEAR.
    if (!isCompleteDate(d.from) || !isCompleteDate(d.to)) return null;
    // A backwards range is read as the range the user drew rather than as an
    // empty result: typing the end date first is a slip, not a request for
    // zero rows.
    return d.from <= d.to ? { from: d.from, to: d.to } : { from: d.to, to: d.from };
  }
  return isCompleteDate(d.date) ? { from: d.date, to: d.date } : null;
}

/** The single ISO date a Before/After condition compares against, or null. */
export function dateCondBound(cond) {
  const d = cond && cond.date;
  if (!d) return null;
  const raw = d.mode === 'range' ? d.from : d.date;
  return isCompleteDate(raw) ? raw : null;
}

/** Mirrors DataTable's condActive for a date condition. */
export function dateCondActive(cond) {
  if (!cond) return false;
  if (DATE_NO_VALUE_OPS.includes(cond.op)) return true;
  if (cond.op === 'Before' || cond.op === 'After') return !!dateCondBound(cond);
  return !!dateCondWindow(cond);
}

// ── Reading a row's date ─────────────────────────────────────────────────────
/**
 * A cell value as a plain 'YYYY-MM-DD' UTC date, or null when it holds none.
 *
 * Three shapes reach this: a DateField's 'YYYY-MM-DD', a DateTimeField's ISO
 * timestamp, and the em dash the table renders for an empty cell. A bare
 * timestamp with no offset is read as UTC — DRF renders 'Z' and the whole CRM
 * is UTC, whereas `new Date('2026-08-24T18:30:00')` would be read as browser
 * LOCAL time by the language spec and could land the row on the wrong day.
 */
export function rowDateISO(v) {
  if (v == null) return null;
  if (v instanceof Date) return Number.isNaN(v.getTime()) ? null : iso(v);
  const s = String(v).trim();
  if (!s || s === '—') return null;
  const m = /^(\d{4}-\d{2}-\d{2})(?:[T ](.+))?$/.exec(s);
  if (!m) {
    const d = new Date(s);
    return Number.isNaN(d.getTime()) ? null : iso(d);
  }
  if (!m[2]) return m[1];
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(s);
  const d = new Date(hasZone ? s.replace(' ', 'T') : `${m[1]}T${m[2]}Z`);
  return Number.isNaN(d.getTime()) ? m[1] : iso(d);
}

/**
 * Does this row pass the condition? The browser-side twin of the server's Q.
 *
 * ISO dates compare correctly as STRINGS — 'YYYY-MM-DD' is lexicographically
 * ordered — so no Date objects are built per row here. On a 130,000-row table
 * that is the difference between a filter and a freeze.
 *
 * A row with NO date fails every operator except "is empty" and "is not", and
 * that one exception is not a preference, it is what the server does. Django
 * compiles a negated lookup on a nullable column to
 * `NOT (col = x AND col IS NOT NULL)`, so an undated row comes back from
 * `is_not` and from `not_between` alike. The browser-side twin has to agree:
 * these two evaluators run over the same table, one when the field is
 * registered server-side and one when it is not, and a row that appears under a
 * filter in Bookings and vanishes under the same filter in Events would be a
 * far worse answer than either convention alone.
 */
export function dateCondPasses(row, cond) {
  const val = rowDateISO(row[cond.key]);
  if (cond.op === 'Is Empty') return val === null;
  if (cond.op === 'Is Not Empty') return val !== null;
  if (val === null) return cond.op === 'Is Not';

  if (cond.op === 'Before' || cond.op === 'After') {
    const bound = dateCondBound(cond);
    if (!bound) return true;
    return cond.op === 'Before' ? val < bound : val > bound;
  }

  const win = dateCondWindow(cond);
  if (!win) return true;
  const inside = val >= win.from && val <= win.to;
  return cond.op === 'Is Not' ? !inside : inside;
}

// ── Reading it back ──────────────────────────────────────────────────────────
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** '24 Aug 2026' for an ISO date, formatted without ever leaving UTC. */
export function fmtISO(s) {
  const d = parseISO(s);
  if (!d) return s || '';
  return `${d.getUTCDate()} ${MON[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

/** '1 Aug → 24 Aug 2026' for a window, dropping the repeated year. */
export function fmtWindow(win) {
  if (!win) return '';
  if (win.from === win.to) return fmtISO(win.from);
  const a = parseISO(win.from), b = parseISO(win.to);
  if (!a || !b) return `${fmtISO(win.from)} → ${fmtISO(win.to)}`;
  const sameYear = a.getUTCFullYear() === b.getUTCFullYear();
  const one = (x, withYear) => `${x.getUTCDate()} ${MON[x.getUTCMonth()]}${withYear ? ` ${x.getUTCFullYear()}` : ''}`;
  return `${one(a, !sameYear)} → ${one(b, true)}`;
}

/** What the chip shows for the value half of the condition. */
export function dateValueLabel(cond) {
  if (DATE_RANGE_OPS.includes(cond.op)) {
    const w = dateCondWindow(cond);
    return w ? fmtWindow(w) : 'Select a range…';
  }
  const bound = dateCondBound(cond);
  return bound ? fmtISO(bound) : 'Select a date…';
}

/** The chip text: "is 24 Aug 2026", "between 1 Aug → 24 Aug 2026". */
export function dateCondText(cond) {
  if (DATE_NO_VALUE_OPS.includes(cond.op)) return dateOpLabel(cond.op);
  return `${dateOpLabel(cond.op)} ${dateValueLabel(cond)}`;
}
