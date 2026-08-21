import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Modal from '../../components/Modal';
import { Icon } from '../../lib/icons';
import { NumField } from '../../components/UI';
import { EVENT_STATUSES, YES_NO, VR1_STATUS, SALES_CHECK_OPTIONS } from '../../lib/constants';
import * as usersApi from '../../api/users';
import { useFetch } from '../../hooks/useFetch';
import { useToast } from '../../context/ToastContext';
import * as eventsApi from '../../api/events';
import { OWNER_EDIT_FIELDS } from '../../lib/owners';

// Only the SCA and the sales team leader are editable here — see OWNER_EDIT_FIELDS
// in lib/owners.js. The other five owner columns belong to the Teams module and are
// shown, inherited, in the drawer's Teams tab and the Events table; giving them an
// editor here only invites someone to re-type what the team already knows, and a
// value typed on the event outranks the team's answer permanently.
//
// The selects read form values RAW rather than through ownerOf(): an inherited name
// is the team's answer, and writing it into the event would freeze "whoever leads
// Sales" into one person's name on the next unrelated save.
const OWNER_LABELS = OWNER_EDIT_FIELDS.map((f) => f.label);
const OWNER_KEYS = OWNER_EDIT_FIELDS.map((f) => f.key);

export default function NewEventModal({ onClose, onSaved }) {
  const toast = useToast();
  const nav = useNavigate();
  const { data: allUsers } = useFetch(usersApi.list, [], { initialData: [] });
  const pool = (allUsers || []).filter((u) => u.status === 'active');
  const [form, setForm] = useState({
    event_code: '', edition: '', name: '', status: 'Draft', event_type: '',
    event_date: '', end_date: '', location: '', website_live_date: '',
    website: '', web_bookings_enabled: 'No', nearest_related: '', vr1_status: 'Not Sent',
    capacity: '', sales_check: 'Unassigned',
    email_marketing_name: '', branding_name: '', annualisation: 'Annual', date_format: 'DD-MM-YYYY',
    related_event_1: '', related_event_2: '', related_event_3: '',
    upcoming_event_1: '', upcoming_event_2: '', upcoming_event_3: '',
    owners: OWNER_KEYS.map(() => ''),
  });
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const setOwner = (i) => (e) => setForm((f) => { const o = [...f.owners]; o[i] = e.target.value; return { ...f, owners: o }; });

  async function create() {
    const code = form.event_code.trim(), name = form.name.trim(), start = form.event_date;
    if (!code || !name || !start) { toast('Event code, name and start date are required', 'er'); return; }
    const end = form.end_date || new Date(new Date(start).getTime() + 2 * 864e5).toISOString().slice(0, 10);
    const ownerFields = {};
    // EMPTY, never '—'. This used to write a literal em dash into every owner
    // column it had no editor for, and blank is now what makes a column inherit
    // the owning team's lead: a stored '—' is a value, so it suppressed the
    // team's answer and left the row showing nothing on every event created here.
    // The columns with no editor are not sent at all, so they keep the model
    // default and inherit.
    OWNER_KEYS.forEach((k, i) => { ownerFields[k] = form.owners[i] || ''; });
    try {
      await eventsApi.create({
        event_code: code, name, location: form.location.trim() || '—', event_date: start, end_date: end,
        website_live_date: form.website_live_date || null,
        website: form.website.trim() || '', web_bookings_enabled: form.web_bookings_enabled,
        nearest_related: form.nearest_related.trim() || '—', vr1_status: form.vr1_status,
        status: form.status, event_type: form.event_type.trim() || 'Summit', edition: form.edition.trim() || '1st',
        capacity: +form.capacity || 300, sales_check: form.sales_check,
        ...ownerFields,
        email_marketing_name: form.email_marketing_name.trim() || name,
        branding_name: form.branding_name.trim() || code,
        annualisation: form.annualisation.trim() || 'Annual',
        date_format: form.date_format.trim() || 'DD-MM-YYYY',
        related_event_1: form.related_event_1.trim() || '—', related_event_2: form.related_event_2.trim() || '—', related_event_3: form.related_event_3.trim() || '—',
        upcoming_event_1: form.upcoming_event_1.trim() || '—', upcoming_event_2: form.upcoming_event_2.trim() || '—', upcoming_event_3: form.upcoming_event_3.trim() || '—',
      });
    } catch (err) {
      if (err.response?.data?.event_code) { toast('That event code already exists', 'er'); return; }
      toast('Could not create event — check the form and try again', 'er');
      return;
    }
    onClose(); toast(code + ' added to the catalogue', 'ok'); onSaved?.(); nav('/events');
  }

  return (
    <Modal size="full" title="New event" sub="Add an event to the catalogue." onClose={onClose}
      footer={<><button className="btn btn-s" onClick={onClose}>Cancel</button><button className="btn btn-p" onClick={create}><Icon name="check" size={15} />Create event</button></>}>
      <div className="fs">
        <div className="fs-t"><Icon name="calendar" size={13} />Identification</div>
        <div className="fg c4">
          <div className="fd"><label className="fd-l">Event code<span className="req">*</span></label><input className="in mono" placeholder="e.g. XYZ - PM27" value={form.event_code} onChange={set('event_code')} /></div>
          <div className="fd"><label className="fd-l">Edition</label><input className="in" placeholder="e.g. 1st" value={form.edition} onChange={set('edition')} /></div>
          <div className="fd"><label className="fd-l">Status</label><select className="in" value={form.status} onChange={set('status')}>{EVENT_STATUSES.map((s) => <option key={s}>{s}</option>)}</select></div>
          <div className="fd"><label className="fd-l">Event type</label><input className="in" placeholder="e.g. Summit" value={form.event_type} onChange={set('event_type')} /></div>
          <div className="fd full" style={{ gridColumn: '1/-1' }}><label className="fd-l">Official event name<span className="req">*</span></label><input className="in" placeholder="e.g. Battery Recycling Summit 2027" value={form.name} onChange={set('name')} /></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="globe" size={13} />Schedule &amp; location</div>
        <div className="fg c4">
          <div className="fd"><label className="fd-l">Start date<span className="req">*</span></label><input className="in" type="date" value={form.event_date} onChange={set('event_date')} /></div>
          <div className="fd"><label className="fd-l">End date</label><input className="in" type="date" value={form.end_date} onChange={set('end_date')} /></div>
          <div className="fd"><label className="fd-l">Website live date</label><input className="in" type="date" value={form.website_live_date} onChange={set('website_live_date')} /></div>
          <div className="fd"><label className="fd-l">Location</label><input className="in" placeholder="City, Country" value={form.location} onChange={set('location')} /></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="globe" size={13} />Web presence</div>
        <div className="fg c4">
          <div className="fd"><label className="fd-l">Website</label><input className="in" type="url" placeholder="https://example.com" value={form.website} onChange={set('website')} /></div>
          <div className="fd"><label className="fd-l">Web bookings</label><select className="in" value={form.web_bookings_enabled} onChange={set('web_bookings_enabled')}>{YES_NO.map((o) => <option key={o}>{o}</option>)}</select></div>
          <div className="fd"><label className="fd-l">Nearest related event</label><input className="in" value={form.nearest_related} onChange={set('nearest_related')} /></div>
          <div className="fd"><label className="fd-l">VR1 sent status</label><select className="in" value={form.vr1_status} onChange={set('vr1_status')}>{VR1_STATUS.map((o) => <option key={o}>{o}</option>)}</select></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="target" size={13} />Classification &amp; capacity</div>
        <div className="fg c4">
          <div className="fd"><label className="fd-l">Capacity</label><NumField min={0} placeholder="300" value={form.capacity} onChange={set('capacity')} /></div>
          <div className="fd"><label className="fd-l">Sales check</label><select className="in" value={form.sales_check} onChange={set('sales_check')}>{SALES_CHECK_OPTIONS.map((o) => <option key={o}>{o}</option>)}</select></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="target" size={13} />Naming &amp; metadata</div>
        <div className="fg c4">
          <div className="fd"><label className="fd-l">Event name for email marketing</label><input className="in" placeholder="e.g. Battery Recycling Summit" value={form.email_marketing_name} onChange={set('email_marketing_name')} /></div>
          <div className="fd"><label className="fd-l">Event name for branding</label><input className="in" placeholder="e.g. BRS 27" value={form.branding_name} onChange={set('branding_name')} /></div>
          <div className="fd"><label className="fd-l">Annualisation</label><input className="in" placeholder="e.g. Annual" value={form.annualisation} onChange={set('annualisation')} /></div>
          <div className="fd"><label className="fd-l">Date format</label><input className="in" placeholder="e.g. DD-MM-YYYY" value={form.date_format} onChange={set('date_format')} /></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="link" size={13} />Related &amp; upcoming events</div>
        <div className="fg c3">
          <div className="fd"><label className="fd-l">Related event 1</label><input className="in" value={form.related_event_1} onChange={set('related_event_1')} /></div>
          <div className="fd"><label className="fd-l">Related event 2</label><input className="in" value={form.related_event_2} onChange={set('related_event_2')} /></div>
          <div className="fd"><label className="fd-l">Related event 3</label><input className="in" value={form.related_event_3} onChange={set('related_event_3')} /></div>
          <div className="fd"><label className="fd-l">Upcoming event 1</label><input className="in" value={form.upcoming_event_1} onChange={set('upcoming_event_1')} /></div>
          <div className="fd"><label className="fd-l">Upcoming event 2</label><input className="in" value={form.upcoming_event_2} onChange={set('upcoming_event_2')} /></div>
          <div className="fd"><label className="fd-l">Upcoming event 3</label><input className="in" value={form.upcoming_event_3} onChange={set('upcoming_event_3')} /></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="users" size={13} />Team ownership</div>
        <div className="fg c4">
          {OWNER_LABELS.map((lbl, i) => (
            <div className="fd" key={lbl}>
              <label className="fd-l">{lbl}</label>
              <select className="in" value={form.owners[i]} onChange={setOwner(i)}>
                <option value="">— Unassigned —</option>
                {pool.map((u) => <option key={u.id}>{u.name}</option>)}
              </select>
            </div>
          ))}
        </div>
      </div>
    </Modal>
  );
}
