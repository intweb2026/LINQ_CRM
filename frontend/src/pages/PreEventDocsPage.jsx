import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Tabs, EmptyState } from '../components/UI';
import { useFetch } from '../hooks/useFetch';
import { useSession } from '../context/SessionContext';
import { apiErrorMessage } from '../api/client';
import { useToast } from '../context/ToastContext';
import { useConfirm } from '../context/ConfirmContext';
import { Icon } from '../lib/icons';
import { bookingCodeTone } from '../lib/constants';
import { toExcel, printElement, fileName, sheetTitle } from '../lib/exportSheet';
import * as pedApi from '../api/preEventDocs';
import BadgeRunModal from './preEventDocs/BadgeRunModal';
import NetworkingTab from './preEventDocs/NetworkingTab';
import EventPicker from './preEventDocs/EventPicker';
import RunHistory from './preEventDocs/RunHistory';

/**
 * Pre-Event Docs. FOUR REPORTS, named exactly as the spec names them.
 *
 *   Name Badges             every booking, full name and company, by company
 *   Check-In Sheet          six columns, a two-row header, by company
 *   Additional Name Badges  what changed since the badges were logged
 *   Speed Networking        see preEventDocs/NetworkingTab.jsx
 *
 * WHY PLAIN TABLES AND NOT DataTable. These are reports: read, printed and
 * exported, not sorted and filtered. DataTable also virtualises its rows, so
 * only the visible ones exist in the page and printing produced one screenful
 * with the rest silently missing, which meant carrying a second complete copy
 * of every list purely for paper. One compact table per report now serves the
 * screen, the printer and the exporter, and the sort order is the spec's rather
 * than the reader's.
 *
 * WHAT REPLACED THE Control TAB. One cell in the workbook held the active event
 * code, so two people could not work two events at once. That cell is
 * preEventDocs/EventPicker.jsx, held in component state.
 *
 * THE PICKER READS DELEGATES, NOT THE EVENTS CATALOGUE, and the reason is worth
 * knowing before touching it: BookDelegate.save strips the trailing year out of
 * event_code into `edition`, so a delegate carries ("ACU", 2025) where the
 * catalogue carries "ACU25". Filtering delegates by a catalogue code returns
 * nothing at all, silently. See backend/pre_event_docs/services.py.
 */

const TABS = (d) => [
  { id: '', label: 'Name Badges', count: d?.name_badges?.length },
  { id: 'check-in', label: 'Check-In Sheet', count: d?.check_in?.length },
  { id: 'additional', label: 'Additional Name Badges', count: d?.additional?.length },
  { id: 'networking', label: 'Speed Networking' },
];

// How each remark reads at a glance. A new badge is routine work; a correction
// means a badge already on the table is wrong. Two different jobs, so two
// tones. There is no third: a badge to take OFF the table is a cancellation,
// and cancellations are their own list under Name Badges.
const REMARK_TONE = {
  'New Badge': 'neutral',
  'Name Change': 'amber',
  'Company Change': 'amber',
  'Name & Company Change': 'amber',
};

