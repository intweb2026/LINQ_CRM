import { useMemo, useState } from 'react';
import Modal from '../../components/Modal';
import Select from '../../components/Select';
import { Icon } from '../../lib/icons';
import { TkBadge, Who } from '../../components/Badge';
import { extUrl, fdate, nf, rel } from '../../lib/helpers';
import { TK_PRIORITY, TK_RELATIONSHIPS, TK_TICKET_TYPES, TK_TYPES } from '../../lib/constants';
import * as usersApi from '../../api/users';
import { useFetch } from '../../hooks/useFetch';
import { useSession } from '../../context/SessionContext';
import { useToast } from '../../context/ToastContext';
import * as ticketsApi from '../../api/tickets';
import { apiErrorMessage } from '../../api/client';

/**
 * Ticket Central's one form, used for both New and Edit.
 *
 * Layout mirrors the Zoho Creator form this module replaces, field for field and
 * column for column: a "Ticket Hub" section (what MR raises) then a "For DMD"
 * section (what Data Mining fills in). Both are three COLUMNS read top to bottom,
 * which is why each is a .fcol stack inside the grid rather than the row-major .fg
 * the other modals use — .fg alone would deal the fields across the rows and the
 * order would no longer match the form people already know.
 *
 * Add and Edit are the same component on purpose. The two drifted apart in the
 * Zoho app and users learned two different layouts for one record; here a field
 * can only be added, moved or relabelled in one place.
 */

// Mirrors ticket_central/constants.py — the backend refuses a PATCH that carries
// a field from the other side's section (TicketMRUpdateSerializer.validate /
// TicketDMDUpdateSerializer.validate), so which section a field belongs to is not
// a cosmetic grouping here: it decides what may be sent.
const MR_KEYS = [
  'purpose', 'link_url', 'linkedin_keywords', 'duplicate_tickets',
  'competitor_event_name', 'organizer', 'event_month_year', 'event_location',
  'relationship', 'type_of_ticket', 'priority', 'estimate', 'mr_comments', 'assigned_mr',
];
const DMD_KEYS = [
  'assign_name', 'assign_date', 'actual_number', 'new_contacts_created',
  'source_spreadsheet_id', 'source_tab', 'source_row_number', 'idempotency_key',
  'ticket_type', 'complete_date', 'hubspot_entry_date', 'mined_count', 'dm_comments',
  'assign_name_lx2', 'actual_count_lx2', 'complete_date_lx2', 'dm_comments_lx2',
];
const ALL_KEYS = [...MR_KEYS, ...DMD_KEYS];

// Everything is held as a string while editing (that is what an <input> gives
// back); these two sets say how to turn each one back into what the API wants.
const NUM_KEYS = new Set(['estimate', 'actual_number', 'new_contacts_created', 'source_row_number', 'mined_count', 'actual_count_lx2']);
const DATE_KEYS = new Set(['event_month_year', 'assign_date', 'complete_date', 'hubspot_entry_date', 'complete_date_lx2']);

/**
 * A ticket row (or nothing, for a new one) as form state.
 *
 * '—' collapses to empty: the seed data and the modal this replaced both wrote a
 * literal em-dash for "blank", so it is a value that exists in the column and
 * would otherwise be presented as if someone had typed it — and then saved back.
 */
function toForm(t) {
  const f = {};
  ALL_KEYS.forEach((k) => {
    const v = t ? t[k] : null;
    f[k] = v == null || v === '—' ? '' : String(v);
  });
  return f;
}

function outValue(key, raw) {
  const s = typeof raw === 'string' ? raw.trim() : raw;
  if (NUM_KEYS.has(key)) return s === '' ? null : Number(s);
  if (DATE_KEYS.has(key)) return s === '' ? null : s;
  return s;
}

// The first readable line out of a DRF error body lives in api/client.js now —
// the Bookings modals need the same reader, and this local copy answered
// "[object Object]" for the nested-error shape.
const errText = apiErrorMessage;

