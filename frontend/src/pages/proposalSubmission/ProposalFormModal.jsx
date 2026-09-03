import { useState } from 'react';
import Modal from '../../components/Modal';
import Select from '../../components/Select';
import RichTextField from '../../components/RichTextField';
import { Icon } from '../../lib/icons';
import { NumField } from '../../components/UI';
import {
  PARTICIPATION_TYPES, QC_GRADE_TONE, SPEAKER_SLOT_STATUSES, SPONSORSHIP_STATUSES,
  REVENUE_POSSIBILITY, PANEL_APPROACHED, SLOT_REOFFER_STATUSES, RISK_LEVELS,
  PAPER_SESSION_OPTIONS, STATUS_TONE,
} from '../../lib/constants';
import { Dot } from '../../components/Badge';
import { fdate } from '../../lib/helpers';
import * as proposalApi from '../../api/proposalSubmission';
import { apiErrorMessage } from '../../api/client';
import { useFetch } from '../../hooks/useFetch';
import { useToast } from '../../context/ToastContext';
import { useConfirm } from '../../context/ConfirmContext';

/**
 * A value the form SHOWS but does not own.
 *
 * Same treatment as proposal score and grade on PaperReviewFields.jsx: rendered
 * with the input's own styling so it lines up in the grid, on the muted surface
 * so it reads as inert, and with no onChange, no name and no id, so it is not a
 * control at all. Displaying beats omitting — the team needs these values while
 * working a row — but every one of them is read-only on the serializer, and a box
 * that silently discards what you type into it is worse than no box.
 */
function ReadOut({ label, hint, children }) {
  return (
    <div className="fd">
      <label className="fd-l">{label}</label>
      <div className="in" style={{ display: 'flex', alignItems: 'center', background: 'var(--surface-2)', color: 'var(--text-2)' }}>
        {children ?? <span className="dim">—</span>}
      </div>
      {hint ? <span style={{ fontSize: 10, color: 'var(--text-4)' }}>{hint}</span> : null}
    </div>
  );
}

// Everything the API returns but will not accept back. The MRE pair comes from
// the paper review rubric; the rest are annotations read from the event
// catalogue and from Bookings.
const READ_ONLY_KEYS = [
  'qc_grade', 'qc_score',
  'event_date', 'event_status', 'production_executive', 'spex_manager',
  'booking_date', 'payment_date', 'booking_status_se',
  'event_name', 'duplicate_count', 'qc_score_stale', 'source_paper_review',
  'import_batch_id', 'created_at', 'updated_at',
  'created_by', 'updated_by', 'created_by_name', 'updated_by_name',
];

