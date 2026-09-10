import { useEffect, useState } from 'react';
import { EmptyState } from '../../components/UI';
import { Icon } from '../../lib/icons';
import * as api from '../../api/attendance';

/**
 * The confirmed roster for one event, and a direct Check in per row.
 *
 * WHY NO CONFIRMATION STEP HERE. The row already shows the person's name,
 * company, email and type before the button is pressed, so the operator has
 * read the whole record they are acting on — which is exactly what the manual
 * search flow has to add a panel to achieve. Adding a second Are-you-sure to a
 * row that already names its person is a tap per attendee for nothing.
 *
 * THE SEARCH IS THE SERVER'S. The term goes to /roster/?search=, which splits it
 * on whitespace and ANDs the tokens across the name, company and email columns.
 * A client-side `includes` over a joined string cannot do that, and would fail
 * on the stored names that carry stray internal whitespace.
 *
 * `checked_in_at` COMES BACK ON THE ROW, so a person already in reads as a
 * timestamp rather than as another button to press.
 */

const DEBOUNCE_MS = 250;

const time = (value) => {
  if (!value) return '';
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? '' : d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
};

export default function AttendeeRoster({ event, mayScan, onCheckIn, reloadKey }) {
  const [term, setTerm] = useState('');
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [pending, setPending] = useState(null);

  useEffect(() => {
    if (!event) return undefined;
    let live = true;
    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const data = await api.roster(event, term.trim());
        if (live) { setRows(data.results || []); setFailed(false); }
      } catch {
        if (live) { setRows([]); setFailed(true); }
      } finally {
        if (live) setLoading(false);
      }
    }, term ? DEBOUNCE_MS : 0);
    return () => { live = false; clearTimeout(timer); };
  }, [event, term, reloadKey]);

  async function check(row) {
    setPending(row.delegate_id);
    // The token the roster already handed out, posted to the same scan endpoint
    // the camera posts to. The server re-verifies every one of its checks, so
    // this path cannot record anything a scan could not.
    await onCheckIn(row.token, 'manual');
    setPending(null);
  }

  return (
    <div className="att-roster">
      <label className="att-search">
        <Icon name="filter" size={14} />
        <input
          className="in" value={term} autoComplete="off"
          placeholder="Search name, company or email…"
          onChange={(e) => setTerm(e.target.value)}
        />
        {term ? (
          <button className="att-search-x" onClick={() => setTerm('')} aria-label="Clear">
            <Icon name="x" size={14} />
          </button>
        ) : null}
      </label>

      {failed ? (
        <EmptyState icon="warn" title="Could not load the roster"
          body="Could not reach the server. Check the connection and try again." />
      ) : loading && !rows.length ? (
        <p className="att-quiet">Loading the roster…</p>
      ) : !rows.length ? (
        <EmptyState
          icon="users"
          title={term ? 'Nobody matches that' : 'Nobody is confirmed on this event yet'}
          body={term
            ? 'Every word has to match somewhere on the row. Try one word.'
            : 'The roster is the Pre-Event Docs check-in sheet, so a booking '
              + 'appears here once its payment status is Paid, Paid (Transferred) '
              + 'or Pending.'}
        />
      ) : (
        <ul className="att-list">
          {rows.map((row) => (
            <li key={row.delegate_id}
              className={'att-row' + (row.checked_in_at ? ' att-in' : '')}>
              <div className="att-row-who">
                <b>{row.attendee_name}</b>
                <span>
                  {[row.company_name, row.position].filter(Boolean).join('  ·  ')}
                </span>
              </div>
              <span className={`att-tag att-tag-${row.attendee_type}`}>
                {row.attendee_type === 'speaker' ? 'Speaker' : 'Delegate'}
              </span>
              {row.checked_in_at ? (
                <span className="att-row-in">
                  <Icon name="check" size={14} /> {time(row.checked_in_at)}
                </span>
              ) : mayScan ? (
                <button className="btn att-row-btn" disabled={pending === row.delegate_id}
                  onClick={() => check(row)}>
                  {pending === row.delegate_id ? 'Recording…' : 'Check in'}
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
