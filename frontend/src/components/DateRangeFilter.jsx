import { Seg } from './UI';
import { MON, nf } from '../lib/helpers';
import { DASH_PERIODS, DASH_PERIOD_LABEL } from '../lib/constants';

/**
 * The CRM's date-range presets, as one control.
 *
 * WHY THIS IS A COMPONENT AND NOT FOUR COPIES
 * The Dashboard, Bookings, Ticket Central, Paper Review and Proposal Submission
 * all want the same four buttons. Five hand-rolled copies is five chances for
 * the labels to drift from the keys the backend accepts, and the failure is
 * quiet: a button reading "Last month" that sends `last_30_days` is not wrong
 * until someone asks which it meant.
 *
 * The keys come from DASH_PERIODS, which is the wire contract shared with
 * backend/accounts/period_filter.py PERIOD_DAYS. The backend 400s on anything
 * else, so a typo here surfaces immediately rather than silently widening the
 * window to all time.
 *
 * `count` / `noun` — the number of rows the window currently holds, shown beside
 * the buttons. Worth the space: "Last 7 days" over an empty table is ambiguous
 * between "nothing happened" and "the filter is broken", and a caption reading
 * "52 of 3,001 bookings" settles it.
 *
 * `from` / `to` are the resolved dates as the SERVER computed them. Passed in
 * rather than derived here on purpose — the window is the server's arithmetic
 * (its today is the UTC date; see period_filter.today_for_period), and a second
 * implementation in the browser would be one timezone away from disagreeing with
 * the rows underneath it.
 */
export default function DateRangeFilter({
  value, onChange, count = null, total = null, noun = 'records',
  from = null, to = null, note = null, loading = false, actions = null,
}) {
  const isAll = value === 'all';
  const range = from && to ? fmtRange(from, to) : null;

  const parts = [];
  if (count != null) {
    parts.push(
      isAll || total == null || total === count
        ? `${nf(count)} ${noun}`
        : `${nf(count)} of ${nf(total)} ${noun}`,
    );
  }
  if (range) parts.push(range);
  if (note) parts.push(note);

  return (
    <div className="dfl">
      <div className="dfl-t">
        <span className="n">
          Date range<span className="tg bg-neutral">{DASH_PERIOD_LABEL[value] || value}</span>
          {loading ? <span className="s" style={{ fontWeight: 500 }}>updating…</span> : null}
        </span>
        <span className="s">{parts.length ? parts.join(' · ') : 'Every record on file'}</span>
      </div>
      <div className="dfl-r">
        {/* Bookings and Ticket Central fold their import/create buttons into
            the tab row above this one (Tabs' own `actions` slot) because they
            have a tab row to fold into. Paper Review and Proposal Submission
            don't — no status tabs — so without this slot those buttons sat in
            a PageHead row of their own above everything, costing this page a
            whole extra row of height that the tabbed pages never pay. */}
        {actions}
        <Seg
          options={DASH_PERIODS.map((p) => ({ value: p.k, label: p.l }))}
          value={value}
          onChange={onChange}
        />
      </div>
    </div>
  );
}

// 'T00:00:00' is load-bearing: `new Date('2026-08-07')` is parsed as UTC
// midnight and renders as the 6th for anyone west of Greenwich, so a window
// caption would name a day the server never sent.
function fmtRange(from, to) {
  const d = (iso) => new Date(iso + 'T00:00:00');
  const a = d(from), b = d(to);
  const same = a.getFullYear() === b.getFullYear();
  const one = (x, withYear) => x.getDate() + ' ' + MON[x.getMonth()] + (withYear ? ' ' + x.getFullYear() : '');
  return one(a, !same) + ' → ' + one(b, true);
}
