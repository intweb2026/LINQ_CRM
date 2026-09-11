import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { EmptyState } from '../components/UI';
import DataTable from '../components/DataTable';
import Modal from '../components/Modal';
import { Icon } from '../lib/icons';
import { fdate, rel } from '../lib/helpers';
import * as ccApi from '../api/creditControl';
import { apiErrorMessage } from '../api/client';
import { useFetch } from '../hooks/useFetch';
import { useSession } from '../context/SessionContext';
import { useToast } from '../context/ToastContext';
import NoAccessPage from './NoAccessPage';

/**
 * CREDIT CONTROL
 * ──────────────
 * The calling queue for unpaid invoices. One row per invoice, because one
 * invoice is one phone call however many delegates sit under it; a row with
 * more than one opens to show them.
 *
 * FIVE ROUTES, ONE TABLE. The four buckets are a stored column on the lead, so
 * each surface is one filtered read. The Dashboard is a separate aggregate for
 * the same reason the Mining Matrix's is: its figures are over the whole set,
 * and a paginated total is not a total.
 *
 * WHAT A CALLER CAN CHANGE is three columns: disposition, remark and callback
 * date. Ownership, bucket and the classifier's verdict are read-only here AND
 * on the server, which accepts only those three fields, so this is an
 * affordance backed by a real rule rather than a hidden field away from being
 * editable. A lead assigned to somebody else renders as text, because the
 * server would refuse the write and a control that lies is worse than none.
 *
 * EVERY EDIT SAVES ON ITS OWN, on blur or on change. That is what makes the
 * predecessor's worst bug impossible rather than unlikely: a remark is durable
 * the moment it is typed, so the nightly routing pass reads a database that
 * already has it and cannot overwrite it.
 *
 * ASSIGNMENT IS NOT A CONTROL ANYWHERE ON THIS PAGE. The engine owns it: new
 * leads go to the team lead, and on day 4 anything not in flight moves to an
 * exec, carrying the remarks across. A drag-to-reassign would be a second,
 * silent answer to a question the rules already answer.
 */

/**
 * The class that fills a disposition dropdown, keyed on its status group.
 *
 * The COLOURS are not here. They are theme tokens selected by these classes in
 * components.css [credit_control], so the fill follows the light and dark
 * palettes; a hex chosen here could not.
 */
const GROUP_FILL = {
  'Not Attempted': 'cc-disp cc-disp-none',
  Attempted: 'cc-disp cc-disp-att',
  Ongoing: 'cc-disp cc-disp-ong',
  'Technical Issues': 'cc-disp cc-disp-tech',
};

const GROUP_DOT = {
  'Not Attempted': 'cc-dot cc-dot-none',
  Attempted: 'cc-dot cc-dot-att',
  Ongoing: 'cc-dot cc-dot-ong',
  'Technical Issues': 'cc-dot cc-dot-tech',
};

const dim = (text = '—') => <span className="dim">{text}</span>;

/** Days pending, red once a lead is past the point of polite chasing. */
function Days({ value }) {
  if (value === null || value === undefined) return dim();
  const heavy = value >= 30;
  return (
    <span style={heavy ? { color: 'var(--red-tx)', fontWeight: 700 } : undefined}>
      {value}
    </span>
  );
}

/**
 * Speaker, Delegate or Add-Ons.
 *
 * Its own column because it changes the call. A speaker may never have asked
 * for the invoice at all, a delegate always booked something, and an add-on is
 * a small balance on a booking that is otherwise settled. Derived on the server
 * from the booking code, so this and the dashboard can never disagree.
 */
function LeadTypeTag({ value }) {
  if (!value) return dim();
  const tone = value === 'Speaker' ? 'cc-lt-spk'
    : value === 'Add-Ons' ? 'cc-lt-add' : 'cc-lt-del';
  return <span className={`cc-lt ${tone}`}>{value}</span>;
}

/**
 * A call length, as something a person reads.
 *
 * ZERO IS NOT NOTHING and must not render as a dash: it is a call that
 * connected to nobody, which is exactly what somebody about to dial again
 * needs to know. Null means we have never looked, which is the case that gets
 * the dash.
 */
function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return '';
  if (seconds === 0) return 'no answer';
  if (seconds < 60) return `${seconds}s`;
  const mins = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest ? `${mins}m ${rest}s` : `${mins}m`;
}

function InvoiceTypeTag({ lead }) {
  const value = lead.effective_invoice_type;
  if (!value) {
    /*
     * TWO DIFFERENT FACTS THAT MUST NOT RENDER THE SAME, and the test is the
     * BOOKING TYPE, not whether an error happens to be recorded.
     *
     * A delegate or an add-on has no invoice type by definition: they booked
     * something, so "did they ask for this invoice" is not a question that
     * applies. Those render NOTHING, and always will.
     *
     * A SPEAKER with no verdict is Unclassified, whether it has been attempted
     * or not. Keying this off `invoice_type_error` was wrong: a speaker that
     * has never been through the classifier carries no error, so it fell
     * through to nothing and was indistinguishable from a delegate on screen,
     * while the dashboard went on counting it as unclassified. The table and
     * the dashboard have to agree about the same row.
     */
    if (lead.lead_type !== 'Speaker') return null;
    return (
      <span
        className="tg bg-neutral"
        title={lead.invoice_type_error || 'Not classified yet.'}
      >
        Unclassified
      </span>
    );
  }
  const manual = lead.invoice_type_source === 'manual';
  const tone = value === 'Blind Invoice' ? 'bg-amber' : 'bg-green';
  return (
    <span className={`tg ${manual ? 'bg-teal' : tone}`} title={lead.invoice_type_basis || ''}>
      {value}{manual ? ' · manual' : ''}
    </span>
  );
}

/**
 * The queue, as a DataTable.
 *
 * IT USED TO HAND-ROLL ITS OWN <table>, which is why it was the one table in
 * the app with no column menu, no user-chosen freeze boundary, no sort and no
 * filters. All four already exist in DataTable and are already persisted per
 * table id, so the flexible freezing and hiding asked for here is a matter of
 * using the component rather than building a second one.
 *
 * GROUPS ARE WHAT MAKE FREEZING WORK. DataTable's freeze boundary is a group
 * key, so the columns are grouped by what they are FOR: who this is, what the
 * classifier decided, how to reach them, what the caller wrote, and the
 * bookkeeping. Freeze through any of those and the boundary lands somewhere
 * meaningful instead of mid-thought.
 *
 * THE `work` GROUP IS THE ONE THE CALLERS CONTROL and is tinted across its
 * whole width, header included, so a caller can see at a glance which three
 * columns are theirs.
 */

/**
 * An editable cell that saves itself.
 *
 * Each one holds its own draft and commits on change for the controls and on
 * blur for the text, so a keystroke is not a request and a page-level Save
 * button never has to exist. `key` on the wrapper is the invoice number, so a
 * refresh that replaces a row remounts its editors with the new values rather
 * than leaving a stale draft on screen.
 */
