import { useState } from 'react';
import Modal from '../../components/Modal';
import Select from '../../components/Select';
import RichTextField from '../../components/RichTextField';
import { Icon } from '../../lib/icons';
import {
  PARTICIPATION_TYPES, QC_GRADES, SPEAKER_SLOT_STATUSES, SPONSORSHIP_STATUSES, REVENUE_POSSIBILITY,
} from '../../lib/constants';
import * as proposalApi from '../../api/proposalSubmission';
import { useFetch } from '../../hooks/useFetch';
import { useToast } from '../../context/ToastContext';
import { useConfirm } from '../../context/ConfirmContext';

const BLANK = {
  event_code: '', submission_date: '', participation_type: '',
  speaker_name: '', email: '', company_name: '',
  qc_grade: '', qc_score: '', presentation_theme: '', sales_pitch_factor: '',
  linkedin_speaker: '', linkedin_company: '', linkedin_followers: '',
  speaker_slot_status: '', sponsorship_status: '', spex_remarks: '',
  agenda_slot: '', revenue_possibility: '',
  internal_footnotes_mr: '', slot_recommendation_mr: '', agenda_addition: '',
};

// Shared Add/Edit form — fields follow the reference screenshots 1:1 (see
// PROPOSAL_SUBMISSION_BACKEND.md for the field contract this form submits).
export default function ProposalFormModal({ proposal, onClose, onSaved }) {
  const toast = useToast();
  const confirm = useConfirm();
  const isNew = !proposal;
  // permittedEvents, NOT the full events catalogue: access.py is the only
  // authority on which codes this user may attach a proposal to, and the
  // validator refuses the rest with a 400. Offering all 142 made a scoped user's
  // save fail for reasons the form never explained.
  const { data: events } = useFetch(proposalApi.permittedEvents, [], { initialData: [] });
  const EVENTS = events || [];
  const [form, setForm] = useState(() => (proposal ? { ...BLANK, ...proposal } : { ...BLANK }));
  const [saving, setSaving] = useState(false);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const setSel = (k) => (v) => setForm((f) => ({ ...f, [k]: v }));

  async function save() {
    if (!form.event_code) { toast('Event code is required', 'er'); return; }
    if (!form.speaker_name.trim()) { toast('Speaker name is required', 'er'); return; }
    if (!form.email.trim()) { toast('Email address is required', 'er'); return; }
    setSaving(true);
    const payload = {
      ...form,
      qc_score: form.qc_score === '' ? null : +form.qc_score,
      linkedin_followers: form.linkedin_followers === '' ? null : +form.linkedin_followers,
      submission_date: form.submission_date || null,
    };
    try {
      if (isNew) await proposalApi.create(payload);
      else await proposalApi.update(proposal.id, payload);
    } catch (err) {
      toast(err.response?.data?.detail || 'Could not save — check the form and try again', 'er');
      setSaving(false);
      return;
    }
    setSaving(false);
    onClose();
    toast((isNew ? 'Proposal added for ' : 'Proposal updated for ') + form.speaker_name, 'ok');
    onSaved?.();
  }

  async function del() {
    onClose();
    const ok = await confirm({ title: 'Delete this proposal?', sub: form.speaker_name + ' · ' + form.company_name, danger: true, ok: 'Delete', body: <p style={{ fontSize: 12.5, color: 'var(--text-3)' }}>This cannot be undone.</p> });
    if (ok) {
      try {
        await proposalApi.remove(proposal.id);
        toast('Proposal deleted', 'ok');
        onSaved?.();
      } catch {
        toast('Could not delete this proposal', 'er');
      }
    }
  }

  return (
    <Modal size="full" title={isNew ? 'New proposal submission' : 'Edit proposal submission'}
      sub={isNew ? 'Log a speaker or sponsorship proposal against an event.' : form.speaker_name + ' · ' + form.company_name}
      onClose={onClose}
      footJustify={isNew ? undefined : 'space-between'}
      footer={isNew ? (
        <><button className="btn btn-s" onClick={onClose}>Cancel</button><button className="btn btn-p" disabled={saving} onClick={save}><Icon name="check" size={15} />Create proposal</button></>
      ) : (
        <>
          <button className="btn btn-g" style={{ color: 'var(--red)' }} onClick={del}><Icon name="trash" size={14} />Delete proposal</button>
          <div style={{ display: 'flex', gap: 7 }}>
            <button className="btn btn-s" onClick={onClose}>Cancel</button>
            <button className="btn btn-p" disabled={saving} onClick={save}><Icon name="check" size={15} />Save changes</button>
          </div>
        </>
      )}>
      <div className="fs">
        <div className="fs-t"><Icon name="calendar" size={13} />Identification</div>
        <div className="fg c4">
          <div className="fd"><label className="fd-l">Event code<span className="req">*</span></label>
            <Select value={form.event_code} placeholder="— Select —" options={EVENTS.map((e) => e.event_code)} onChange={setSel('event_code')} />
          </div>
          <div className="fd"><label className="fd-l">Submission date</label><input className="in" type="date" value={form.submission_date} onChange={set('submission_date')} /></div>
          <div className="fd"><label className="fd-l">Participation type</label>
            <Select value={form.participation_type} placeholder="— Select —" options={PARTICIPATION_TYPES} onChange={setSel('participation_type')} />
          </div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="users" size={13} />Speaker &amp; company</div>
        <div className="fg c4">
          <div className="fd"><label className="fd-l">Speaker name<span className="req">*</span></label><input className="in" value={form.speaker_name} onChange={set('speaker_name')} /></div>
          <div className="fd"><label className="fd-l">Email address<span className="req">*</span></label><input className="in" type="email" value={form.email} onChange={set('email')} /></div>
          <div className="fd"><label className="fd-l">Company name</label><input className="in" value={form.company_name} onChange={set('company_name')} /></div>
          <div className="fd"><label className="fd-l">LinkedIn followers</label><input className="in" type="number" value={form.linkedin_followers} onChange={set('linkedin_followers')} /></div>
          <div className="fd" style={{ gridColumn: '1/-1' }}><label className="fd-l">LinkedIn (speaker)</label><input className="in" type="url" placeholder="https://linkedin.com/in/…" value={form.linkedin_speaker} onChange={set('linkedin_speaker')} /></div>
          <div className="fd" style={{ gridColumn: '1/-1' }}><label className="fd-l">LinkedIn (company)</label><input className="in" type="url" placeholder="https://linkedin.com/company/…" value={form.linkedin_company} onChange={set('linkedin_company')} /></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="star" size={13} />Quality &amp; content</div>
        <div className="fg c4">
          <div className="fd"><label className="fd-l">QC grade</label>
            <Select value={form.qc_grade} placeholder="— Select —" options={QC_GRADES} onChange={setSel('qc_grade')} />
          </div>
          <div className="fd"><label className="fd-l">QC score</label><input className="in" type="number" value={form.qc_score} onChange={set('qc_score')} /></div>
          <div className="fd" style={{ gridColumn: '3/-1' }}><label className="fd-l">Presentation theme</label><input className="in" value={form.presentation_theme} onChange={set('presentation_theme')} /></div>
          <div className="fd" style={{ gridColumn: '1/-1' }}><label className="fd-l">Sales pitch factor</label><input className="in" value={form.sales_pitch_factor} onChange={set('sales_pitch_factor')} /></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="flag" size={13} />Status &amp; revenue</div>
        <div className="fg c4">
          <div className="fd"><label className="fd-l">Speaker slot status</label>
            <Select value={form.speaker_slot_status} placeholder="— Select —" options={SPEAKER_SLOT_STATUSES} onChange={setSel('speaker_slot_status')} />
          </div>
          <div className="fd"><label className="fd-l">Sponsorship status</label>
            <Select value={form.sponsorship_status} placeholder="— Select —" options={SPONSORSHIP_STATUSES} onChange={setSel('sponsorship_status')} />
          </div>
          <div className="fd"><label className="fd-l">Revenue possibility</label>
            <Select value={form.revenue_possibility} placeholder="— Select —" options={REVENUE_POSSIBILITY} onChange={setSel('revenue_possibility')} />
          </div>
          <div className="fd"><label className="fd-l">Agenda slot</label><input className="in" placeholder="e.g. Day 1, Afternoon Session" value={form.agenda_slot} onChange={set('agenda_slot')} /></div>
          <div className="fd" style={{ gridColumn: '1/-1' }}><label className="fd-l">SpEx remarks</label><input className="in" value={form.spex_remarks} onChange={set('spex_remarks')} /></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="note" size={13} />Internal notes (MR)</div>
        <div className="fg c4">
          <div className="fd" style={{ gridColumn: '1/-1' }}><label className="fd-l">Internal footnotes (MR)</label><input className="in" value={form.internal_footnotes_mr} onChange={set('internal_footnotes_mr')} /></div>
          <div className="fd" style={{ gridColumn: '1/-1' }}><label className="fd-l">Slot recommendation by MR</label><input className="in" value={form.slot_recommendation_mr} onChange={set('slot_recommendation_mr')} /></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="edit" size={13} />Agenda addition</div>
        <div className="fg">
          {/* Not a textarea. Zoho exports this field as HTML and the importer
              stores it as it arrived, so a textarea showed the reader
              `<p><b>TITLE</b><br /></p><ul><li>…` instead of a title and three
              bullets. RichTextField renders the formatting and still edits it. */}
          {/* gridColumn, as every other wide field in this form sets it. `full`
              alone is a no-op here: the class is defined as `.dg .full`, so
              inside .fg it left the only field in the section sitting in the
              left half of a 1,560px modal. */}
          <div className="fd full" style={{ gridColumn: '1/-1' }}>
            <RichTextField value={form.agenda_addition} onChange={setSel('agenda_addition')}
              minHeight={200} placeholder="Session outline, talking points, tags…" />
          </div>
        </div>
      </div>
    </Modal>
  );
}
