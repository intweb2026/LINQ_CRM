import { useState } from 'react';
import Drawer from '../../components/Drawer';
import Modal from '../../components/Modal';
import { Icon } from '../../lib/icons';
import { TkBadge, PriBadge, Who } from '../../components/Badge';
import { fdate, fmy, rel, nf } from '../../lib/helpers';
import { useSession } from '../../context/SessionContext';
import { useToast } from '../../context/ToastContext';
import * as ticketsApi from '../../api/tickets';

export default function TicketDrawer({ ticket: t, onClose, onChanged }) {
  const { can } = useSession();
  const toast = useToast();
  const [returning, setReturning] = useState(false);
  const [reason, setReason] = useState('');
  if (!t) return null;

  async function submit() { await ticketsApi.submitToDMD(t.id); onClose(); toast(t.ticket_number + ' submitted to Data Mining', 'ok'); onChanged(); }
  async function markDone() { await ticketsApi.markComplete(t.id); onClose(); toast(t.ticket_number + ' marked complete', 'ok'); onChanged(); }
  async function doReturn() {
    if (!reason.trim()) { toast('A reason is required', 'er'); return; }
    await ticketsApi.returnToMR(t.id, reason.trim());
    setReturning(false); onClose();
    toast(t.ticket_number + ' returned to MR', 'wn'); onChanged();
  }

  return (
    <>
      <Drawer
        wide onClose={onClose}
        head={
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 3, flexWrap: 'wrap' }}>
              <span className="mono" style={{ color: 'var(--t-600)' }}>{t.ticket_number}</span><TkBadge value={t.status} /><PriBadge value={t.priority} />
            </div>
            <h2>{t.purpose}</h2><p>{t.type_of_ticket} · source {t.source_event} · raised {rel(t.created_at)}</p>
          </div>
        }
        foot={<>
          <button className="btn btn-s" onClick={onClose}>Close</button>
          {t.status === 'draft' && can('update', 'ticket_central') ? <button className="btn btn-p" onClick={submit}><Icon name="send" size={15} />Submit to DMD</button> : null}
          {t.status === 'mr_submitted' && can('update', 'ticket_central') ? <>
            <button className="btn btn-s btn-do" onClick={() => setReturning(true)}><Icon name="refresh" size={15} />Return to MR</button>
            <button className="btn btn-p" onClick={markDone}><Icon name="check" size={15} />Mark complete</button>
          </> : null}
          {t.status === 'returned' && can('update', 'ticket_central') ? <button className="btn btn-p" onClick={submit}><Icon name="send" size={15} />Resubmit</button> : null}
        </>}
      >
        <div className="sl">MR Section — research brief</div>
        <div className="ro">
          <div className="ro-c f"><div className="ro-l">Competitor event</div><div className="ro-v">{t.competitor_event_name}</div></div>
          <div className="ro-c"><div className="ro-l">Organizer</div><div className="ro-v">{t.organizer}</div></div>
          <div className="ro-c"><div className="ro-l">Event month</div><div className="ro-v">{fmy(t.event_month_year)}</div></div>
          <div className="ro-c f"><div className="ro-l">Event location</div><div className="ro-v">{t.event_location}</div></div>
          <div className="ro-c"><div className="ro-l">Relationship</div><div className="ro-v">{t.relationship}</div></div>
          <div className="ro-c"><div className="ro-l">Estimate</div><div className="ro-v">{nf(t.estimate)} contacts</div></div>
          <div className="ro-c"><div className="ro-l">Assigned MR</div><div className="ro-v">{t.assigned_mr}</div></div>
          <div className="ro-c"><div className="ro-l">Duplicate of</div><div className="ro-v">{t.duplicate_tickets}</div></div>
          <div className="ro-c f"><div className="ro-l">Link</div><div className="ro-v"><a href="#" onClick={(e) => e.preventDefault()}>{t.link_url}</a></div></div>
          <div className="ro-c f"><div className="ro-l">LinkedIn keywords</div><div className="ro-v" style={{ fontWeight: 500 }}>{t.linkedin_keywords}</div></div>
          <div className="ro-c f"><div className="ro-l">MR comments</div><div className="ro-v" style={{ fontWeight: 500 }}>{t.mr_comments}</div></div>
        </div>
        <div className="sl">DMD Section — mining result</div>
        <div className="ro">
          <div className="ro-c"><div className="ro-l">Assigned to</div><div className="ro-v">{t.assign_name}</div></div>
          <div className="ro-c"><div className="ro-l">Assign date</div><div className="ro-v">{t.assign_date ? fdate(t.assign_date) : '—'}</div></div>
          <div className="ro-c"><div className="ro-l">Ticket type</div><div className="ro-v">{t.ticket_type}</div></div>
          <div className="ro-c"><div className="ro-l">Complete date</div><div className="ro-v">{t.complete_date ? fdate(t.complete_date) : '—'}</div></div>
          <div className="ro-c f"><div className="ro-l">DM comments</div><div className="ro-v" style={{ fontWeight: 500 }}>{t.dm_comments}</div></div>
        </div>
        <div className="ms">
          <div><div className="l">Actual</div><div className="v">{t.actual_number == null ? '—' : nf(t.actual_number)}</div></div>
          <div><div className="l">New contacts</div><div className="v g">{t.new_contacts_created == null ? '—' : nf(t.new_contacts_created)}</div></div>
          <div><div className="l">Mined</div><div className="v">{t.mined_count == null ? '—' : nf(t.mined_count)}</div></div>
        </div>
        {t.assign_name_lx2 !== '—' ? (
          <>
            <div className="sl">LX-2 second pass</div>
            <div className="ro">
              <div className="ro-c"><div className="ro-l">Assigned</div><div className="ro-v">{t.assign_name_lx2}</div></div>
              <div className="ro-c"><div className="ro-l">Count</div><div className="ro-v">{t.actual_count_lx2 == null ? '—' : nf(t.actual_count_lx2)}</div></div>
            </div>
          </>
        ) : null}
        <div className="sl">Workflow</div>
        <div className="tl">
          <div className="tl-i"><span className="tl-d t"><Icon name="edit" size={10} /></span><div><div className="tl-t">Ticket raised</div><div className="tl-s">{t.assigned_mr} · Market Research</div><div className="tl-m">{rel(t.created_at)}</div></div></div>
          {t.status !== 'draft' ? <div className="tl-i"><span className={'tl-d ' + (t.status === 'returned' ? 'r' : 'g')}><Icon name="send" size={10} /></span><div><div className="tl-t">Submitted to Data Mining</div><div className="tl-s">{t.assign_name}</div><div className="tl-m">{t.assign_date ? rel(t.assign_date) : '—'}</div></div></div> : null}
          {t.status === 'completed' ? <div className="tl-i"><span className="tl-d g"><Icon name="check" size={10} /></span><div><div className="tl-t">Mining complete</div><div className="tl-s">{nf(t.mined_count)} contacts · HubSpot {t.hubspot_entry_date ? fdate(t.hubspot_entry_date) : 'pending'}</div><div className="tl-m">{rel(t.complete_date)}</div></div></div> : null}
          {t.status === 'returned' ? <div className="tl-i"><span className="tl-d r"><Icon name="refresh" size={10} /></span><div><div className="tl-t">Returned to MR</div><div className="tl-s">Insufficient detail to mine</div><div className="tl-m">recent</div></div></div> : null}
        </div>
      </Drawer>
      {returning ? (
        <Modal size="sm" title="Return to Market Research" sub={t.ticket_number} onClose={() => setReturning(false)}
          footer={<><button className="btn btn-s" onClick={() => setReturning(false)}>Cancel</button><button className="btn btn-d" onClick={doReturn}>Return ticket</button></>}>
          <div className="fd"><label className="fd-l">Reason<span className="req">*</span></label><textarea className="in" placeholder="What detail is missing?" value={reason} onChange={(e) => setReason(e.target.value)} /></div>
        </Modal>
      ) : null}
    </>
  );
}