export default function PreEventDocsPage() {
  const { tab = '' } = useParams();
  const nav = useNavigate();
  const { can } = useSession();
  const toast = useToast();
  const confirm = useConfirm();

  const [ev, setEv] = useState(null);
  const [runMode, setRunMode] = useState(null);
  // A download, not a mode: the printable sheet it used to open is gone, so
  // there is no view to lay out. Its own flag so it cannot disable logRun.
  const [qrBusy, setQrBusy] = useState(false);
  const [busy, setBusy] = useState(false);
  const printRef = useRef(null);

  const mayRun = can('create', 'pre_event_docs');
  const mayUndo = can('delete', 'pre_event_docs');

  const events = useFetch(pedApi.events, []);

  // Opens on the event this user last worked on, and on the one with the most
  // delegates only when there is no such event — a first visit, or a stored
  // event that has since dropped off the list. Once only, and only until the
  // user picks for themselves. `ev` stays the single source of truth; storage
  // holds an identifier for it, never a copy. See api/preEventDocs.js.
  useEffect(() => {
    if (ev || !events.data?.length) return;
    setEv(pedApi.recallEvent(events.data)
      || events.data.reduce((a, b) => (b.delegates > a.delegates ? b : a)));
  }, [events.data, ev]);

  // Every pick is a statement about what this user is working on, so it is the
  // one place that writes. The tabs all read the same `ev`, so the choice
  // follows across them without any of them knowing about storage.
  function pickEvent(next) {
    setEv(next);
    pedApi.rememberEvent(next);
  }

  const docs = useFetch(
    () => (ev ? pedApi.docs(ev.event_code, ev.edition) : Promise.resolve(null)),
    [ev?.event_code, ev?.edition],
  );
  const d = docs.data;
  const label = pedApi.eventLabel(ev);

  // ── what each report is, in one place ──────────────────────────────────────

  const report = useMemo(() => {
    if (!d) return null;
    if (tab === 'check-in') {
      // UPCOMING EVENTS IS ALWAYS THREE COLUMNS, named or not. On the delivered
      // sheet they are tick boxes under one merged header, and the desk writes
      // in which one a delegate is interested in. The catalogue does not always
      // name them, WSE - MP names none, and a file that then carried five
      // columns instead of eight would leave the desk nowhere to write.
      const upcoming = [0, 1, 2].map((i) => (d.upcoming_events || [])[i] || '');
      return {
        title: 'Check-In Sheet',
        rows: d.check_in,
        // Six columns, exactly the workbook's, and Upcoming Events spans three.
        cols: [['name', 'Full Name'], ['company', 'Company Name'],
          ['_in', 'IN?'], ['booking_code_label', 'Booking Code'],
          ['payment_status_label', 'Payment Status'],
          ...upcoming.map((e, i) => [`_up${i}`, e, 'Upcoming Events'])],
      };
    }
    if (tab === 'additional') {
      return {
        title: 'Additional Name Badges',
        rows: d.additional,
        // The band the delivered file carries over the header. It says what the
        // list is FOR, which the tab name does not; these are badges to run off
        // and add to the table, not the table itself.
        section: pedApi.TO_PRINT,
        cols: [['name', 'Full Name'], ['company', 'Company Name'],
          ['remark', 'Remarks']],
      };
    }
    return {
      title: 'Name Badges',
      rows: d.name_badges,
      cols: [['name', 'Full Name'], ['company', 'Company Name']],
    };
  }, [d, tab]);

  // WHAT THE REPORT IS, in one line: its name, how many rows, and the one note
  // that changes what somebody does next. Rendered twice on purpose, in the page
  // header for the screen and inside the card for the printed sheet, which
  // carries no page header of its own.
  const meta = tab === 'networking' ? networkingMeta(d?.networking) : !report ? null : (
    <>
      {report.title}
      {' · '}
      {report.rows.length}
      {tab === 'additional' && !d.runs?.length ? ' · nothing frozen yet' : null}
      {tab === 'additional' && d.runs?.length && d.change_deadline
        ? ` · action by ${new Date(d.change_deadline).toLocaleDateString()}, ${d.change_window_days} days before the event`
        : null}
    </>
  );

  function exportList(kind) {
    if (!report?.rows?.length) return;
    const name = fileName(label, report.title);
    // The printed path carries the event and the report in its own header, so
    // only the workbook needs a title band.
    if (kind === 'excel') {
      toExcel(report.rows, report.cols, name, report.title, {
        title: sheetTitle(label, report.title),
        section: report.section,
      });
      // Six columns, three of them written into by hand. The two badge lists
      // beside it are two and three narrow columns and stay portrait.
    } else printElement(printRef.current, name, { landscape: tab === 'check-in' });
  }

  // The count comes from the response header, not d.check_in: somebody may have
  // been paid or cancelled since the page loaded, and the number reported has to
  // be the number of PDFs actually in the file.
  async function exportQrCodes() {
    setQrBusy(true);
    try {
      const res = await pedApi.downloadQrCodes(ev.event_code, ev.edition);
      const n = Number(res.headers['x-badge-count']) || 0;
      toast(`${n} ${n === 1 ? 'badge' : 'badges'} exported to ${res.name}`, 'ok');
    } catch (e) {
      toast(apiErrorMessage(e, 'Could not build the QR codes.'), 'err');
    } finally {
      setQrBusy(false);
    }
  }

  async function logRun(rows) {
    setBusy(true);
    try {
      const res = await pedApi.badgeRun(ev.event_code, ev.edition, rows);
      toast(`${res.badges} ${res.badges === 1 ? 'badge' : 'badges'} logged`, 'ok');
      setRunMode(null);
      docs.refetch();
    } catch (e) {
      toast('Could not log the run. The file downloaded, nothing was recorded.', 'err');
    } finally {
      setBusy(false);
    }
  }

  async function undoRun(run) {
    const ok = await confirm({
      title: 'Undo this badge run?',
      body: `${run.badges} ${run.badges === 1 ? 'badge' : 'badges'} will stop counting as issued, so everybody on it appears under Additional Name Badges as a new badge again. The badges themselves are not affected.`,
      ok: 'Undo run',
      danger: true,
    });
    if (!ok) return;
    try {
      await pedApi.undoBadgeRun(run.run_id);
      toast('Badge run undone', 'ok');
      docs.refetch();
    } catch (e) {
      toast('Could not undo the run', 'err');
    }
  }

  if (events.loading && !events.data) return null;
  if (!events.data?.length) {
    return (
      <EmptyState
        icon="calendar"
        title="No events with delegates yet"
        body="Pre-Event Docs reads the delegates on a booking. Once an event has bookings against it, it appears here."
      />
    );
  }

  const printable = tab !== 'networking' && !!report?.rows?.length;

  return (
    <>
      {/* The event is named once, here, with the report under it and the
          controls on its baseline. The tab strip carried these in its action
          slot before, which put the page's heaviest control beside the tabs and
          left the event name to the card below. */}
      <div className="ped-head">
        <div>
          <h2>{label}</h2>
          <p>{meta}</p>
        </div>
        <div className="ph-act">
          <EventPicker events={events.data} value={ev} onPick={pickEvent} />
          {printable ? (
            <>
              <button className="btn btn-s" onClick={() => exportList('excel')}>
                <Icon name="download" size={15} />Excel
              </button>
              <button className="btn btn-s" onClick={() => exportList('print')}>
                <Icon name="sheet" size={15} />PDF
              </button>
            </>
          ) : null}
          {tab === 'check-in' && d?.check_in?.length ? (
            <button className="btn btn-p" onClick={exportQrCodes} disabled={qrBusy}
              title="One PDF per confirmed person, in a single ZIP. Regenerating produces the same codes.">
              <Icon name="qr" size={15} />
              {qrBusy ? 'Building ZIP…' : 'Generate QR Codes'}
            </button>
          ) : null}
          {tab === '' && mayRun && d?.name_badges?.length ? (
            <button className="btn btn-p" onClick={() => setRunMode('full')} title="Exports the list and freezes it as sent, so later changes show as corrections">
              <Icon name="send" size={15} />Freeze &amp; print
            </button>
          ) : null}
          {/* `Remove` was a remark once, and this used to exclude it. It is a
              cancellation now, so every row on this tab is a badge to run. */}
          {tab === 'additional' && mayRun && d?.additional?.length ? (
              <button className="btn btn-p" onClick={() => setRunMode('additional')} title="Exports the corrections and freezes them as sent">
                <Icon name="send" size={15} />Freeze &amp; print
              </button>
            ) : null}
        </div>
      </div>

      <Tabs
        list={TABS(d)}
        active={tab}
        onPick={(id) => nav('/pre-event-docs' + (id ? '/' + id : ''))}
      />

      {tab === 'networking' ? (
        <NetworkingTab
          ev={ev}
          label={label}
          plan={d?.networking}
          suggestedTables={d?.suggested_tables}
          attendees={d?.networking_attendees}
          mayDraw={mayRun}
          onDrawn={() => docs.refetch()}
        />
      ) : report ? (
        <div className="ped-report" ref={printRef}>
          <div className="ped-sheet-h">
            <h3>{label}</h3>
            <p>{meta}</p>
          </div>

          {tab === 'check-in'
            ? <CheckInTable rows={report.rows} upcoming={d.upcoming_events || []} />
            : <ReportTable rows={report.rows} cols={report.cols} tab={tab}
                frozen={!!d.runs?.length} />}

          {/* CANCELLATIONS SIT WITH THE NAME BADGES, not with the additional
              ones. This tab is the badge TABLE: the full list that was printed,
              and which of those cards has to come off it. A badge to remove is
              not an additional badge, which is why it is not on that tab. */}
          {tab === '' && d.cancellations?.length ? (
            <Cancellations rows={d.cancellations} />
          ) : null}

          {tab === 'additional' ? (
            <RunHistory runs={d.runs} label={label} mayUndo={mayUndo}
              onUndo={undoRun} />
          ) : null}
        </div>
      ) : null}

      {runMode ? (
        <BadgeRunModal
          mode={runMode}
          rows={runMode === 'additional' ? d.additional : d.name_badges}
          eventLabel={label}
          busy={busy}
          onClose={() => setRunMode(null)}
          onConfirm={logRun}
        />
      ) : null}
    </>
  );
}

