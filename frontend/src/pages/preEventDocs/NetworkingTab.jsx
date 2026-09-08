import { useRef, useState } from 'react';
import { NumField, Seg, EmptyState } from '../../components/UI';
import { Icon } from '../../lib/icons';
import { toExcel, printElement, fileName } from '../../lib/exportSheet';
import * as pedApi from '../../api/preEventDocs';

/**
 * Speed networking.
 *
 * TWO VIEWS OF ONE DRAW, and both are needed by different people. `cards` is
 * per PERSON, which is what goes on an attendee's table card. `room` is per
 * TABLE per ROUND, which is what whoever lays the room out needs; the workbook
 * only ever produced the first, so setting the room up meant reading a
 * per-person list sideways.
 *
 * WHY A DRAW IS STORED RATHER THAN COMPUTED ON EVERY LOAD
 * It is random, by requirement, so recomputing it would reseat the whole room on
 * every page load and the cards printed at nine would not match the screen at
 * ten. Drawing again writes a new plan and leaves the old one readable.
 *
 * THE ROOM IS YOURS, NOT THE ALGORITHM's
 * Two fields, because a venue quotes two numbers: how many tables the room has,
 * and how many people can sit at one. The server honours the count exactly and
 * treats the seats as a maximum.
 *
 * They are LINKED THROUGH THE HEAD COUNT, so editing either recomputes the
 * other: forty-five people over six tables is eight per table, and there is no
 * third possibility to choose between. Whichever number you typed is the one
 * that stands.
 *
 * The count used to be derived from a single people-per-table field, which is
 * why they are two clearly named fields rather than one: asking for six meant
 * six PEOPLE and gave 28 tables on a 170 person event. Nothing here suggests a
 * different room; a pair of numbers the room cannot hold is refused by the
 * server, naming both numbers that would work.
 *
 * WHAT "BEST MATCHUP" MEANS, precisely
 * The optimiser minimises repeat pairings, and the server also returns the floor
 * for this table plan, the fewest repeats the room ALLOWS. With the count fixed
 * that floor is often above zero: six tables of twenty-eight across three rounds
 * force hundreds of repeats, because a table of twenty-eight refilled from six
 * earlier tables must contain people who already met. So the banner reads off
 * `optimal`, never off repeats being zero, and where repeats are forced it says
 * so rather than pretending the draw is poor.
 */