function EditCell({ lead, field, onSaved, children }) {
  const toast = useToast();
  const [value, setValue] = useState(lead[field] ?? '');
  const [busy, setBusy] = useState(false);

  useEffect(() => { setValue(lead[field] ?? ''); }, [lead, field]);

  const commit = useCallback(async (next) => {
    if ((lead[field] ?? '') === (next ?? '')) return;
    setBusy(true);
    try {
      const saved = await ccApi.save(lead.invoice_number, {
        [field]: field === 'callback_date' ? (next || null) : next,
      });
      onSaved(saved);
    } catch (err) {
      toast(apiErrorMessage(err, 'That edit did not save. Try again.'), 'er');
      setValue(lead[field] ?? '');
    } finally {
      setBusy(false);
    }
  }, [lead, field, onSaved, toast]);

  return children({ value, setValue, commit, busy });
}

function DispositionCell({ lead, dispositions, groupOf, onSaved }) {
  return (
    <EditCell lead={lead} field="disposition" onSaved={onSaved}>
      {({ value, setValue, commit }) => (
        <span className={GROUP_FILL[groupOf(value)] || GROUP_FILL['Not Attempted']}>
          <select
            value={value}
            aria-label={`Disposition for ${lead.company || lead.client_name}`}
            onChange={(e) => { setValue(e.target.value); commit(e.target.value); }}
          >
            <option value="">Not attempted</option>
            {dispositions.map((d) => (
              <option key={d.label} value={d.label}>{d.label}</option>
            ))}
          </select>
        </span>
      )}
    </EditCell>
  );
}

function RemarkCell({ lead, onSaved, onOpen }) {
  return (
    <EditCell lead={lead} field="remark" onSaved={onSaved}>
      {({ value, setValue, commit }) => (
        <div className="cc-rmk">
          <input
            className="in in-xs"
            value={value}
            aria-label="Remark"
            placeholder="What they said"
            onChange={(e) => setValue(e.target.value)}
            onBlur={(e) => commit(e.target.value)}
          />
          <button
            type="button"
            className="cc-rmk-x"
            title="Open the full remark"
            aria-label={`Open the full remark for ${lead.company || lead.client_name}`}
            onClick={() => onOpen(lead)}
          >
            <Icon name="edit" size={13} />
          </button>
        </div>
      )}
    </EditCell>
  );
}

function CallbackCell({ lead, onSaved }) {
  return (
    <EditCell lead={lead} field="callback_date" onSaved={onSaved}>
      {({ value, setValue, commit }) => (
        <input
          type="date"
          className="in in-xs"
          value={value || ''}
          aria-label="Callback date"
          onChange={(e) => { setValue(e.target.value); commit(e.target.value); }}
        />
      )}
    </EditCell>
  );
}