const BLANK = {
  event_code: '', submission_date: '', participation_type: '',
  speaker_name: '', email: '', company_name: '',
  qc_grade: '', qc_score: '', presentation_theme: '', sales_pitch_factor: '',
  linkedin_speaker: '', linkedin_company: '', linkedin_followers: '',
  speaker_slot_status: '', sponsorship_status: '', spex_remarks: '',
  agenda_slot: '', speaking_slot_assignment: '', revenue_possibility: '',
  panel_approached: '', panel_topic: '', panel_status: '',
  speaker_slot_reoffered: '', risk_assessment_live: '', added_to_agenda: false,
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
      linkedin_followers: form.linkedin_followers === '' ? null : +form.linkedin_followers,
      submission_date: form.submission_date || null,
    };
    // Read-only on the serializer, and dropped rather than left in. On the edit
    // path `form` is {...BLANK, ...proposal}, so every one of these arrives from
    // the GET and would be posted straight back; sending them implies the client
    // owns values it does not. Same reasoning, and the same `delete`, as
    // buildPayload in paperReview/PaperReviewFields.jsx.
    READ_ONLY_KEYS.forEach((k) => { delete payload[k]; });
    try {
      if (isNew) await proposalApi.create(payload);
      else await proposalApi.update(proposal.id, payload);
    } catch (err) {
      // apiErrorMessage, not .detail: DRF field errors carry no detail key, so
      // every one of them read as the generic fallback and named nothing.
      toast(apiErrorMessage(err, 'Could not save — check the form and try again'), 'er');
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
          <div className="fd"><label className="fd-l">LinkedIn followers</label><NumField min={0} value={form.linkedin_followers} onChange={set('linkedin_followers')} /></div>
          <div className="fd" style={{ gridColumn: '1/-1' }}><label className="fd-l">LinkedIn (speaker)</label><input className="in" type="url" placeholder="https://linkedin.com/in/…" value={form.linkedin_speaker} onChange={set('linkedin_speaker')} /></div>
          <div className="fd" style={{ gridColumn: '1/-1' }}><label className="fd-l">LinkedIn (company)</label><input className="in" type="url" placeholder="https://linkedin.com/company/…" value={form.linkedin_company} onChange={set('linkedin_company')} /></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="star" size={13} />Quality &amp; content</div>
        <div className="fg c4">
          {/* MRE OUTPUT, not input. Both are produced by the paper review: score
              is summed from the six-criterion rubric and grade is derived from
              the score, server-side on every save. Typing either here would put
              a number on the row that no rubric produced, and it would make the
              qc_score_stale flag — which exists to show where a proposal and its
              review have diverged — impossible to read. Read-only on the
              serializer too (MRE_FIELDS), so these were boxes that discarded
              what you typed. */}
          <ReadOut label="QC score" hint="From the paper review rubric">
            {form.qc_score === '' || form.qc_score == null ? null : form.qc_score}
          </ReadOut>
          <ReadOut label="QC grade" hint="Derived from the score">
            {form.qc_grade ? <Dot tone={QC_GRADE_TONE[form.qc_grade] || 'neutral'}>{form.qc_grade}</Dot> : null}
          </ReadOut>
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
          {/* TWO fields, the same ten slots behind each. agenda_slot is the MRE's
              recommendation, which the bridge writes here from the paper review;
              speaking_slot_assignment is what the agenda team decides. Both left
              EDITABLE, unlike QC score and grade: nothing here was asked to be
              blocked, and a recommendation the team cannot correct on a manually
              created row is worse than one they can. */}
          <div className="fd"><label className="fd-l">Slot recommendation by MRE</label>
            <Select value={form.agenda_slot} placeholder="— Select —" options={PAPER_SESSION_OPTIONS} onChange={setSel('agenda_slot')} />
          </div>
          <div className="fd"><label className="fd-l">Speaking slot assignment</label>
            <Select value={form.speaking_slot_assignment} placeholder="— Select —" options={PAPER_SESSION_OPTIONS} onChange={setSel('speaking_slot_assignment')} />
          </div>
          {/* A checkbox, and a different question from the Agenda addition
              section below: that is the session outline, this is whether the
              speaker reached the published agenda. */}
          <div className="fd" style={{ display: 'flex', alignItems: 'center', gap: 7, alignSelf: 'end', paddingBottom: 6 }}>
            <input type="checkbox" className="ck" id="ps-added" name="added_to_agenda"
              checked={!!form.added_to_agenda}
              onChange={(e) => setForm((f) => ({ ...f, added_to_agenda: e.target.checked }))} />
            <label className="fd-l" htmlFor="ps-added" style={{ marginBottom: 0 }}>Added to agenda</label>
          </div>
          <div className="fd" style={{ gridColumn: '1/-1' }}><label className="fd-l">SpEx remarks</label><input className="in" value={form.spex_remarks} onChange={set('spex_remarks')} /></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="users" size={13} />Panel &amp; risk</div>
        {/* Three Selects and two text boxes, as specified. Panel status and panel
            topic are free text by decision, not by default, so they stay inputs
            however tempting a dropdown looks beside the other three.

            Select, not a checkbox, for Panel approached. It is the yes/no field
            asked for, and it keeps a THIRD state that a checkbox cannot hold: a
            blank means nobody has been approached yet, where an unticked box
            would assert "No" on every row the sheet imported empty. */}
        <div className="fg c4">
          <div className="fd"><label className="fd-l">Panel approached?</label>
            <Select value={form.panel_approached} placeholder="— Select —" options={PANEL_APPROACHED} onChange={setSel('panel_approached')} />
          </div>
          <div className="fd"><label className="fd-l">Speaker slot re-offered</label>
            <Select value={form.speaker_slot_reoffered} placeholder="— Select —" options={SLOT_REOFFER_STATUSES} onChange={setSel('speaker_slot_reoffered')} />
          </div>
          <div className="fd"><label className="fd-l">Risk assessment (live)</label>
            <Select value={form.risk_assessment_live} placeholder="— Select —" options={RISK_LEVELS} onChange={setSel('risk_assessment_live')} />
          </div>
          <div className="fd"><label className="fd-l">Panel status</label><input className="in" value={form.panel_status} onChange={set('panel_status')} /></div>
          <div className="fd" style={{ gridColumn: '1/-1' }}><label className="fd-l">Panel topic</label><input className="in" value={form.panel_topic} onChange={set('panel_topic')} /></div>
        </div>
      </div>
      {/* Read straight from Bookings, matched to this speaker on event code and
          email address. Shown here because the team works a row in one place, and
          an empty Booking date beside a Confirmed slot is the thing they are
          looking for. Not editable, and not editable ANYWHERE on this screen:
          Bookings owns these three values, and a second place to change them
          would be a second answer to the same question. Blank means this speaker
          has no booking on this event yet. */}
      <div className="fs">
        <div className="fs-t"><Icon name="download" size={13} />Booking &nbsp;<span style={{ fontWeight: 400, color: 'var(--text-4)', fontSize: 11 }}>from Bookings, read-only</span></div>
        <div className="fg c4">
          <ReadOut label="Booking date">{form.booking_date ? fdate(form.booking_date) : null}</ReadOut>
          <ReadOut label="Payment date">{form.payment_date ? fdate(form.payment_date) : null}</ReadOut>
          <ReadOut label="Booking status by SE">
            {form.booking_status_se ? <Dot tone={STATUS_TONE[form.booking_status_se] || 'neutral'}>{form.booking_status_se}</Dot> : null}
          </ReadOut>
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