export default function NetworkingTab({
  ev, label, plan, suggestedTables, attendees, mayDraw, onDrawn,
}) {
  // Seeded from the saved draw, else from the server suggestion, else 6. The
  // suggestion is only ever a starting value for this field; the number in the
  // field is what gets used.
  const [tables, setTables] = useState(plan?.tables || suggestedTables || 6);
  const [rounds, setRounds] = useState(plan?.rounds || 3);
  // How many people sit at one table. The draw already had a size, so the field
  // opens on it rather than on a guess.
  const seated = plan?.attendees || attendees || 0;
  const [seats, setSeats] = useState(
    plan?.largest_table
    || (seated ? Math.ceil(seated / (suggestedTables || 6)) : 6),
  );
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [look, setLook] = useState('cards');
  const printRef = useRef(null);

  // THE TWO FIELDS ARE ONE FACT SEEN TWICE, and the head count is what ties
  // them: forty-five people over six tables IS eight per table. So editing
  // either recomputes the other rather than letting the pair drift into a room
  // that does not exist and having the server refuse the draw. Whichever number
  // was typed is the one that survives; the other follows.
  const other = (n) => Math.max(1, Math.ceil(seated / Math.max(1, n)));
  const pickTables = (n) => { setTables(n); if (seated) setSeats(other(n)); };
  const pickSeats = (n) => { setSeats(n); if (seated) setTables(other(n)); };

  async function drawNow() {
    setBusy(true);
    setErr('');
    try {
      await pedApi.draw(ev.event_code, ev.edition, tables, rounds, seats);
      onDrawn();
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Could not draw a plan.');
    } finally {
      setBusy(false);
    }
  }

  const cardCols = [
    ['name', 'Name'],
    ['company', 'Company'],
    ...Array.from({ length: plan?.rounds || 0 }, (_, i) => [`r${i + 1}`, `Round ${i + 1}`]),
  ];
  const cardRows = (plan?.assignment || []).map((a) => ({
    ...a,
    ...Object.fromEntries((a.tables || []).map((t, i) => [`r${i + 1}`, t])),
  }));

  const controls = (
    <div className="ped-draw-bar">
      <div className="fd inline">
        <label className="fd-l">Tables in the room</label>
        <NumField value={tables} min={1} max={200}
          onChange={(e) => pickTables(Number(e.target.value) || 1)} />
      </div>
      <div className="fd inline">
        <label className="fd-l">People per table</label>
        <NumField value={seats} min={1} max={200}
          onChange={(e) => pickSeats(Number(e.target.value) || 1)} />
      </div>
      <div className="fd inline">
        <label className="fd-l">Rounds</label>
        <NumField value={rounds} min={1} max={6}
          onChange={(e) => setRounds(Number(e.target.value) || 3)} />
      </div>
      {mayDraw ? (
        <button className="btn btn-p" onClick={drawNow} disabled={busy}>
          <Icon name="refresh" size={15} />
          {busy ? 'Drawing…' : plan ? 'Draw again' : 'Draw plan'}
        </button>
      ) : null}
      {plan ? (
        <>
          <button className="btn btn-s" onClick={() => toExcel(
            look === 'cards' ? cardRows : roomRows(plan),
            look === 'cards' ? cardCols : ROOM_COLS,
            fileName(label, look === 'cards' ? 'table cards' : 'room layout'),
            'Networking',
          )}>
            <Icon name="download" size={15} />Excel
          </button>
          <button className="btn btn-s" onClick={() => printElement(
            printRef.current, fileName(label, 'networking'),
          )}>
            <Icon name="sheet" size={15} />PDF
          </button>
        </>
      ) : null}
    </div>
  );

  if (!ev) return null;

  if (!plan) {
    return (
      <div className="ped-net">
        {controls}
        {err ? <p className="ped-err">{err}</p> : null}
        <EmptyState
          icon="users"
          title="No draw yet"
          body="Set how many tables the room has, or how many people sit at each, then draw. Everybody is seated across the rounds to meet as many new people as the room allows, and the draw stays fixed once made, so printed table cards keep matching the screen."
        />
      </div>
    );
  }

  return (
    <div className="ped-net">
      {controls}
      {err ? <p className="ped-err">{err}</p> : null}

      {/* Quality of the draw, in one sentence rather than a row of stat tiles.
          `optimal` and not repeat_pairs === 0; see the note at the top. */}
      <div className={'ped-quality' + (plan.optimal ? ' ok' : ' warn')}>
        <Icon name={plan.optimal ? 'target' : 'warn'} size={15} />
        <div>
          {plan.repeat_pairs === 0 ? (
            <>
              <b>Nobody meets the same person twice.</b> {plan.attendees} people
              across {plan.tables} tables of up to {plan.largest_table},{' '}
              {plan.rounds} rounds.
            </>
          ) : plan.optimal ? (
            <>
              <b>Best matchup {plan.tables} tables allow.</b> {plan.repeat_pairs} repeat
              pairings, which is the fewest possible with {plan.tables} tables of up
              to {plan.largest_table} over {plan.rounds} rounds. Drawing again cannot
              beat it. Fewer rounds or more tables would.
            </>
          ) : (
            <>
              <b>{plan.repeat_pairs} repeat pairings</b>, against a floor of{' '}
              {plan.floor_repeats} for {plan.tables} tables of up to{' '}
              {plan.largest_table}. Drawing again may improve it.
            </>
          )}
        </div>
      </div>

      <Seg
        value={look}
        onChange={setLook}
        options={[
          { value: 'cards', label: 'Table cards' },
          { value: 'room', label: 'Room layout' },
        ]}
      />

      {look === 'cards' ? (
        <table className="ped-tbl wide">
          <thead>
            <tr>
              <th>Name</th><th>Company</th>
              {Array.from({ length: plan.rounds }, (_, i) => <th key={i}>Round {i + 1}</th>)}
            </tr>
          </thead>
          <tbody>
            {cardRows.map((a) => (
              <tr key={a.delegate_id}>
                <td>{a.name}</td>
                <td>{a.company}</td>
                {(a.tables || []).map((t, i) => (
                  <td key={i} className="ped-round">{t}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="ped-rooms">
          {(plan.rosters || []).map((round, i) => (
            <section key={i}>
              <h4>Round {i + 1}</h4>
              <div className="ped-room-grid">
                {Object.entries(round).map(([table, people]) => (
                  <div className="ped-table" key={table}>
                    <header>Table {table}<span>{people.length}</span></header>
                    <ul>
                      {people.map((p) => (
                        <li key={p.delegate_id}>
                          <b>{p.name}</b>
                          <span>{p.company}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}

      {/* Print target, carrying both views, because whoever prints wants the
          cards and the room layout on the same trip to the printer. */}
      <div className="ped-print" aria-hidden="true" ref={printRef}>
        <div className="ped-sheet-h">
          <h3>{label}</h3>
          <p>speed networking · {plan.attendees} people · {plan.tables} tables · {plan.rounds} rounds</p>
        </div>
        <table className="ped-tbl">
          <thead>
            <tr>
              <th>Name</th><th>Company</th>
              {Array.from({ length: plan.rounds }, (_, i) => <th key={i}>R{i + 1}</th>)}
            </tr>
          </thead>
          <tbody>
            {cardRows.map((a) => (
              <tr key={a.delegate_id}>
                <td>{a.name}</td><td>{a.company}</td>
                {(a.tables || []).map((t, i) => <td key={i}>{t}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
        {(plan.rosters || []).map((round, i) => (
          <div key={i} className="ped-print-room">
            <h4>Round {i + 1}</h4>
            {Object.entries(round).map(([table, people]) => (
              <p key={table}>
                <b>Table {table}:</b>{' '}
                {people.map((p) => `${p.name} (${p.company})`).join(', ')}
              </p>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

const ROOM_COLS = [
  ['round', 'Round'], ['table', 'Table'], ['name', 'Name'], ['company', 'Company'],
];

/** The room layout flattened for a spreadsheet, one row per seat. */
function roomRows(plan) {
  const out = [];
  (plan.rosters || []).forEach((round, i) => {
    Object.entries(round).forEach(([table, people]) => {
      people.forEach((p) => out.push({
        round: i + 1, table: Number(table), name: p.name, company: p.company,
      }));
    });
  });
  return out;
}