// .fd-h puts the label beside the field, right-aligned against it, the way the
// Zoho form does — see overlays.css. Collapses to label-above on a narrow screen.
function Field({ label, req, children }) {
  return (
    <div className="fd fd-h">
      <label className="fd-l">{label}{req ? <span className="req">*</span> : null}</label>
      {children}
    </div>
  );
}

/**
 * A picklist that cannot lose the value already stored.
 *
 * type_of_ticket, priority, ticket_type and relationship are free CharFields (D4)
 * holding Zoho text, so a row can carry a value that is not on the offered list.
 * That value is appended to its own dropdown rather than displayed as blank —
 * otherwise merely opening a ticket and saving something else would wipe it.
 * Choosing the placeholder row clears the field.
 */
function Pick({ value, options, onChange, disabled, placeholder = '—Select—' }) {
  if (disabled) return <input className="in" value={value} readOnly disabled />;
  const opts = value && !options.includes(value) ? [...options, value] : options;
  return (
    <Select value={value} placeholder={placeholder} options={[placeholder, ...opts]}
      onChange={(v) => onChange(v === placeholder ? '' : v)} />
  );
}

export default function TicketFormModal({ ticket, onClose, onSaved }) {
  const { can, user } = useSession();
  const toast = useToast();
  const isNew = !ticket;
  const { data: users } = useFetch(usersApi.list, [], { initialData: [] });
  // Active users' emails, because that is the shape assigned_mr actually stores
  // (every non-blank value in the column is an @iq-hub.com address) and it is the
  // same list the server offers for a mass assign — see TicketViewSet
  // .bulk_update_fields. A name here would write a value nothing else matches.
  const mrEmails = useMemo(
    () => (users || []).filter((u) => u.status === 'active' && u.email).map((u) => u.email).sort((a, b) => a.localeCompare(b)),
    [users],
  );

  const [initial] = useState(() => toForm(ticket));
  const [form, setForm] = useState(() => toForm(ticket));
  const [saving, setSaving] = useState(false);
  const [returning, setReturning] = useState(false);
  const [reason, setReason] = useState('');

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const pick = (k) => (v) => setForm((f) => ({ ...f, [k]: v }));

  // ── Who may write what ───────────────────────────────────────────────────
  // These mirror the serializer guards exactly (ticket_central/serializers.py).
  // Rendering a field the server will refuse produces a save that fails with a
  // message about a field the user was invited to type in, so the form locks the
  // same doors the API does and says why underneath the section title.
  const mayWrite = can(isNew ? 'create' : 'update', 'ticket_central');
  const isAdmin = user.role === 'admin';
  const status = ticket?.status;
  const mrOpen = mayWrite && (isNew || isAdmin || (user.role === 'market_research' && (status === 'draft' || status === 'returned')));
  const dmdOpen = mayWrite && !isNew && (isAdmin || (user.role === 'data_mining' && status === 'mr_submitted'));
  const mrLock = mrOpen ? null
    : isNew ? 'You do not have permission to raise tickets.'
      : user.role === 'market_research' ? 'Read-only — MR fields are editable while a ticket is Draft or Returned.'
        : 'Read-only for your role.';
  const dmdLock = dmdOpen ? null
    : isNew ? 'Data Mining fills this in after the ticket is submitted — the API refuses these fields at create.'
      : user.role === 'data_mining' ? 'Read-only — DMD fields are editable while a ticket is MR Submitted.'
        : 'Read-only for your role.';

  const patch = useMemo(() => {
    const out = {};
    ALL_KEYS.forEach((k) => {
      const open = MR_KEYS.includes(k) ? mrOpen : dmdOpen;
      if (open && form[k] !== initial[k]) out[k] = outValue(k, form[k]);
    });
    return out;
  }, [form, initial, mrOpen, dmdOpen]);
  const dirty = Object.keys(patch).length > 0;

  async function create() {
    if (!form.purpose.trim()) { toast('Purpose is required', 'er'); return; }
    if (!form.link_url.trim()) { toast('Link URL is required', 'er'); return; }
    if (!form.type_of_ticket.trim()) { toast('Type of Ticket is required', 'er'); return; }
    const body = {};
    // MR fields only. A create that carries any DMD key is rejected outright by
    // TicketCreateSerializer.validate, so blanks are omitted rather than sent.
    MR_KEYS.forEach((k) => {
      const v = outValue(k, form[k]);
      if (v !== '' && v !== null) body[k] = v;
    });
    setSaving(true);
    try {
      await ticketsApi.create(body);
    } catch (err) {
      toast(errText(err, 'Could not create ticket — check the form and try again'), 'er');
      setSaving(false);
      return;
    }
    setSaving(false);
    onClose();
    toast('Ticket created', 'ok');
    onSaved?.();
  }

  async function save() {
    if (!dirty) { onClose(); return; }
    setSaving(true);
    try {
      await ticketsApi.update(ticket.id, patch);
    } catch (err) {
      toast(errText(err, 'Could not save this ticket — check the form and try again'), 'er');
      setSaving(false);
      return;
    }
    setSaving(false);
    onClose();
    toast((ticket.ticket_number || 'Ticket') + ' saved', 'ok');
    onSaved?.();
  }

  /**
   * A workflow transition, run after any pending field edits are saved.
   *
   * Status moves through the @action endpoints, never through PATCH, so a user who
   * typed into the form and then pressed "Mark complete" would otherwise lose what
   * they typed — the transition would succeed and the edits would go nowhere.
   */
  async function transition(run, done) {
    setSaving(true);
    try {
      if (dirty) await ticketsApi.update(ticket.id, patch);
      await run();
    } catch (err) {
      toast(errText(err, 'Could not update this ticket'), 'er');
      setSaving(false);
      return;
    }
    setSaving(false);
    onClose();
    toast(done.msg, done.tone || 'ok');
    onSaved?.();
  }

  const submit = () => transition(() => ticketsApi.submitToDMD(ticket.id), { msg: (ticket.ticket_number || 'Ticket') + ' submitted to Data Mining' });
  const markDone = () => transition(() => ticketsApi.markComplete(ticket.id), { msg: (ticket.ticket_number || 'Ticket') + ' marked complete' });
  async function doReturn() {
    if (!reason.trim()) { toast('A reason is required', 'er'); return; }
    setReturning(false);
    await transition(() => ticketsApi.returnToMR(ticket.id, reason.trim()), { msg: (ticket.ticket_number || 'Ticket') + ' returned to MR', tone: 'wn' });
  }

  const canAct = !isNew && can('update', 'ticket_central');
  const url = extUrl(form.link_url);

  return (
    <>
      <Modal size="full" onClose={onClose}
        title="Ticket Central"
        sub={isNew ? 'New ticket — Market Research raises it, Data Mining works the queue.'
          : [ticket.ticket_number, ticket.purpose, ticket.type_of_ticket].filter(Boolean).join(' · ')}
        footJustify={isNew ? undefined : 'space-between'}
        footer={isNew ? (
          <>
            <button className="btn btn-s" onClick={onClose}>Cancel</button>
            <button className="btn btn-p" disabled={saving || !mrOpen} onClick={create}><Icon name="check" size={15} />Create Ticket</button>
          </>
        ) : (
          <>
            <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
              {canAct && (status === 'draft' || status === 'returned')
                ? <button className="btn btn-s" disabled={saving} onClick={submit}><Icon name="send" size={15} />{status === 'returned' ? 'Resubmit' : 'Submit to DMD'}</button> : null}
              {canAct && status === 'mr_submitted' ? <>
                <button className="btn btn-s btn-do" disabled={saving} onClick={() => setReturning(true)}><Icon name="refresh" size={15} />Return to MR</button>
                <button className="btn btn-s" disabled={saving} onClick={markDone}><Icon name="check" size={15} />Mark complete</button>
              </> : null}
            </div>
            <div style={{ display: 'flex', gap: 7 }}>
              <button className="btn btn-s" onClick={onClose}>Close</button>
              <button className="btn btn-p" disabled={saving || !dirty} onClick={save}><Icon name="check" size={15} />Save changes</button>
            </div>
          </>
        )}>

        <div className="fs">
          <div className="fs-t"><Icon name="target" size={13} />Ticket Hub</div>
          {mrLock ? <div className="hint" style={{ marginBottom: 10 }}>{mrLock}</div> : null}
          <div className="fg c3">
            <div className="fcol">
              <Field label="Purpose" req>
                <input className="in" placeholder="e.g. CCU" value={form.purpose} onChange={set('purpose')} disabled={!mrOpen} />
              </Field>
              <Field label="Link URL" req>
                <div className="fd-lnk">
                  <input className="in" placeholder="https://…" value={form.link_url} onChange={set('link_url')} disabled={!mrOpen} />
                  {/* Opens what the field currently holds, resolved the same way the
                      table's link column resolves it — so a value with no scheme
                      goes to the site rather than back to the CRM. */}
                  <a className="fd-lnk-go" href={url || undefined} target="_blank" rel="noopener noreferrer"
                    aria-disabled={url ? undefined : 'true'} title={url || 'Not a link'} aria-label="Open link in a new tab">
                    <Icon name="link" size={14} />
                  </a>
                </div>
              </Field>
              <Field label="LinkedIn Keywords">
                <input className="in" placeholder="hydrogen, electrolyser" value={form.linkedin_keywords} onChange={set('linkedin_keywords')} disabled={!mrOpen} />
              </Field>
              <Field label="Duplicate Tickets">
                <input className="in" value={form.duplicate_tickets} onChange={set('duplicate_tickets')} disabled={!mrOpen} />
              </Field>
            </div>
            <div className="fcol">
              <Field label="Competitor Event Name">
                <input className="in" placeholder="e.g. Hydrogen World 2026" value={form.competitor_event_name} onChange={set('competitor_event_name')} disabled={!mrOpen} />
              </Field>
              <Field label="Organizer">
                <input className="in" placeholder="e.g. Informa" value={form.organizer} onChange={set('organizer')} disabled={!mrOpen} />
              </Field>
              <Field label="Event Month/Year">
                <input className="in" type="date" value={form.event_month_year} onChange={set('event_month_year')} disabled={!mrOpen} />
              </Field>
              <Field label="Event Location (City, Region)">
                <input className="in" placeholder="City, Country" value={form.event_location} onChange={set('event_location')} disabled={!mrOpen} />
              </Field>
              <Field label="Relationship (Direct/Indirect)">
                <Pick value={form.relationship} options={TK_RELATIONSHIPS} onChange={pick('relationship')} disabled={!mrOpen} />
              </Field>
            </div>
            <div className="fcol">
              <Field label="Type of Ticket" req>
                <Pick value={form.type_of_ticket} options={TK_TYPES} onChange={pick('type_of_ticket')} disabled={!mrOpen} />
              </Field>
              <Field label="Priority">
                <Pick value={form.priority} options={Object.keys(TK_PRIORITY)} onChange={pick('priority')} disabled={!mrOpen} />
              </Field>
              <Field label="Estimate">
                <input className="in" type="number" min="0" placeholder="250" value={form.estimate} onChange={set('estimate')} disabled={!mrOpen} />
              </Field>
              <Field label="MR Comments">
                <input className="in" placeholder="Anything Data Mining should know…" value={form.mr_comments} onChange={set('mr_comments')} disabled={!mrOpen} />
              </Field>
              <Field label="Assigned MR">
                <Pick value={form.assigned_mr} options={mrEmails} onChange={pick('assigned_mr')} disabled={!mrOpen} placeholder="—Unassigned—" />
              </Field>
            </div>
          </div>
        </div>

        <div className="fs">
          <div className="fs-t"><Icon name="sheet" size={13} />For DMD</div>
          {dmdLock ? <div className="hint" style={{ marginBottom: 10 }}>{dmdLock}</div> : null}
          <div className="fg c3">
            <div className="fcol">
              <Field label="Ticket Number">
                {/* Server-assigned at create from purpose + type code, and reused
                    from gaps by assign_next_ticket_number — never caller-writable,
                    at any status or role. */}
                <input className="in" value={ticket?.ticket_number || ''} placeholder={isNew ? 'Assigned on create' : ''} readOnly disabled />
              </Field>
              <Field label="Assign Name">
                <input className="in" value={form.assign_name} onChange={set('assign_name')} disabled={!dmdOpen} />
              </Field>
              <Field label="Assign Date">
                <input className="in" type="date" value={form.assign_date} onChange={set('assign_date')} disabled={!dmdOpen} />
              </Field>
              <Field label="Actual Number">
                <input className="in" type="number" min="0" value={form.actual_number} onChange={set('actual_number')} disabled={!dmdOpen} />
              </Field>
              <Field label="New Contacts Created">
                <input className="in" type="number" min="0" value={form.new_contacts_created} onChange={set('new_contacts_created')} disabled={!dmdOpen} />
              </Field>
              <Field label="Source_Spreadsheet_ID">
                <input className="in" value={form.source_spreadsheet_id} onChange={set('source_spreadsheet_id')} disabled={!dmdOpen} />
              </Field>
              <Field label="Source_Tab">
                <input className="in" value={form.source_tab} onChange={set('source_tab')} disabled={!dmdOpen} />
              </Field>
              <Field label="Source_Row_Number">
                <input className="in" type="number" min="0" value={form.source_row_number} onChange={set('source_row_number')} disabled={!dmdOpen} />
              </Field>
              <Field label="Idempotency_Key">
                <input className="in" value={form.idempotency_key} onChange={set('idempotency_key')} disabled={!dmdOpen} />
              </Field>
            </div>
            <div className="fcol">
              <Field label="Ticket Type">
                <Pick value={form.ticket_type} options={TK_TICKET_TYPES} onChange={pick('ticket_type')} disabled={!dmdOpen} />
              </Field>
              <Field label="Complete Date">
                <input className="in" type="date" value={form.complete_date} onChange={set('complete_date')} disabled={!dmdOpen} />
              </Field>
              <Field label="HubSpot Entry Date">
                <input className="in" type="date" value={form.hubspot_entry_date} onChange={set('hubspot_entry_date')} disabled={!dmdOpen} />
              </Field>
              <Field label="Mined Count">
                <input className="in" type="number" min="0" value={form.mined_count} onChange={set('mined_count')} disabled={!dmdOpen} />
              </Field>
              <Field label="DM Comments">
                <input className="in" value={form.dm_comments} onChange={set('dm_comments')} disabled={!dmdOpen} />
              </Field>
            </div>
            <div className="fcol">
              <Field label="Assign Name (LX-2)">
                <input className="in" value={form.assign_name_lx2} onChange={set('assign_name_lx2')} disabled={!dmdOpen} />
              </Field>
              <Field label="Actual Count (LX-2)">
                <input className="in" type="number" min="0" value={form.actual_count_lx2} onChange={set('actual_count_lx2')} disabled={!dmdOpen} />
              </Field>
              <Field label="Complete Date - LX2">
                <input className="in" type="date" value={form.complete_date_lx2} onChange={set('complete_date_lx2')} disabled={!dmdOpen} />
              </Field>
              <Field label="DM Comments (LX-2)">
                <input className="in" value={form.dm_comments_lx2} onChange={set('dm_comments_lx2')} disabled={!dmdOpen} />
              </Field>
            </div>
          </div>
        </div>

        {/* Record — status and the system stamps, LAST rather than in the header.
            Status is not a field: every transition goes through submit_mr /
            submit_dmd / return_to_mr so the audit trail is stamped, which is what
            the buttons in the footer do. */}
        {isNew ? null : (
          <div className="fs">
            <div className="fs-t"><Icon name="clock" size={13} />Record</div>
            <div className="ro">
              <div className="ro-c"><div className="ro-l">Added Time</div><div className="ro-v">{fdate(ticket.created_at)}</div></div>
              <div className="ro-c"><div className="ro-l">Modified Time</div><div className="ro-v">{fdate(ticket.updated_at)}</div></div>
              <div className="ro-c"><div className="ro-l">Added User</div><div className="ro-v">{ticket.added_user_text || ticket.created_by_name || '—'}</div></div>
              <div className="ro-c"><div className="ro-l">ID</div><div className="ro-v mono">{ticket.id}</div></div>
              <div className="ro-c"><div className="ro-l">Event Code</div><div className="ro-v mono">{ticket.event_code || '—'}</div></div>
              <div className="ro-c"><div className="ro-l">Status</div><div className="ro-v"><TkBadge value={ticket.status} /></div></div>
            </div>
            <div className="sl">Workflow</div>
            <div className="tl">
              <div className="tl-i"><span className="tl-d t"><Icon name="edit" size={10} /></span><div>
                <div className="tl-t">Ticket raised</div>
                <div className="tl-s">{ticket.assigned_mr || ticket.created_by_name || 'Market Research'}</div>
                <div className="tl-m">{rel(ticket.created_at)}</div>
              </div></div>
              {ticket.mr_submitted_at ? (
                <div className="tl-i"><span className="tl-d g"><Icon name="send" size={10} /></span><div>
                  <div className="tl-t">Submitted to Data Mining</div>
                  <div className="tl-s">{ticket.mr_submitted_by_name || '—'}</div>
                  <div className="tl-m">{rel(ticket.mr_submitted_at)}</div>
                </div></div>
              ) : null}
              {ticket.returned_at ? (
                <div className="tl-i"><span className="tl-d r"><Icon name="refresh" size={10} /></span><div>
                  <div className="tl-t">Returned to MR</div>
                  <div className="tl-s">{ticket.return_reason || 'No reason recorded'}</div>
                  <div className="tl-m">{rel(ticket.returned_at)}</div>
                </div></div>
              ) : null}
              {ticket.status === 'completed' ? (
                <div className="tl-i"><span className="tl-d g"><Icon name="check" size={10} /></span><div>
                  <div className="tl-t">Mining complete</div>
                  <div className="tl-s">{nf(ticket.mined_count)} contacts · HubSpot {ticket.hubspot_entry_date ? fdate(ticket.hubspot_entry_date) : 'pending'}</div>
                  <div className="tl-m">{ticket.dmd_submitted_at ? rel(ticket.dmd_submitted_at) : fdate(ticket.complete_date)}</div>
                </div></div>
              ) : null}
              {ticket.assign_name && ticket.assign_name !== '—' ? (
                <div className="tl-i"><span className="tl-d"><Icon name="users" size={10} /></span><div>
                  <div className="tl-t">Assigned to</div>
                  <div className="tl-s"><Who name={ticket.assign_name} /></div>
                  <div className="tl-m">{ticket.assign_date ? fdate(ticket.assign_date) : '—'}</div>
                </div></div>
              ) : null}
            </div>
          </div>
        )}
      </Modal>

      {returning ? (
        <Modal size="sm" title="Return to Market Research" sub={ticket.ticket_number} onClose={() => setReturning(false)}
          footer={<>
            <button className="btn btn-s" onClick={() => setReturning(false)}>Cancel</button>
            <button className="btn btn-d" onClick={doReturn}>Return ticket</button>
          </>}>
          <div className="fd"><label className="fd-l">Reason<span className="req">*</span></label>
            <textarea className="in" placeholder="What detail is missing?" value={reason} onChange={(e) => setReason(e.target.value)} />
          </div>
        </Modal>
      ) : null}
    </>
  );
}
