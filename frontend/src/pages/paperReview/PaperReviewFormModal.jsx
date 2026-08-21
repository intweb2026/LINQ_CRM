import { useState, useMemo } from 'react';
import Modal from '../../components/Modal';
import Select from '../../components/Select';
import { Icon } from '../../lib/icons';
import { Dot } from '../../components/Badge';
import { NumField } from '../../components/UI';
import { PAPER_REVIEW_CRITERIA, PAPER_REVIEW_MAX_SCORE, PAPER_GRADE_TONE, PAPER_SESSION_OPTIONS } from '../../lib/constants';
import * as paperReviewApi from '../../api/paperReview';
import { apiErrorMessage } from '../../api/client';
import { useFetch } from '../../hooks/useFetch';
import { useToast } from '../../context/ToastContext';
import { useConfirm } from '../../context/ConfirmContext';

// speaker_email_ref / research_email_ref are deliberately absent. They are
// OUTPUTS: the backend fills them with the addresses the production-team
// notification actually resolved at send time (paper_review/notifications.py),
// and they are read-only on the serializer. Offering them as inputs meant a typed
// address was silently discarded on save.
const BLANK = {
  paper_submission_date: '', event_code: '',
  speaker_name: '', company_name: '', email: '',
  linkedin_speaker: '', linkedin_followers: '', linkedin_company: '', nos: false,
  session_location_on_agenda: '', internal_footnotes: '', feedback_to_speaker: '',
  proposal_received: '', theme: '', agenda_addition: '',
};
PAPER_REVIEW_CRITERIA.forEach((c) => { BLANK[c.key] = ''; });

// Score/grade are derived, not entered — see the read-only fields at the end
// of the "Review scoring" section, matching the greyed-out fields in the
// reference screenshot. Bands are inferred — see PAPER_REVIEW_BACKEND.md.
// '' from an untouched input, null from a cleared NumField, undefined from a
// key the row never carried. 0 is a legal score and must not be caught here.
const isBlank = (v) => v === '' || v === null || v === undefined;

function gradeFor(pct) {
  if (pct >= 80) return 'A';
  if (pct >= 60) return 'B';
  if (pct >= 40) return 'C';
  return 'D';
}

