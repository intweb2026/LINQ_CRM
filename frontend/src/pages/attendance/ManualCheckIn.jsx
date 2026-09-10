import { useState } from 'react';
import { Icon } from '../../lib/icons';
import * as api from '../../api/attendance';

/**
 * The ONE path in this module with a confirmation step, and the reason it has
 * one: nobody presented a badge, so a person was typed rather than read, and
 * the operator has to see who they are about to mark present before they do it.
 *
 * A SINGLE MATCH GOES THROUGH THE PANEL TOO. One hit on a partial name FEELS
 * conclusive and is not — "Sam" matches one person today and two after the next
 * booking lands, and auto-confirming the single case means the flow behaves
 * differently on the day it matters. So: search, select, read the panel, mark
 * present.
 *
 * THE WHOLE SELECTED ROW IS HELD IN STATE, not its id. Otherwise a search typed
 * while the panel is open re-resolves the id against a new result set and swaps
 * the person under the button.
 *
 * NO NEW ENDPOINT. /roster/ already returns the details AND each match's badge
 * token, and Mark present posts that token to the same /scan/ the camera posts
 * to. This is a UI gate over a server that re-verifies everything: the event,
 * the roster membership, and whether this operator may work that door.
 */

export default function ManualCheckIn({ event, onCheckIn }) {
  const [name, setName] = useState('');
  const [company, setCompany] = useState('');
  const [hits, setHits] = useState(null);      // null = not searched yet
  const [chosen, setChosen] = useState(null);  // the whole row
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function find(e) {
    e.preventDefault();
    const term = [name, company].map((s) => s.trim()).filter(Boolean).join(' ');
    if (!term) return;
    setBusy(true);
    setChosen(null);
    try {
      // Both fields are sent as ONE term, because the server ANDs the words
      // across the name, company and email columns already. Two separate
      // server-side filters would be a second search rule to keep in step.
      const data = await api.roster(event, term);
      setHits(data.results || []);
      setFailed(false);
    } catch {
      setHits([]);
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  async function mark() {
    setBusy(true);
    await onCheckIn(chosen.token, 'manual');
    setBusy(false);
    setChosen(null);
    setHits(null);
    setName('');
    setCompany('');
  }

  if (chosen) {
    return (
      <div className="att-panel">
        <h4>Mark this person present?</h4>
        <dl className="att-panel-dl">
          <dt>Name</dt><dd>{chosen.attendee_name}</dd>
          <dt>Type</dt>
          <dd>{chosen.attendee_type === 'speaker' ? 'Speaker' : 'Delegate'}</dd>
          <dt>Company</dt><dd>{chosen.company_name || '—'}</dd>
          <dt>Email</dt><dd>{chosen.attendee_email || '—'}</dd>
        </dl>
        <div className="att-panel-act">
          <button className="btn pri" onClick={mark} disabled={busy}>
            {busy ? 'Recording…' : 'Mark present'}
          </button>
          <button className="btn" onClick={() => setChosen(null)} disabled={busy}>
            Cancel
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="att-manual">
      <form className="att-manual-form" onSubmit={find}>
        <label>
          <span>Name</span>
          <input className="in" value={name} autoComplete="off"
            placeholder="Any part of the name" onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          <span>Company <i>optional</i></span>
          <input className="in" value={company} autoComplete="off"
            placeholder="Narrows the search" onChange={(e) => setCompany(e.target.value)} />
        </label>
        <button className="btn pri" type="submit" disabled={busy || !name.trim()}>
          {busy ? 'Searching…' : 'Find'}
        </button>
      </form>

      {failed ? (
        <p className="att-quiet">Could not reach the server. Try again.</p>
      ) : hits === null ? (
        <p className="att-quiet">
          Search the confirmed roster for somebody whose badge will not scan.
        </p>
      ) : !hits.length ? (
        <p className="att-quiet">
          Nobody on this event's confirmed roster matches that. Every word has to
          match somewhere on the row, so try one word.
        </p>
      ) : (
        <ul className="att-list">
          {hits.map((row) => (
            <li key={row.delegate_id} className="att-row">
              <div className="att-row-who">
                <b>{row.attendee_name}</b>
                <span>{row.company_name || '—'}</span>
              </div>
              {row.checked_in_at ? (
                <span className="att-row-in"><Icon name="check" size={14} /> In</span>
              ) : (
                <button className="btn att-row-btn" onClick={() => setChosen(row)}>
                  Select
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