/**
 * Speed Networking has no row count to report, so its subtitle reports the draw:
 * the head count, the room, and how many rounds. Before a draw exists there is
 * nothing to say but the name of the tab.
 */
function networkingMeta(plan) {
  if (!plan) return 'Speed Networking';
  return `Speed Networking · ${plan.attendees} people · ${plan.tables} tables · ${plan.rounds} rounds`;
}

/**
 * A name, or the note that there is not one yet. One cell for every list on the
 * page, so the placeholder reads the same on all of them.
 *
 * NO INITIALS DISC. One was tried here and taken out: on a report somebody reads
 * a page of names down, a coloured circle per row is decoration competing with
 * the text beside it, which is the same conclusion components/Badge.jsx reached
 * for the listing tables.
 */
function NameCell({ name }) {
  if (!name) return <span className="dim">— write at the desk —</span>;
  return name;
}

/**
 * Name Badges and Additional Name Badges. Two or three columns, by company.
 *
 * `frozen` separates the two things an empty Additional report can mean, which
 * are not the same message and were previously both silent:
 *
 *   nothing frozen yet  -> there is no "since" to report changes against, so go
 *                          and freeze the Name Badges first
 *   frozen, no changes  -> the badges on the table all still match the bookings,
 *                          which is the good outcome
 */
