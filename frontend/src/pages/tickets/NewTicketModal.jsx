import { useState, useEffect } from 'react';
import Modal from '../../components/Modal';
import { Icon } from '../../lib/icons';
import { TK_PRIORITY } from '../../lib/constants';
import * as eventsApi from '../../api/events';
import * as usersApi from '../../api/users';
import { useFetch } from '../../hooks/useFetch';
import { uniq } from '../../lib/helpers';
import { useToast } from '../../context/ToastContext';
import * as ticketsApi from '../../api/tickets';

export default function NewTicketModal({ onClose, onCreated }) {
  const toast = useToast();
  const { data: events } = useFetch(eventsApi.list, [], { initialData: [] });
  const { data: users } = useFetch(usersApi.list, [], { initialData: [] });
  const { data: tickets } = useFetch(ticketsApi.list, [], { initialData: [] });
  const EVENTS = events || [];
  const TICKETS = tickets || [];
  const MR_USERS = (users || []).filter((u) => u.role === 'market_research' && u.status === 'active');
  const [form, setForm] = useState({
    source_event: '', purpose: '',
    link_url: '', linkedin_keywords: '', duplicate_tickets: '—', competitor_event_name: '', organizer: '',
    event_month_year: '', event_location: '', relationship: '', type_of_ticket: '',
    priority: Object.keys(TK_PRIORITY)[0], estimate: '', assigned_mr: '', mr_comments: '',
  });
  useEffect(() => {
    setForm((f) => ({
      ...f,
      source_event: f.source_event || EVENTS[0]?.event_code || '',
      purpose: f.purpose || uniq(TICKETS.map((t) => t.purpose))[0] || '',
      type_of_ticket: f.type_of_ticket || uniq(TICKETS.map((t) => t.type_of_ticket))[0] || '',
      assigned_mr: f.assigned_mr || MR_USERS[0]?.name || '',
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [EVENTS.length, TICKETS.length, MR_USERS.length]);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const ev = EVENTS.find((e) => e.event_code === form.source_event);

  async function create() {
    if (!form.purpose.trim()) { toast('Purpose is required', 'er'); return; }
    if (!form.link_url.trim()) { toast('Link URL is required', 'er'); return; }
    try {
      await ticketsApi.create({
        source_event: form.source_event, event_name: ev?.name || '', purpose: form.purpose, link_url: form.link_url.trim(),
        linkedin_keywords: form.linkedin_keywords.trim() || '—', duplicate_tickets: form.duplicate_tickets.trim() || '—',
        competitor_event_name: form.competitor_event_name.trim() || '—', organizer: form.organizer.trim() || '—',
        event_month_year: form.event_month_year || null, event_location: form.event_location.trim() || '—',
        relationship: form.relationship || 'Prospect', type_of_ticket: form.type_of_ticket, priority: form.priority,
        estimate: +form.estimate || 0, assigned_mr: form.assigned_mr, mr_comments: form.mr_comments.trim() || '—',
      });
    } catch (err) {
      toast('Could not create ticket — check the form and try again', 'er');
      return;
    }
    onClose();
    toast('Ticket created', 'ok');
    onCreated && onCreated();
  }

  return (
    <Modal size="full" title="New Ticket" onClose={onClose}
      footer={<><button className="btn btn-s" onClick={onClose}>Cancel</button><button className="btn btn-p" onClick={create}><Icon name="check" size={15} />Create Ticket</button></>}>
      <div className="fs">
        <div className="fg c3">
          <div className="fd"><label className="fd-l">Event Code</label>
            <select className="in" value={form.source_event} onChange={set('source_event')}>
              {EVENTS.map((e) => <option key={e.id} value={e.event_code}>{e.event_code}</option>)}
            </select>
          </div>
          <div className="fd"><label className="fd-l">Event Name</label><input className="in" value={ev?.name || ''} readOnly /></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="target" size={13} />Market Research</div>
        <div className="fg c3">
          <div className="fd"><label className="fd-l">Purpose<span className="req">*</span></label>
            <select className="in" value={form.purpose} onChange={set('purpose')}>{uniq(TICKETS.map((t) => t.purpose)).map((p) => <option key={p}>{p}</option>)}</select>
          </div>
          <div className="fd"><label className="fd-l">Link URL<span className="req">*</span></label><input className="in" placeholder="linkedin.com/company/…" value={form.link_url} onChange={set('link_url')} /></div>
          <div className="fd"><label className="fd-l">LinkedIn Keywords</label><input className="in" placeholder="hydrogen, electrolyser" value={form.linkedin_keywords} onChange={set('linkedin_keywords')} /></div>

          <div className="fd"><label className="fd-l">Duplicate Tickets</label><input className="in" value={form.duplicate_tickets} onChange={set('duplicate_tickets')} /></div>
          <div className="fd"><label className="fd-l">Competitor Event Name</label><input className="in" placeholder="e.g. Hydrogen World 2026" value={form.competitor_event_name} onChange={set('competitor_event_name')} /></div>
          <div className="fd"><label className="fd-l">Organizer</label><input className="in" placeholder="e.g. Informa" value={form.organizer} onChange={set('organizer')} /></div>

          <div className="fd"><label className="fd-l">Event Month/Year</label><input className="in" type="date" value={form.event_month_year} onChange={set('event_month_year')} /></div>
          <div className="fd"><label className="fd-l">Event Location (City, Region)</label><input className="in" placeholder="City, Country" value={form.event_location} onChange={set('event_location')} /></div>
          <div className="fd"><label className="fd-l">Relationship</label>
            <select className="in" value={form.relationship} onChange={set('relationship')}>
              <option value="">—</option>
              {uniq(TICKETS.map((t) => t.relationship)).map((p) => <option key={p}>{p}</option>)}
            </select>
          </div>

          <div className="fd"><label className="fd-l">Type of Ticket</label>
            <select className="in" value={form.type_of_ticket} onChange={set('type_of_ticket')}>
              <option value="">—</option>
              {uniq(TICKETS.map((t) => t.type_of_ticket)).map((p) => <option key={p}>{p}</option>)}
            </select>
          </div>
          <div className="fd"><label className="fd-l">Priority</label>
            <select className="in" value={form.priority} onChange={set('priority')}>
              <option value="">—</option>
              {Object.keys(TK_PRIORITY).map((p) => <option key={p}>{p}</option>)}
            </select>
          </div>
          <div className="fd"><label className="fd-l">Estimate</label><input className="in" type="number" placeholder="250" value={form.estimate} onChange={set('estimate')} /></div>

          <div className="fd"><label className="fd-l">Assigned MR</label>
            <select className="in" value={form.assigned_mr} onChange={set('assigned_mr')}>{MR_USERS.map((u) => <option key={u.id}>{u.name}</option>)}</select>
          </div>
          <div className="fd" style={{ gridColumn: '2/-1' }}><label className="fd-l">MR Comments</label><textarea className="in" placeholder="Anything Data Mining should know…" value={form.mr_comments} onChange={set('mr_comments')} /></div>
        </div>
      </div>
    </Modal>
  );
}
