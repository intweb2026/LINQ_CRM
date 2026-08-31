import { useMemo } from 'react';
import Select from '../../components/Select';
import { Icon } from '../../lib/icons';
import { Dot } from '../../components/Badge';
import { NumField } from '../../components/UI';
import { PAPER_REVIEW_CRITERIA, PAPER_REVIEW_MAX_SCORE, PAPER_GRADE_TONE, PAPER_SESSION_OPTIONS } from '../../lib/constants';

/**
 * THE paper review form fields, rendered identically in two places.
 *
 * PaperReviewFormModal is the CRM form; PublicPaperReviewFormPage is the MRE
 * link a reviewer opens without a login (backend/paper_review/public_form.py).
 * They differ in their frame, their submit call and one field, and in nothing
 * else — a second copy of twenty-one inputs and a six-criterion rubric is how
 * the public form would quietly start asking for different things than the CRM
 * form asks for.
 *
 * The validation and payload helpers live here for the same reason: both callers
 * post to serializers with the same REQUIRED_FIELDS, so the pre-flight checks
 * have to agree.
 *
 * speaker_email_ref / research_email_ref are deliberately absent. They are
 * OUTPUTS: the backend fills them with the addresses the production-team
 * notification actually resolved at send time (paper_review/notifications.py),
 * and they are read-only on the serializer. Offering them as inputs meant a typed
 * address was silently discarded on save.
 */
export const BLANK = {
  paper_submission_date: '', event_code: '',
  speaker_name: '', company_name: '', email: '',
  linkedin_speaker: '', linkedin_followers: '', linkedin_company: '', nos: false,
  session_location_on_agenda: '', internal_footnotes: '', feedback_to_speaker: '',
  proposal_received: '', theme: '', agenda_addition: '',
};
PAPER_REVIEW_CRITERIA.forEach((c) => { BLANK[c.key] = ''; });

// '' from an untouched input, null from a cleared NumField, undefined from a
// key the row never carried. 0 is a legal score and must not be caught here.
export const isBlank = (v) => v === '' || v === null || v === undefined;

// Bands mirror GRADE_BANDS in paper_review/models.py; the grade shown here is a
// preview of what the server will derive, never a value that is sent.
export function gradeFor(pct) {
  if (pct >= 80) return 'A';
  if (pct >= 60) return 'B';
  if (pct >= 40) return 'C';
  return 'D';
}

export const scoreOf = (form) =>
  PAPER_REVIEW_CRITERIA.reduce((s, c) => s + (+form[c.key] || 0), 0);

/**
 * The first missing required field, as a message, or null when the form is
 * complete.
 *
 * Convenience layer only — the serializer's REQUIRED_FIELDS stays the authority.
 * These are the ones users actually miss; catching them here saves a round trip,
 * and anything else still comes back named by apiErrorMessage.
 */
export function firstMissing(form) {
  if (!form.event_code) return 'Event code is required';
  if (!form.speaker_name.trim()) return 'Speaker name is required';
  if (!form.company_name.trim()) return 'Company name is required';
  if (!form.email.trim()) return 'Email address of the speaker is required';
  if (!form.linkedin_speaker.trim()) return 'Speaker LinkedIn URL is required';
  if (isBlank(form.linkedin_followers)) return 'LinkedIn followers count is required';
  if (!form.session_location_on_agenda) return 'Session location on agenda is required';
  if (!form.proposal_received.trim()) return 'Proposal received is required';
  if (!form.theme.trim()) return 'Theme is required';
  if (!form.agenda_addition.trim()) return 'Agenda addition is required';
  const missing = PAPER_REVIEW_CRITERIA.find((c) => isBlank(form[c.key]));
  if (missing) return `${missing.label || missing.key} score is required`;
  return null;
}

/**
 * The form as the API wants it.
 *
 * proposal_score and grade are computed in PaperReview.save() and read-only on
 * the serializer. They arrive in `form` on the edit path via {...BLANK, ...review},
 * so deleting them is not redundant: sending them implies the client owns values
 * it does not.
 */
export function buildPayload(form) {
  const payload = {
    ...form,
    linkedin_followers: form.linkedin_followers === '' ? null : +form.linkedin_followers,
    paper_submission_date: form.paper_submission_date || null,
  };
  delete payload.proposal_score;
  delete payload.grade;
  PAPER_REVIEW_CRITERIA.forEach((c) => { payload[c.key] = payload[c.key] === '' ? null : +payload[c.key]; });
  return payload;
}

export default function PaperReviewFields({ form, setForm, events, showInternal = true }) {
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const setSel = (k) => (v) => setForm((f) => ({ ...f, [k]: v }));
  const EVENTS = events || [];

  const score = useMemo(() => scoreOf(form), [form]);
  const pct = Math.round((score / PAPER_REVIEW_MAX_SCORE) * 100);
  const grade = gradeFor(pct);

  return (
    <>
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
          {/* MR-only, and absent rather than disabled on the public MRE form:
              PublicPaperReviewSerializer does not accept the field, so an input
              for it would silently discard whatever was typed. */}
          {showInternal ? (
            <div className="fd" style={{ gridColumn: '1/3' }}><label className="fd-l">Internal footnotes</label><input className="in" value={form.internal_footnotes} onChange={set('internal_footnotes')} /></div>
          ) : null}
          <div className="fd" style={{ gridColumn: showInternal ? '3/-1' : '1/3' }}><label className="fd-l">Feedback to speaker or request information</label><input className="in" value={form.feedback_to_speaker} onChange={set('feedback_to_speaker')} /></div>
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

    </>
  );
}