export default function PaperReviewFormModal({ review, onClose, onSaved }) {
  const toast = useToast();
  const confirm = useConfirm();
  const isNew = !review;
  // permittedEvents, NOT the full events catalogue: access.py is the only
  // authority on which codes this user may attach a review to, and the validator
  // refuses the rest with a 400. Offering all 142 made a scoped user's save fail
  // for reasons the form never explained.
  const { data: events } = useFetch(paperReviewApi.permittedEvents, [], { initialData: [] });
  const EVENTS = events || [];
  const [form, setForm] = useState(() => (review ? { ...BLANK, ...review } : { ...BLANK }));
  const [saving, setSaving] = useState(false);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const setSel = (k) => (v) => setForm((f) => ({ ...f, [k]: v }));

  const score = useMemo(() => PAPER_REVIEW_CRITERIA.reduce((s, c) => s + (+form[c.key] || 0), 0), [form]);
  const pct = Math.round((score / PAPER_REVIEW_MAX_SCORE) * 100);
  const grade = gradeFor(pct);

  async function save() {
    // Convenience layer only — the serializer's REQUIRED_FIELDS stays the
    // authority. These are the ones users actually miss; catching them here
    // saves a round trip, and anything else still comes back named by
    // apiErrorMessage below.
    if (!form.event_code) { toast('Event code is required', 'er'); return; }
    if (!form.speaker_name.trim()) { toast('Speaker name is required', 'er'); return; }
    if (!form.email.trim()) { toast('Email address of the speaker is required', 'er'); return; }
    if (!form.linkedin_speaker.trim()) { toast('Speaker LinkedIn URL is required', 'er'); return; }
    if (isBlank(form.linkedin_followers)) { toast('LinkedIn followers count is required', 'er'); return; }
    if (!form.session_location_on_agenda) { toast('Session location on agenda is required', 'er'); return; }
    if (!form.proposal_received.trim()) { toast('Proposal received is required', 'er'); return; }
    if (!form.theme.trim()) { toast('Theme is required', 'er'); return; }
    if (!form.agenda_addition.trim()) { toast('Agenda addition is required', 'er'); return; }

    const missingCriterion = PAPER_REVIEW_CRITERIA.find((c) => isBlank(form[c.key]));
    if (missingCriterion) {
      toast(`${missingCriterion.label || missingCriterion.key} score is required`, 'er');
      return;
    }

    setSaving(true);
    const payload = {
      ...form,
      linkedin_followers: form.linkedin_followers === '' ? null : +form.linkedin_followers,
      paper_submission_date: form.paper_submission_date || null,
    };
    // Both are computed in PaperReview.save() and read-only on the serializer.
    // They arrive here on the edit path via {...BLANK, ...review}, so deleting
    // is not redundant: sending them implies the client owns values it does not.
    delete payload.proposal_score;
    delete payload.grade;
    PAPER_REVIEW_CRITERIA.forEach((c) => { payload[c.key] = payload[c.key] === '' ? null : +payload[c.key]; });
    try {
      if (isNew) await paperReviewApi.create(payload);
      else await paperReviewApi.update(review.id, payload);
    } catch (err) {
      // apiErrorMessage, not .detail: DRF field errors ({"linkedin_followers":
      // […]}) carry no detail key, so every one of them read as the generic
      // fallback and named nothing.
      toast(apiErrorMessage(err, 'Could not save — check the form and try again'), 'er');
      setSaving(false);
      return;
    }
    setSaving(false);
    onClose();
    toast((isNew ? 'Paper review added for ' : 'Paper review updated for ') + form.speaker_name, 'ok');
    onSaved?.();
  }

  async function del() {
    onClose();
    const ok = await confirm({ title: 'Delete this paper review?', sub: form.speaker_name + ' · ' + form.company_name, danger: true, ok: 'Delete', body: <p style={{ fontSize: 12.5, color: 'var(--text-3)' }}>This cannot be undone.</p> });
    if (ok) {
      try {
        await paperReviewApi.remove(review.id);
        toast('Paper review deleted', 'ok');
        onSaved?.();
      } catch {
        toast('Could not delete this paper review', 'er');
      }
    }
  }

  return (
    <Modal size="full" title={isNew ? 'New paper review' : 'Edit paper review'}
      sub={isNew ? 'Score a speaker proposal against an event.' : form.speaker_name + ' · ' + form.company_name}
      onClose={onClose}
      footJustify={isNew ? undefined : 'space-between'}
      footer={isNew ? (
        <><button className="btn btn-s" onClick={onClose}>Cancel</button><button className="btn btn-p" disabled={saving} onClick={save}><Icon name="check" size={15} />Create review</button></>
      ) : (
        <>
          <button className="btn btn-g" style={{ color: 'var(--red)' }} onClick={del}><Icon name="trash" size={14} />Delete review</button>
          <div style={{ display: 'flex', gap: 7 }}>
            <button className="btn btn-s" onClick={onClose}>Cancel</button>
            <button className="btn btn-p" disabled={saving} onClick={save}><Icon name="check" size={15} />Save changes</button>
          </div>
        </>
      )}>
      <div className="fs">
        <div className="fs-t"><Icon name="calendar" size={13} />Identification</div>
        <div className="fg c4">
          <div className="fd"><label className="fd-l">Paper submission date<span className="req">*</span></label><input className="in" type="date" value={form.paper_submission_date} onChange={set('paper_submission_date')} /></div>
          <div className="fd"><label className="fd-l">Event code<span className="req">*</span></label>
            <Select value={form.event_code} placeholder="— Select —" options={EVENTS.map((e) => e.event_code)} onChange={setSel('event_code')} />
          </div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="users" size={13} />Speaker &amp; company</div>
        <div className="fg c4">
          <div className="fd"><label className="fd-l">Speaker name<span className="req">*</span></label><input className="in" value={form.speaker_name} onChange={set('speaker_name')} /></div>
          <div className="fd"><label className="fd-l">Company name<span className="req">*</span></label><input className="in" value={form.company_name} onChange={set('company_name')} /></div>
          <div className="fd"><label className="fd-l">Email address of the speaker<span className="req">*</span></label><input className="in" type="email" value={form.email} onChange={set('email')} /></div>
          <div className="fd"><label className="fd-l">LinkedIn followers count<span className="req">*</span></label><NumField min={0} value={form.linkedin_followers} onChange={set('linkedin_followers')} /></div>
          <div className="fd" style={{ gridColumn: '1/3' }}><label className="fd-l">LinkedIn profile of speaker<span className="req">*</span></label><input className="in" type="url" placeholder="https://linkedin.com/in/…" value={form.linkedin_speaker} onChange={set('linkedin_speaker')} /></div>
          <div className="fd" style={{ gridColumn: '3/-1' }}><label className="fd-l">LinkedIn company profile</label><input className="in" type="url" placeholder="https://linkedin.com/company/…" value={form.linkedin_company} onChange={set('linkedin_company')} /></div>
          <div className="fd" style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingTop: 18 }}>
            <input type="checkbox" className="ck" id="pr-nos" checked={!!form.nos} onChange={(e) => setForm((f) => ({ ...f, nos: e.target.checked }))} />
            <label className="fd-l" htmlFor="pr-nos" style={{ marginBottom: 0 }}>NOS?</label>
          </div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="star" size={13} />Review scoring</div>
        <div className="fg c4">
          {PAPER_REVIEW_CRITERIA.map((c) => (
            <div className="fd" key={c.key}>
              <label className="fd-l">{c.label} ({c.max})<span className="req">*</span></label>
              {/* NumField, not a bare number input: min/max on an <input> are a
                  spinner hint, not a restriction, so every one of these boxes
                  accepted 99999 and the derived total read 364994 / 45. */}
              <NumField min={0} max={c.max} value={form[c.key]} onChange={set(c.key)} />
            </div>
          ))}
          <div className="fd">
            <label className="fd-l">Proposal score</label>
            <div className="in" style={{ display: 'flex', alignItems: 'center', background: 'var(--surface-2)', color: 'var(--text-2)' }}>{score} / {PAPER_REVIEW_MAX_SCORE}</div>
          </div>
          <div className="fd">
            <label className="fd-l">Grade (auto)</label>
            <div className="in" style={{ display: 'flex', alignItems: 'center', background: 'var(--surface-2)' }}><Dot tone={PAPER_GRADE_TONE[grade]}>{grade}</Dot></div>
            <span style={{ fontSize: 10, color: 'var(--text-4)' }}>Derived from score</span>
          </div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="flag" size={13} />Agenda &amp; feedback</div>
        <div className="fg c4">
          <div className="fd"><label className="fd-l">Session or location on agenda<span className="req">*</span></label>
            <Select value={form.session_location_on_agenda} placeholder="— Select —" options={PAPER_SESSION_OPTIONS} onChange={setSel('session_location_on_agenda')} />
          </div>
          <div className="fd" style={{ gridColumn: '2/-1' }}><label className="fd-l">Theme<span className="req">*</span></label><input className="in" value={form.theme} onChange={set('theme')} /></div>
          <div className="fd" style={{ gridColumn: '1/3' }}><label className="fd-l">Internal footnotes</label><input className="in" value={form.internal_footnotes} onChange={set('internal_footnotes')} /></div>
          <div className="fd" style={{ gridColumn: '3/-1' }}><label className="fd-l">Feedback to speaker or request information</label><input className="in" value={form.feedback_to_speaker} onChange={set('feedback_to_speaker')} /></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="note" size={13} />Proposal received<span className="req">*</span></div>
        <div className="fg">
          <div className="fd full"><textarea className="in" style={{ minHeight: 140 }} placeholder="Proposed session title, talking points…" value={form.proposal_received} onChange={set('proposal_received')} /></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="edit" size={13} />Agenda addition<span className="req">*</span></div>
        <div className="fg">
          <div className="fd full"><textarea className="in" style={{ minHeight: 140 }} placeholder="Agenda copy, industry tags…" value={form.agenda_addition} onChange={set('agenda_addition')} /></div>
        </div>
      </div>
    </Modal>
  );
}