function ReportTable({ rows, cols, tab, frozen }) {
  if (!rows.length) {
    if (tab !== 'additional') {
      return <p className="ped-none">No bookings on this event yet.</p>;
    }
    return (
      <p className="ped-none">
        {frozen
          ? 'No changes since the last freeze. Every badge on the table still matches its booking.'
          : 'Nothing frozen for this event yet, so there are no changes to report. Freeze the Name Badges first; anything that changes in Bookings after that appears here.'}
      </p>
    );
  }
  // Two columns means Name Badges, and two columns split evenly. Three means
  // Additional Name Badges or Cancellations, where the third column is a tag and
  // an even split would starve it, so those keep the browser's own sizing. The
  // class says which of the two this table is rather than the stylesheet trying
  // to count columns.
  return (
    <table className={'ped-tbl' + (cols.length === 2 ? ' ped-split' : '')}>
      <thead>
        <tr>{cols.map(([k, l]) => <th key={k}>{l}</th>)}</tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={r.delegate_id ?? r.badge_id ?? i}>
            {cols.map(([k]) => (
              <td key={k}>
                {k === 'remark'
                  ? <span className={'tg bg-' + (REMARK_TONE[r[k]] || 'neutral')}>{r[k]}</span>
                  : k === 'name'
                    ? <NameCell name={r[k]} />
                    : r[k]}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * CANCELLATIONS. Badges already on the table for people who are no longer on it.
 *
 * The workbook's own second half of Report_Additional, headed "Cancellations",
 * and it lives on the Name Badges tab rather than the Additional one because it
 * is about the badge table: somebody walks along it pulling cards. Sorted by
 * company for exactly that reason, since that is how the table is laid out.
 *
 * A row appears here only when the booking has left the Name Badge list
 * entirely, so it is genuinely gone rather than merely unpaid. Comparing
 * against the check-in sheet instead reported thirty of these on an event that
 * had none; see services.badge_changes.
 */
function Cancellations({ rows }) {
  return (
    <section className="ped-cancel">
      <header className="ped-sec-h">
        <div>
          <h3>Cancellations</h3>
          <p>
            Badges already printed for bookings that have since left the list.
            Pull these off the badge table.
          </p>
        </div>
        <span className="tg bg-red">{rows.length}</span>
      </header>
      <table className="ped-tbl">
        <thead>
          <tr><th>Full Name</th><th>Company Name</th><th>Reason</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.badge_id}>
              <td>{r.name}</td>
              <td>{r.company}</td>
              <td><span className="tg bg-red">{r.reason}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

/**
 * The Check-In Sheet, with the workbook's TWO-ROW HEADER.
 *
 * Upcoming Events spans three columns and the three event codes sit under it,
 * read from the Events module. The cells below them are deliberately EMPTY:
 * they are for the desk to mark which upcoming event a delegate is interested
 * in, which is what that block is for on the paper sheet.
 *
 * IN? SHOWS THE TICK AND DOES NOT WRITE IT. The box is checked from the row's
 * own `checked_in`, which services.py sets from delegate.attendance, so the desk
 * can see who has already arrived. It is readOnly and takes no clicks: ticking
 * somebody in belongs to PATCH /api/delegates/{id}/update_attendance/, which
 * validates against the Attendance choices. A writable control here would be a
 * second way to record one fact; see the note in api/preEventDocs.js.
 *
 * On paper the box is EMPTY whatever the state, because a printed sheet is
 * ticked by hand at the door; the print rules hide the input and draw the cell
 * as a box to write in.
 */
function CheckInTable({ rows, upcoming }) {
  if (!rows.length) {
    return <p className="ped-none">Nobody on this event reaches the desk sheet.</p>;
  }
  const span = Math.max(upcoming.length, 1);
  return (
    <table className="ped-tbl ped-checkin">
      <thead>
        <tr>
          <th rowSpan={2}>Full Name</th>
          <th rowSpan={2}>Company Name</th>
          <th rowSpan={2} className="w-in">IN?</th>
          <th rowSpan={2}>Booking Code</th>
          <th rowSpan={2}>Payment Status</th>
          <th colSpan={span} className="ped-grp">Upcoming Events</th>
        </tr>
        <tr>
          {upcoming.length
            ? upcoming.map((e) => <th key={e} className="ped-sub">{e}</th>)
            : <th className="ped-sub dim">none set in the Events module</th>}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.delegate_id}>
            <td><NameCell name={r.name} /></td>
            <td>{r.company}</td>
            <td className="w-in">
              <input type="checkbox" className="ped-in" checked={!!r.checked_in}
                readOnly tabIndex={-1}
                aria-label={r.checked_in ? 'Checked in' : 'Not checked in'} />
            </td>
            {/* THE CELL IS THE BLOCK. The tone class goes on the <td>, not on a
                <span> inside it, so the fill runs the full width of the column
                and the rows read as bands rather than as a column of small
                badges floating in white. .bg-* sets a tint and the matching text
                colour and nothing else, so it does this without a rule of its
                own; ped-fill only adds the weight. An empty cell stays empty and
                takes no class, because a tint with no word in it is noise. */}
            <td className={r.booking_code_label
              ? 'ped-fill bg-' + bookingCodeTone(r.booking_code_label) : undefined}>
              {r.booking_code_label}
            </td>
            <td className={r.payment_status_label ? 'ped-fill bg-red' : undefined}>
              {/* Red, and the only red on the sheet. It reads "Payment to
                  collect on-site" and nothing else, see services.py; the desk
                  has to take money off this person before they go in. */}
              {r.payment_status_label}
            </td>
            {Array.from({ length: span }, (_, i) => <td key={i} className="ped-up" />)}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