/** The delegates on one invoice, opened from the client-name cell. */
function DelegateList({ lead }) {
  return (
    <div className="cc-drill-in">
      <div className="cc-drill-h">
        {lead.delegate_count} delegates on {lead.invoice_number}
      </div>
      <table className="cc-sub">
        <thead>
          <tr>
            <th>#</th><th>Delegate</th><th>Job title</th><th>Email</th>
            <th>Phone</th><th>Paid / free</th><th>Tier</th><th>Add-ons</th>
          </tr>
        </thead>
        <tbody>
          {(lead.delegates || []).map((d, i) => (
            <tr key={d.email || i}>
              {/* POSITION, not the stored delegate_number. 18 live invoices
                  carry two delegates both numbered 1, because the column
                  defaults to 1 and some import path never sequenced them. The
                  "#" here answers "which of these am I looking at", so a
                  duplicated source value makes it useless; counting the rows
                  cannot be wrong. */}
              <td className="dim">{i + 1}</td>
              <td style={{ fontWeight: 650, color: 'var(--text)' }}>{d.name}</td>
              <td>{d.job_title || dim()}</td>
              <td className="mono">{d.email || dim()}</td>
              <td className="mono">{d.phone || dim('no number')}</td>
              <td>{d.paid_or_free || dim()}</td>
              <td className="dim">{d.ticket_tier || '—'}</td>
              <td className="dim">{d.add_ons || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const GROUPS = [
  { key: 'who', label: 'Who' },
  { key: 'ai', label: 'Invoice type' },
  { key: 'age', label: 'Age' },
  { key: 'reach', label: 'How to reach them' },
  { key: 'work', label: 'Your columns' },
  { key: 'meta', label: 'Record' },
];

function LeadTable({ bucket, rows, dispositions, groupOf, onSaved, onOpenRemark }) {
  const editable = bucket === ccApi.BUCKETS.ACTIVE;

  const cols = useMemo(() => {
    const base = [
      {
        /* CLIENT NAME LEADS THE ROW. A list that opens with the company makes
           every booking from one organisation look identical; the person is
           what tells them apart, and is who the caller asks for. */
        key: 'client_name', label: 'Client name', group: 'who', pin: true, w: 190,
        /* The delegate count IS the drill-down control, and it opens the row
           IN PLACE. A dialog was the wrong gesture: these are the lines that
           make up the row above them, so covering the queue to read them loses
           the context that gives them meaning. */
        cell: (v, r, api) => (
          <span className="cc-co">
            {(r.delegate_count || 0) > 1 ? (
              <button
                type="button"
                className="cc-dz"
                aria-expanded={!!(api && api.open)}
                title={`${r.delegate_count} delegates on this invoice`}
                aria-label={`Show the ${r.delegate_count} delegates on ${r.company}`}
                onClick={(e) => {
                  e.stopPropagation();
                  if (api && api.onOpen) api.onOpen();
                }}
              >{r.delegate_count}</button>
            ) : <span className="cc-dz-x" />}
            <span className="nm">{v || dim('Unnamed')}</span>
          </span>
        ),
      },
      { key: 'company', label: 'Company', group: 'who', w: 200,
        cell: (v) => v || dim() },
      { key: 'invoice_number', label: 'Invoice', group: 'who', w: 140,
        cell: (v) => <span className="mono">{v}</span> },
      { key: 'event_code', label: 'Event', group: 'who', w: 110,
        cell: (v) => <span className="mono">{v || '—'}</span> },
      { key: 'lead_type', label: 'Type', group: 'who', w: 96,
        cell: (v) => <LeadTypeTag value={v} /> },

      { key: 'effective_invoice_type', label: 'Invoice type', group: 'ai', w: 130,
        cell: (v, r) => <InvoiceTypeTag lead={r} /> },
      { key: 'invoice_type_basis', label: 'Why', group: 'ai', w: 260,
        cell: (v) => (v ? <span className="cc-reason" title={v}>{v}</span> : dim()) },

      { key: 'days_pending', label: 'Days', group: 'age', num: true, w: 76,
        cell: (v) => <Days value={v} /> },
      { key: 'invoice_date', label: 'Invoiced', group: 'age', type: 'date', w: 110,
        cell: (v) => (v ? fdate(v) : dim()) },

      {
        /* Hyperlinked into HubSpot, so the caller lands on the record rather
           than searching for it. The link only renders when the contact is
           actually cached, because a link to a record that is not there costs
           a page load to discover. */
        key: 'who_we_call', label: 'Who we call', group: 'reach', w: 230,
        cell: (v, r) => {
          if (!v) return dim('No contact');
          if (!r.who_we_call_url) return <span className="mono">{v}</span>;
          return (
            <a
              className="lnk mono" href={r.who_we_call_url}
              target="_blank" rel="noreferrer"
              title="Open in HubSpot"
              onClick={(e) => e.stopPropagation()}
            >{v}</a>
          );
        },
      },
      { key: 'phone_direct', label: 'Direct', group: 'reach', w: 140,
        cell: (v) => (v ? <span className="mono">{v}</span> : dim()) },
      { key: 'phone_mobile', label: 'Mobile', group: 'reach', w: 140,
        cell: (v) => (v ? <span className="mono">{v}</span> : dim()) },
      { key: 'phone_main', label: 'Main', group: 'reach', w: 140,
        cell: (v) => (v ? <span className="mono">{v}</span> : dim()) },
      { key: 'times_called', label: 'Calls', group: 'reach', num: true, w: 74,
        cell: (v) => (v === null || v === undefined ? dim() : v) },
      // Two call columns, and both are wanted. "Calls" is HubSpot's total
      // against the contact, so it counts everybody who ever rang them; "Mine"
      // is this caller's own logged attempts on this invoice, which is the one
      // that answers "have I already tried them tonight".
      { key: 'my_calls', label: 'Mine', group: 'reach', num: true, w: 62,
        cell: (v) => (v ? v : dim()) },
      { key: 'last_call_at', label: 'Last call', group: 'reach', type: 'date', w: 130,
        cell: (v, r) => (v ? (
          <span className="cc-lastcall">
            {fdate(v)}
            <span className="cc-dur">{formatDuration(r.last_call_seconds)}</span>
          </span>
        ) : dim('never')),
      },
    ];

    /* A row somebody else owns renders as TEXT, not as a disabled input.
       `can_edit` is computed by the same rule the server enforces, so the
       table never offers a control that would 403; a disabled input would
       still read as "this is yours, but broken". */
    const ownCell = (v, r, own, other) => (r.can_edit ? own(v, r) : other(v, r));

    const work = editable ? [
      { key: 'disposition', label: 'Disposition', group: 'work', cls: 'cc-own', w: 210,
        cell: (v, r) => ownCell(v, r,
          () => (
            <DispositionCell
              key={r.invoice_number} lead={r} dispositions={dispositions}
              groupOf={groupOf} onSaved={onSaved}
            />
          ),
          (value) => (value
            ? <span className={GROUP_DOT[groupOf(value)] || GROUP_DOT.Ongoing}>{value}</span>
            : <span className="cc-dot cc-dot-none">Not attempted</span>),
        ),
      },
      { key: 'remark', label: 'Remark', group: 'work', cls: 'cc-own', w: 300,
        cell: (v, r) => ownCell(v, r,
          () => <RemarkCell key={r.invoice_number} lead={r} onSaved={onSaved} onOpen={onOpenRemark} />,
          (value) => (value ? <span className="cc-reason" title={value}>{value}</span> : dim()),
        ),
      },
      { key: 'callback_date', label: 'Callback', group: 'work', cls: 'cc-own',
        type: 'date', w: 140,
        cell: (v, r) => ownCell(v, r,
          () => <CallbackCell key={r.invoice_number} lead={r} onSaved={onSaved} />,
          (value) => (value ? fdate(value) : dim()),
        ),
      },
    ] : [
      { key: 'disposition', label: 'Disposition', group: 'work', cls: 'cc-own', w: 190,
        cell: (v, r) => (v
          ? <span className={GROUP_DOT[groupOf(v)] || GROUP_DOT.Ongoing}>{v}</span>
          : <span className="cc-dot cc-dot-none">Not attempted</span>),
      },
      { key: 'remark', label: 'Remark', group: 'work', cls: 'cc-own', w: 300,
        cell: (v) => (v ? <span className="cc-reason" title={v}>{v}</span> : dim()) },
    ];

    const meta = [
      { key: 'assigned_to_name', label: 'Assigned to', group: 'meta', w: 130,
        cell: (v) => v || dim('unassigned') },
      {
        /* LAST UPDATED IS LAST, and it stamps only the caller's own columns.
           record_touch writes it on the same save that writes the value, so it
           cannot drift from what it describes, and the routing pass never
           touches it: a lead moved between callers has not been WORKED. */
        key: 'last_touched_at', label: 'Last updated', group: 'meta', type: 'date', w: 150,
        cell: (v) => (v ? (
          <span className="cc-lastcall">
            {fdate(v)}
            <span className="cc-dur">{new Date(v).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
          </span>
        ) : dim('never')),
      },
    ];

    return [...base, ...work, ...meta];
  }, [editable, dispositions, groupOf, onSaved, onOpenRemark]);

  if (!rows.length) {
    return (
      <EmptyState
        icon="check"
        title="Nothing here"
        body={
          editable
            ? 'No unpaid invoices on this list. Refresh to re-run the routing pass.'
            : 'Nothing has been routed here yet.'
        }
      />
    );
  }

  return (
    <DataTable
      rows={rows}
      cols={cols}
      groups={GROUPS}
      groupHeader
      noun="leads"
      tableId={`credit_control.v1.${bucket}`}
      detail={(row) => <DelegateList lead={row} />}
      searchPlaceholder="Search client, company, invoice…"
      defaultSort={{ key: 'days_pending', dir: 'desc' }}
    />
  );
}

/** Done and SpEx carry a reason column the others do not. */
function ReasonTable({ rows, title }) {
  if (!rows.length) return <EmptyState icon="check" title={title} body="Nothing here yet." />;
  return (
    <div className="tsc">
      <table className="dt">
        <thead>
          <tr>
            <th>Company</th><th>Invoice</th><th>Assigned to</th>
            <th>Live status</th><th>Reason</th><th>Last disposition</th><th>Remark</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((lead) => (
            <tr key={lead.invoice_number}>
              <td>
                <span className="who-t">
                  <span className="who-n">{lead.company || dim()}</span>
                  <span className="who-s">{lead.event_code}</span>
                </span>
              </td>
              <td className="mono">{lead.invoice_number}</td>
              <td>{lead.assigned_to_name || dim('unassigned')}</td>
              <td>{lead.payment_status || dim()}</td>
              <td className="dim">{lead.done_reason || '—'}</td>
              <td>{lead.disposition || dim('never attempted')}</td>
              <td className="dim" title={lead.remark}>{lead.remark || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * What the classifier decided about one invoice, and why.
 *
 * THE ONLY PLACE THE REASONING IS READABLE. Everywhere else the verdict is a
 * two-word tag, which is not enough to act on or to argue with. A caller about
 * to phone a blind invoice needs to know what made it blind before they use
 * that on the call, and anyone overruling it needs to see the evidence it read.
 *
 * Three states, and they are genuinely different facts:
 *   a delegate booking has no invoice type at all, so this renders nothing;
 *   a speaker with a verdict shows the verdict, the basis and the quote;
 *   a speaker without one shows why it was left for a human, which is the
 *   honest outcome rather than a failure.
 */
function CaseVerdict({ lead, onOverride }) {
  const speaker = lead.invoice_type || lead.invoice_type_override
    || lead.invoice_type_error;
  if (!speaker) return null;

  const value = lead.effective_invoice_type;
  const manual = lead.invoice_type_source === 'manual';
  const confidence = lead.invoice_type_confidence;

  return (
    <div className="cc-case">
      <div className="cc-case-h">
        Invoice type
        {lead.invoice_type_source ? (
          <span className="cc-case-src">
            {manual ? 'set by a person' : `decided by ${lead.invoice_type_source}`}
            {confidence != null && !manual
              ? ` · confidence ${Math.round(confidence * 100)}%`
              : ''}
          </span>
        ) : null}
      </div>

      {value ? (
        <div className={'cc-verdict ' + (value === 'Blind Invoice' ? 'is-blind' : 'is-req')}>
          {value}
        </div>
      ) : (
        <div className="cc-verdict is-unc">Unclassified</div>
      )}

      {lead.invoice_type_basis ? (
        <p className="cc-case-basis">{lead.invoice_type_basis}</p>
      ) : null}

      {lead.invoice_type_quote ? (
        <blockquote className="cc-case-quote">
          {lead.invoice_type_quote}
          {lead.invoice_type_evidence_date ? (
            <cite>from the contact, {fdate(lead.invoice_type_evidence_date)}</cite>
          ) : null}
        </blockquote>
      ) : value && !manual ? (
        /* A blind invoice is usually proved by the ABSENCE of any request, so
           there is nothing to quote. Saying so is better than an empty panel
           that looks like a missing feature. */
        <p className="cc-case-note">
          No quote, because the verdict rests on nobody ever having asked for
          this invoice.
        </p>
      ) : null}

      {lead.invoice_type_error ? (
        <p className="cc-case-note cc-case-warn">{lead.invoice_type_error}</p>
      ) : null}

      <div className="cc-case-ovr">
        <label className="fd-l" htmlFor="cc-override">Your verdict overrules it</label>
        <select
          id="cc-override"
          className="in in-xs"
          value={lead.invoice_type_override || ''}
          onChange={(e) => onOverride(e.target.value)}
        >
          <option value="">Leave it to the classifier</option>
          <option value="Blind Invoice">Blind Invoice</option>
          <option value="Requested">Requested</option>
        </select>
      </div>
    </div>
  );
}

/**
 * SPONSORS, BY COMPANY.
 *
 * A list of invoices is the wrong shape here. Sponsorship is sold to an
 * organisation, and one organisation routinely holds several bookings across
 * several events, so the question anybody opens this to ask is "which companies
 * are we carrying, and at what tier" rather than "which invoice numbers exist".
 *
 * The tier comes from the booking code as it is written, so PLT SpEx and SLV
 * SpEx read as themselves rather than collapsing into one "SpEx" label. Grouped
 * in the browser rather than on the server: the whole bucket is a few dozen rows
 * and it is already in hand, so a second endpoint would be a round trip to
 * re-derive what a reduce answers.
 */
function SponsorTable({ rows }) {
  const companies = useMemo(() => {
    const byCompany = new Map();
    rows.forEach((r) => {
      const name = (r.company || '').trim() || '(No company)';
      const entry = byCompany.get(name) || {
        company: name, bookings: 0, tiers: new Set(), events: new Set(),
      };
      entry.bookings += 1;
      if (r.booking_code) entry.tiers.add(r.booking_code.trim());
      if (r.event_code) entry.events.add(r.event_code.trim());
      byCompany.set(name, entry);
    });
    return [...byCompany.values()]
      .map((e) => ({
        ...e,
        tiers: [...e.tiers].sort(),
        events: [...e.events].sort(),
      }))
      .sort((a, b) => b.bookings - a.bookings || a.company.localeCompare(b.company));
  }, [rows]);

  if (!companies.length) {
    return <EmptyState icon="star" title="No sponsors" body="Nothing routed here yet." />;
  }

  return (
    <div className="tsc" style={{ borderRadius: 0 }}>
      <table className="ccd-t">
        <thead>
          <tr>
            <th>Company</th>
            <th>Tier</th>
            <th>Events</th>
            <th>Bookings</th>
          </tr>
        </thead>
        <tbody>
          {companies.map((c) => (
            <tr key={c.company}>
              <td>{c.company}</td>
              <td style={{ textAlign: 'left' }}>
                <span className="cc-tiers">
                  {c.tiers.length
                    ? c.tiers.map((t) => (
                      <span className="cc-tier" key={t}>{t}</span>
                    ))
                    : dim()}
                </span>
              </td>
              <td className="dim" style={{ textAlign: 'left' }}>
                {c.events.join(', ') || '—'}
              </td>
              <td>{c.bookings}</td>
            </tr>
          ))}
          <tr className="ccd-t-tot">
            <td>{companies.length} companies</td>
            <td /><td />
            <td>{companies.reduce((a, c) => a + c.bookings, 0)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

/**
 * WHEN THINGS NEXT RUN.
 *
 * Read off the server's own CRONJOBS rather than retyped, so it cannot drift
 * into a plausible lie. It answers the question somebody asks before trusting a
 * figure: is this about to move. A job with several entries reports the soonest,
 * and one whose last run is unknown shows only the next, rather than inventing
 * a history.
 */
function ScheduleBrief({ rows, canRun, onDone }) {
  const toast = useToast();
  const [running, setRunning] = useState('');
  if (!rows || !rows.length) return null;

  const run = async (key, label) => {
    setRunning(key);
    try {
      const result = await ccApi.runJob(key);
      // Just the first line of the command's output. They print several, and a
      // toast is not a log; the rest is in the server log if anybody needs it.
      const [summary] = (result.output || '').split(/\r?\n/);
      toast(`${label} finished. ${summary || ''}`.trim(), 'ok');
      // THE RECEIPT IS THE CHIP'S OWN TIMESTAMP. A toast is gone in under four
      // seconds and these jobs run inline for minutes, so the operator is
      // rarely looking when it fires. Refetching turns "never run" into "1m
      // ago" and leaves proof on the page that outlives the toast.
      onDone?.();
    } catch (err) {
      toast(apiErrorMessage(err, `${label} did not run.`), 'er');
    } finally {
      setRunning('');
    }
  };
  const soon = (iso) => {
    if (!iso) return { text: 'not scheduled', mins: null };
    const mins = Math.round((new Date(iso) - Date.now()) / 60000);
    if (mins <= 0) return { text: 'due now', mins };
    if (mins < 60) return { text: `in ${mins} min`, mins };
    const hours = Math.floor(mins / 60);
    return { text: `in ${hours}h ${mins % 60}m`, mins };
  };
  return (
    <div className="cc-sched">
      {rows.map((r) => {
        const next = soon(r.next_run);
        return (
          <span className="cc-sched-i" key={r.job} title={`${r.what} (cron ${r.cron})`}>
            <b>{r.job}</b>
            {/* BOTH FACTS. "Next in 40 minutes" is not reassuring on its own if
                the last run failed silently three days ago, and a last-run time
                alone does not say whether waiting is worth it. */}
            <em className="cc-sched-last">
              {r.last_run ? rel(r.last_run) : 'never run'}
            </em>
            <em className={
              running === r.key || (next.mins !== null && next.mins < 60)
                ? 'is-soon' : undefined
            }>
              {/* The classifier and the HubSpot job can run for minutes. An
                  ellipsis inside a 9px button is not a progress signal, so the
                  chip says so at chip size. */}
              {running === r.key ? 'running now' : next.text}
            </em>
            {/* Cron owns the cadence; this is for when somebody has just fixed
                a verdict or an invoice date and wants to see it land. Admin
                only, and the server refuses everybody else regardless. */}
            {canRun && r.key ? (
              <button
                type="button"
                className="cc-sched-run"
                disabled={!!running}
                title={`Run ${r.job} now`}
                onClick={() => run(r.key, r.job)}
              >
                {running === r.key ? '…' : 'Run'}
              </button>
            ) : null}
          </span>
        );
      })}
    </div>
  );
}

/**
 * WHO COLLECTED WHAT.
 *
 * Two numbers per caller, and the split carries the meaning: a lead resolved
 * after somebody worked it is that caller's win, while an invoice paid before
 * anybody reached the contact is not. Rolling them together would flatter the
 * whole team, so they are never summed into one figure here.
 */
function ResolvedAttribution({ data }) {
  if (!data) return null;
  const { totals, rows, days } = data;
  return (
    <div className="ccd-sec">
      <div className="ccd-h">
        <h3>Collected, last {days} days</h3>
        <p>
          Credited to whoever owned the lead when the payment landed. Worked
          means somebody had reached the contact; the rest were paid before
          anybody got to them, and are counted separately rather than claimed.
        </p>
        <span className="ccd-stamp">{totals.total} resolved</span>
      </div>
      {!rows.length ? (
        <p className="cc-src">
          Nothing has resolved yet. A lead moves here the moment its invoice
          carries a payment date or its status leaves Pending, which is recorded
          the instant the booking is saved rather than overnight.
        </p>
      ) : (
        <div className="tsc" style={{ borderRadius: 0 }}>
          <table className="ccd-t">
            <thead>
              <tr>
                <th>Caller</th><th>Worked</th><th>Paid unworked</th><th>Total</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.user}>
                  <td>{r.user}</td>
                  <td style={{ fontWeight: 700, color: 'var(--green-tx)' }}>{r.worked}</td>
                  <td className={r.unworked ? undefined : 'ccd-zero'}>{r.unworked}</td>
                  <td style={{ fontWeight: 800, color: 'var(--text)' }}>{r.total}</td>
                </tr>
              ))}
              <tr className="ccd-t-tot">
                <td>Team</td>
                <td>{totals.worked}</td>
                <td>{totals.unworked}</td>
                <td>{totals.total}</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/**
 * The dashboard.
 *
 * SHARP CORNERS AND REAL COLOUR, unlike the queue, and the difference is
 * deliberate. A caller reads the queue as a wall of values for a whole shift,
 * so state there is a dot and a recoloured word; a manager reads this for ten
 * seconds to decide where to push, so the shape of the numbers has to arrive
 * before the numbers do.
 *
 * Colour is spent on three things and nothing else: the six KPI rails, which
 * separate six measures that would otherwise read as one grid; the invoice-type
 * proportion bar, which is the headline finding of the whole module; and the
 * two heatmaps, where intensity is the fastest way to find the corner of a
 * matrix that needs attention. Every value comes from a theme token, so the
 * whole page inverts.
 */

function pctOf(n, total) {
  if (!total) return 0;
  return Math.round((n / total) * 100);
}

/* The heatmap tint. An OPACITY on a solid token, not an alpha hex: one number
   then works in both themes, where a hard-coded rgba() could only suit one. */
function heat(value, max) {
  if (!value) return 0;
  return 0.1 + (value / Math.max(max, 1)) * 0.62;
}

function HeatCell({ value, max, hue }) {
  if (!value) return <td><span className="ccd-zero">0</span></td>;
  return (
    <td>
      <span className="ccd-cell">
        <i style={{ background: hue, opacity: heat(value, max) }} />
        <b>{value}</b>
      </span>
    </td>
  );
}

const GROUP_HUE = {
  'Not Attempted': 'var(--n-300)',
  Attempted: 'var(--amber)',
  Ongoing: 'var(--green)',
  'Technical Issues': 'var(--red)',
};

const AGED_HUE = {
  Delegates: 'var(--blue)',
  'Speakers, Requested': 'var(--green)',
  'Speakers, Blind Invoice': 'var(--amber)',
  'Speakers, Unclassified': 'var(--slate)',
  Sponsors: 'var(--cyan)',
  'Add-Ons Only': 'var(--violet)',
};

function Dashboard({ data, canRun, onDone }) {
  if (!data) return null;
  const k = data.kpis || {};
  const { h36, h72 } = data.age_thresholds || { h36: 36, h72: 72 };
  const status = data.status || {};
  const aged = data.aged_debtor || { buckets: [], rows: {} };
  const bifurcation = data.bifurcation || {};
  // The bifurcation's rep columns, in the same order and from the same source
  // as the per-caller section's rows, so the two cannot list different people.
  const reps = Object.keys(data.status || {});
  const matrix = data.shift_matrix || { dates: [], rows: [], source: '' };

  const agedTotal = (row) => aged.buckets.reduce((s, b) => s + (row[b] || 0), 0);
  const bucketTotals = aged.buckets.map((b) => Object.values(aged.rows)
    .reduce((sum, row) => sum + (row[b] || 0), 0));
  const agedMax = Math.max(
    1,
    ...Object.values(aged.rows).flatMap((r) => aged.buckets.map((b) => r[b] || 0)),
  );
  const callMax = Math.max(1, ...matrix.rows.flatMap((r) => r.counts));

  // The three invoice-type figures are read off the aged rows, so the bar can
  // never disagree with the table underneath it.
  const blind = agedTotal(aged.rows['Speakers, Blind Invoice'] || {});
  const requested = agedTotal(aged.rows['Speakers, Requested'] || {});
  const unclassified = agedTotal(aged.rows['Speakers, Unclassified'] || {});
  const speakers = blind + requested + unclassified;
  const classified = blind + requested;
  const share = (n) => (speakers ? Math.round((n / speakers) * 100) : 0);

  const kpis = [
    {
      l: 'Total active',
      v: k.total_active,
      ac: 'var(--t-400)',
      lead: true,
      s: <>across <b>{Object.keys(status).length}</b> callers</>,
    },
    {
      l: `Past ${h36}h`,
      v: k.past_36h,
      ac: 'var(--amber)',
      s: <><b>{pctOf(k.past_36h, k.total_active)}%</b> of active</>,
    },
    {
      l: `Past ${h72}h`,
      v: k.past_72h,
      ac: 'var(--red)',
      s: <><b>{pctOf(k.past_72h, k.total_active)}%</b> of active</>,
    },
    {
      l: 'Resolved, 7 days',
      v: k.resolved_last_7_days,
      ac: 'var(--green)',
      s: 'source confirmed payment',
    },
    {
      l: 'Not invoiced yet',
      v: k.not_invoiced,
      ac: 'var(--violet)',
      s: 'outside the calling flow',
    },
    {
      l: 'Unclassified',
      v: k.unclassified_speakers,
      ac: 'var(--slate)',
      s: 'speakers awaiting a verdict',
    },
  ];

  const segments = [
    { n: blind, t: 'Blind Invoice', c: 'ccd-prop-blind', hue: 'var(--amber)' },
    { n: requested, t: 'Requested', c: 'ccd-prop-req', hue: 'var(--green)' },
    { n: unclassified, t: 'Unclassified', c: 'ccd-prop-unc', hue: 'var(--n-300)' },
  ];

  return (
    <div className="ccd">
      <ScheduleBrief rows={data.schedule} canRun={canRun} onDone={onDone} />

      <div className="ccd-sec">
        <div className="ccd-kpis">
          {kpis.map((t) => (
            <div
              className={'ccd-kpi' + (t.lead ? ' lead' : '')}
              key={t.l}
              style={{ '--ac': t.ac }}
            >
              <div className="ccd-kpi-l">{t.l}</div>
              <div className="ccd-kpi-v">{t.v ?? 0}</div>
              <div className="ccd-kpi-s">{t.s}</div>
            </div>
          ))}
        </div>
      </div>

      {speakers ? (
        <div className="ccd-sec">
          <div className="ccd-h">
            <h3>Speaker debt by invoice type</h3>
            <p>
              A blind invoice went to somebody who never asked for it, and is
              chased differently.
              {classified
                ? ` ${share(blind)}% of the decided speaker debt is blind.`
                : ' Nothing has been decided yet.'}
            </p>
            <span className="ccd-stamp">{speakers} speaker leads</span>
          </div>
          <div className="ccd-prop">
            {segments.filter((seg) => seg.n > 0).map((seg) => (
              <div
                className={`ccd-prop-seg ${seg.c}`}
                key={seg.t}
                style={{ flex: `${seg.n} 1 0` }}
                title={`${seg.t}: ${seg.n}`}
              >
                <div className="ccd-prop-n">{seg.n}</div>
                {/* A sliver narrower than about 6% cannot hold a label without
                    overprinting its neighbour, so it drops the label and keeps
                    the tooltip. */}
                {share(seg.n) >= 6 ? (
                  <div className="ccd-prop-t">{seg.t} · {share(seg.n)}%</div>
                ) : null}
              </div>
            ))}
          </div>
          <div className="ccd-prop-leg">
            {segments.map((seg) => (
              <span key={seg.t}>
                <i style={{ background: seg.hue }} />
                {seg.t} {seg.n}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      <div className="ccd-sec">
        <div className="ccd-h">
          <h3>Status per caller</h3>
          <p>
            The bar is the shape of each queue. Two callers with the same total
            and different shapes are a different problem, and a column of
            numbers hides that while a bar cannot.
          </p>
        </div>
        <div className="ccd-people">
          {Object.entries(status).map(([name, row]) => {
            const groups = Object.entries(row.groups || {});
            let roleClass = 'ccd-role-exec';
            if (row.role === 'Team lead') roleClass = 'ccd-role-lead';
            if (row.role === 'Off team') roleClass = 'ccd-role-off';
            return (
              <div className="ccd-person" key={name}>
                <div className="ccd-person-h">
                  <div style={{ minWidth: 0 }}>
                    <div className="ccd-person-n">{name}</div>
                    <span className={`ccd-role ${roleClass}`}>{row.role}</span>
                  </div>
                  <div>
                    <div className="ccd-person-v">{row.total}</div>
                    <div className="ccd-person-s">
                      {row.h36} / {row.h72} past {h36}h / {h72}h
                    </div>
                  </div>
                </div>
                <div className="ccd-stack">
                  {groups.map(([group, cell]) => (cell.total ? (
                    <i
                      key={group}
                      title={`${group}: ${cell.total}`}
                      style={{ background: GROUP_HUE[group], flex: `${cell.total} 1 0` }}
                    />
                  ) : null))}
                </div>
                {/* THREE FIGURES PER STATUS, not one. A group's total says how
                    much is sitting there; how much of it is past 36 and 72
                    hours says whether it is sitting there for a reason. */}
                <div className="ccd-legend is-aged">
                  <div className="ccd-legend-h" />
                  <div className="ccd-legend-h">Total</div>
                  <div className="ccd-legend-h">36h</div>
                  <div className="ccd-legend-h">72h</div>
                  {groups.flatMap(([group, cell]) => [
                    <div className="ccd-legend-k" key={`${group}-k`}>
                      <i style={{ background: GROUP_HUE[group] }} />
                      {group}
                    </div>,
                    <div className="ccd-legend-v" key={`${group}-t`}>{cell.total}</div>,
                    <div className={'ccd-legend-v' + (cell.h36 ? '' : ' is-nil')} key={`${group}-a`}>{cell.h36}</div>,
                    <div className={'ccd-legend-v' + (cell.h72 ? '' : ' is-nil')} key={`${group}-b`}>{cell.h72}</div>,
                  ])}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="ccd-sec">
        <div className="ccd-h">
          <h3>Invoice type, aged debtor</h3>
          <p>
            Every lead in exactly one row and one bucket, by days since the
            invoice. Blind and Requested apply to <b>speakers only</b>: a
            delegate booked something, so whether they asked for the invoice is
            not a question that applies to them. Sponsors are counted here even
            though another team chases them, because they are still money owed.
          </p>
          <span className="ccd-stamp">
            {aged.active} active + {aged.sponsors} sponsors
          </span>
        </div>
        <div className="tsc" style={{ borderRadius: 0 }}>
          <table className="ccd-t">
            <thead>
              <tr>
                <th>Invoice type</th>
                {aged.buckets.map((b) => <th key={b}>{b}d</th>)}
                <th>Total</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(aged.rows).map(([label, cells]) => {
                const indented = label.startsWith('Speakers, ');
                return (
                  <tr key={label}>
                    <td className={indented ? 'ccd-sub-row' : undefined}>
                      {indented ? label.replace('Speakers, ', '') : label}
                    </td>
                    {aged.buckets.map((b) => (
                      <HeatCell
                        key={b}
                        value={cells[b] || 0}
                        max={agedMax}
                        hue={AGED_HUE[label] || 'var(--t-500)'}
                      />
                    ))}
                    <td style={{ fontWeight: 800, color: 'var(--text)' }}>
                      {agedTotal(cells)}
                    </td>
                  </tr>
                );
              })}
              <tr className="ccd-t-tot">
                {/* NOT "Total active": this table counts sponsors too, so its
                    total is the whole book and is deliberately larger than the
                    Total Active tile above. Two figures that differ by design
                    have to say why. */}
                <td>Total owed</td>
                {bucketTotals.map((n, i) => <td key={aged.buckets[i]}>{n}</td>)}
                <td>{bucketTotals.reduce((a, b) => a + b, 0)}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div className="ccd-sec">
        <div className="ccd-h">
          <h3>Status bifurcation</h3>
          <p>
            Every active lead sits in exactly one disposition. Total first, then
            who is holding them, then how much of each has aged. A row at zero
            still renders, so the table keeps its shape from day to day.
          </p>
        </div>
        <div className="tsc" style={{ borderRadius: 0 }}>
          <table className="ccd-t">
            <thead>
              <tr>
                <th>Disposition</th>
                <th>Total</th>
                {reps.map((name) => <th key={name}>{name}</th>)}
                <th>Past {h36}h</th>
                <th>Past {h72}h</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(bifurcation).flatMap(([group, rows]) => {
                const entries = Object.entries(rows);
                const sum = (key) => entries.reduce((s, [, c]) => s + (c[key] || 0), 0);
                const repSum = (name) => entries.reduce(
                  (s, [, c]) => s + ((c.reps || {})[name] || 0), 0,
                );
                return [
                  <tr className="ccd-t-grp" key={group} style={{ '--ac': GROUP_HUE[group] }}>
                    <td>{group}</td>
                    <td>{sum('total')}</td>
                    {reps.map((name) => <td key={name}>{repSum(name)}</td>)}
                    <td>{sum('h36')}</td>
                    <td>{sum('h72')}</td>
                  </tr>,
                  ...entries.map(([label, cell]) => (
                    <tr key={`${group}-${label}`}>
                      <td className="ccd-sub-row">{label}</td>
                      <td className={cell.total ? undefined : 'ccd-zero'}>{cell.total}</td>
                      {reps.map((name) => {
                        const n = (cell.reps || {})[name] || 0;
                        return (
                          <td key={name} className={n ? undefined : 'ccd-zero'}>{n}</td>
                        );
                      })}
                      <td className={cell.h36 ? undefined : 'ccd-zero'}>{cell.h36}</td>
                      <td className={cell.h72 ? undefined : 'ccd-zero'}>{cell.h72}</td>
                    </tr>
                  )),
                ];
              })}
            </tbody>
          </table>
        </div>
      </div>

      <ResolvedAttribution data={data.resolved} />

      <div className="ccd-sec">
        <div className="ccd-h">
          <h3>Calls per caller by shift</h3>
          <p>
            One column is one 6:30pm to 3:30am IST shift, labelled by the
            evening it began.
            {matrix.source === 'hubspot'
              ? ' Real HubSpot calls, attributed by owner.'
              : ' HubSpot call data is unavailable, so these are dispositions '
                + 'logged here rather than calls placed.'}
          </p>
          <span className="ccd-stamp">{matrix.source}</span>
        </div>
        <div className="tsc" style={{ borderRadius: 0 }}>
          <table className="ccd-t">
            <thead>
              <tr>
                <th>Caller</th>
                {matrix.dates.map((d) => <th key={d}>{fdate(d)}</th>)}
                <th>Total</th>
              </tr>
            </thead>
            <tbody>
              {matrix.rows.map((row) => (
                <tr key={row.user}>
                  <td>{row.user}</td>
                  {row.counts.map((n, i) => (
                    <HeatCell
                      key={matrix.dates[i]}
                      value={n}
                      max={callMax}
                      hue="var(--t-500)"
                    />
                  ))}
                  <td style={{ fontWeight: 800, color: 'var(--text)' }}>
                    {row.counts.reduce((a, b) => a + b, 0)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/**
 * VIEWS, ONE PER ROUTE.
 *
 * The path decides the surface, so every nav entry is a real address. The
 * bucket keys stay what the engine writes; only the labels changed, because
 * renaming a stored value to improve a heading is how a migration gets written
 * for no reason.
 */
const VIEWS = {
  '': { title: 'Dashboard', bucket: null },
  collection: { title: 'Payment Collection', bucket: ccApi.BUCKETS.ACTIVE },
  'not-invoiced': { title: 'Not Invoiced', bucket: ccApi.BUCKETS.NOT_INVOICED },
  sponsors: { title: 'Sponsors', bucket: ccApi.BUCKETS.SPEX },
  resolved: { title: 'Resolved', bucket: ccApi.BUCKETS.DONE },
};

const ALL_CALLERS = '__all__';

export default function CreditControlPage() {
  const { canView, isAdmin } = useSession();
  const { view = '' } = useParams();
  const toast = useToast();

  const [rows, setRows] = useState([]);
  const [caller, setCaller] = useState(ALL_CALLERS);
  const [remarkFor, setRemarkFor] = useState(null);
  const [remarkText, setRemarkText] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const bumpRef = useRef(0);
  const [bump, setBump] = useState(0);

  const config = VIEWS[view] || VIEWS[''];
  const bucket = config.bucket;
  const isDash = bucket === null;

  const { data: dispositionRows } = useFetch(() => ccApi.dispositions(), []);
  const dispositions = useMemo(() => dispositionRows || [], [dispositionRows]);

  const groupOf = useCallback((label) => {
    if (!label) return 'Not Attempted';
    const found = dispositions.find((d) => d.label === label);
    return found ? found.status_group : 'Ongoing';
  }, [dispositions]);

  const { data: dashData, loading: dashLoading } = useFetch(
    () => (isDash ? ccApi.dashboard() : Promise.resolve(null)),
    [isDash, bump],
  );

  const { data: listData, loading: listLoading } = useFetch(
    () => (isDash ? Promise.resolve(null) : ccApi.list({ bucket })),
    [bucket, isDash, bump],
  );

  useEffect(() => {
    if (listData) setRows(listData.results || []);
  }, [listData]);

  // Reset the caller tab when the surface changes, so switching to Sponsors
  // and back does not land on a filter the new list cannot satisfy.
  useEffect(() => { setCaller(ALL_CALLERS); }, [view]);

  /**
   * One tab per caller, built from the rows themselves.
   *
   * Not from the roster: a lead can still be held by somebody who has since
   * left the team, and a tab strip that omits them would hide real work while
   * the totals still counted it. Ordered by size, so the busiest queue is
   * first, with the count on each tab.
   */
  const callers = useMemo(() => {
    if (isDash) return [];
    const seen = new Map();
    rows.forEach((r) => {
      const name = r.assigned_to_name || 'Unassigned';
      seen.set(name, (seen.get(name) || 0) + 1);
    });
    return [...seen.entries()].sort((a, b) => b[1] - a[1]);
  }, [rows, isDash]);

  const visible = useMemo(() => {
    if (caller === ALL_CALLERS) return rows;
    return rows.filter((r) => (r.assigned_to_name || 'Unassigned') === caller);
  }, [rows, caller]);

  /** Replace one row in place, so a save does not reload the whole list. */
  const onSaved = useCallback((saved) => {
    setRows((current) => current.map(
      (r) => (r.invoice_number === saved.invoice_number ? saved : r),
    ));
  }, []);

  /** Refetch the dashboard. Shared by the Refresh button and the job chips. */
  const reload = useCallback(() => {
    bumpRef.current += 1;
    setBump(bumpRef.current);
  }, []);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const summary = await ccApi.refresh();
      toast(
        `Routed ${summary.active} active, ${summary.handed_off} handed off, `
        + `${summary.done} resolved.`,
        'ok',
      );
      reload();
    } catch (err) {
      toast(apiErrorMessage(err, 'The routing pass did not run.'), 'er');
    } finally {
      setRefreshing(false);
    }
  }, [toast, reload]);

  const saveRemark = useCallback(async () => {
    if (!remarkFor) return;
    try {
      const saved = await ccApi.save(remarkFor.invoice_number, { remark: remarkText });
      onSaved(saved);
      setRemarkFor(null);
    } catch (err) {
      toast(apiErrorMessage(err, 'That remark did not save.'), 'er');
    }
  }, [remarkFor, remarkText, onSaved, toast]);

  if (!canView('credit_control')) return <NoAccessPage module="Credit Control" />;

  const head = (
    <div className="cc-head">
      <h1 className="cc-head-t">{config.title}</h1>
      <div className="cc-head-a">
        {!isDash ? (
          <span className="cc-head-n">
            {visible.length}
            {caller === ALL_CALLERS ? ' leads' : ` of ${rows.length}`}
          </span>
        ) : null}
        <button
          className="btn btn-p" type="button"
          onClick={onRefresh} disabled={refreshing}
        >
          <Icon name="refresh" size={15} />
          {refreshing ? 'Routing…' : 'Refresh'}
        </button>
      </div>
    </div>
  );

  return (
    /* .cc-mod zeroes every radius token for the whole module, so nothing
       inside it, down to the inputs and the tags, carries a rounded corner. */
    <div className="gs-page cc-mod">
      {head}

      {/* One tab per caller, on Payment Collection and the other registers.
          Eventually a caller will only be granted their own; until then the
          strip is how a lead reads across the team. */}
      {!isDash && callers.length > 1 ? (
        <div className="cc-tabs">
          <button
            type="button"
            className={'cc-tab' + (caller === ALL_CALLERS ? ' on' : '')}
            onClick={() => setCaller(ALL_CALLERS)}
          >
            Everyone<span className="cc-tab-n">{rows.length}</span>
          </button>
          {callers.map(([name, count]) => (
            <button
              type="button"
              key={name}
              className={'cc-tab' + (caller === name ? ' on' : '')}
              onClick={() => setCaller(name)}
            >
              {name}<span className="cc-tab-n">{count}</span>
            </button>
          ))}
        </div>
      ) : null}

      {isDash ? (
        dashLoading
          ? <EmptyState icon="clock" title="Loading" body="Building the aggregate." />
          : <Dashboard data={dashData} canRun={isAdmin} onDone={reload} />
      ) : listLoading ? (
        <EmptyState icon="clock" title="Loading" body="Fetching the list." />
      ) : bucket === ccApi.BUCKETS.SPEX ? (
        <SponsorTable rows={visible} />
      ) : bucket === ccApi.BUCKETS.DONE ? (
        <ReasonTable rows={visible} title="Nothing resolved yet" />
      ) : (
        <LeadTable
          bucket={bucket}
          rows={visible}
          dispositions={dispositions}
          groupOf={groupOf}
          onSaved={onSaved}
          onOpenRemark={(lead) => { setRemarkFor(lead); setRemarkText(lead.remark || ''); }}
        />
      )}

      {remarkFor ? (
        <Modal
          title={remarkFor.company}
          sub={`${remarkFor.invoice_number} · ${remarkFor.event_code}`}
          onClose={() => setRemarkFor(null)}
          footer={(
            <>
              <button className="btn btn-s" type="button" onClick={() => setRemarkFor(null)}>
                Cancel
              </button>
              <button className="btn btn-p" type="button" onClick={saveRemark}>
                Save remark
              </button>
            </>
          )}
        >
          <div className="fd">
            <label className="fd-l" htmlFor="cc-remark">Remark</label>
            <textarea
              id="cc-remark"
              className="in"
              rows={6}
              value={remarkText}
              style={{ height: 'auto', padding: '9px 10px', lineHeight: 1.55 }}
              onChange={(e) => setRemarkText(e.target.value)}
            />
          </div>

          <CaseVerdict
            lead={remarkFor}
            onOverride={async (value) => {
              try {
                const saved = await ccApi.setInvoiceType(remarkFor.invoice_number, value);
                onSaved(saved);
                setRemarkFor(saved);
                toast(value ? `Set to ${value}.` : 'Override cleared.', 'ok');
              } catch (err) {
                toast(apiErrorMessage(err, 'That override did not save.'), 'er');
              }
            }}
          />

          {remarkFor.handoff_brief ? (
            <div className="cc-case">
              <div className="cc-case-h">Handover note</div>
              <p className="cc-case-brief">{remarkFor.handoff_brief}</p>
            </div>
          ) : null}
        </Modal>
      ) : null}
    </div>
  );
}
