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
 * speaker_email_ref / research_email_ref are SHOWN BUT NOT EDITABLE. They are
 * OUTPUTS: the backend fills them with the addresses the production-team
 * notification actually resolved at send time (paper_review/notifications.py),
 * and they are read-only on the serializer. They were absent entirely until the
 * form was checked against the Zoho layout, which carries both; they are back as
 * read-only boxes, alongside proposal score and grade, and NOT as inputs.
 * Offering them as inputs meant a typed address was silently discarded on save,
 * and paper_review/tests_notification.py still asserts they never become one.
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

/**
 * The letter for a RAW SCORE out of 45, mirroring GRADE_BANDS in
 * paper_review/models.py. A preview of what the server will derive, never a
 * value that is sent — grade is read-only on the serializer.
 *
 * Takes the score, not a percentage. The bands are score ranges (A 36-45,
 * B+ 31-35, B 26-30, C 21-25, D 11-20, E 0-10), and converting to a percentage
 * first rounds boundary scores into the wrong band.
 */
export function gradeFor(score) {
  if (score >= 36) return 'A';
  if (score >= 31) return 'B+';
  if (score >= 26) return 'B';
  if (score >= 21) return 'C';
  if (score >= 11) return 'D';
  return 'E';
}

/**
 * The six criteria as NOS wants them, for spreading over the form.
 *
 * A NOS speaker is not scored: the rubric does not apply, so the boxes lock and
 * the total reads 0 / 45, grade E, which is what the server derives anyway.
 * Unchecking blanks them rather than leaving the zeroes behind — a real review
 * saved at 0 / 45 because nobody noticed the boxes were still holding NOS's
 * zeroes is the one outcome worse than retyping six numbers.
 */
export const scoreReset = (nos) =>
  Object.fromEntries(PAPER_REVIEW_CRITERIA.map((c) => [c.key, nos ? 0 : '']));

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

/**
 * `native` is what makes this form machine-fillable, and it is the whole reason
 * the two pickers have a second rendering.
 *
 * The public MRE link, PublicPaperReviewFormPage, is filled by people and, now,
 * by an assistant driving a browser on a reviewer's behalf. Both read the page
 * through the accessibility tree, and the CRM rendering gave an assistant nothing
 * to read. Every box was an anonymous input whose visible name sat in an unlinked
 * sibling label, so the tree showed fifteen textboxes called nothing; and the two
 * dropdowns were a trigger button plus a portalled, position fixed panel, see
 * components/Select.jsx, which is not a control anything but a human pointer can
 * operate.
 *
 * So every field now carries id === name === its payload key with its label
 * pointing at it, on BOTH forms, because an unnamed input is a bug on the CRM
 * form too; and `native` swaps the two pickers for real select elements on the
 * public page only, where being operable beats being styled. The CRM modal keeps
 * the themed dropdown it was built for.
 */
export default function PaperReviewFields({ form, setForm, events, showInternal = true, native = false }) {
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const setSel = (k) => (v) => setForm((f) => ({ ...f, [k]: v }));
  const EVENTS = events || [];

  const score = useMemo(() => scoreOf(form), [form]);
  const grade = gradeFor(score);

  const lab = (k, text, req) => (
    <label className="fd-l" htmlFor={'pr-' + k}>{text}{req ? <span className="req">*</span> : null}</label>
  );

  // Server-owned values, displayed the way proposal score and grade are. No id,
  // no name, no onChange: nothing here is part of the payload.
  const ro = (text, value, note) => (
    <div className="fd">
      <label className="fd-l">{text}</label>
      <div className="in" style={{ display: 'flex', alignItems: 'center', background: 'var(--surface-2)', color: 'var(--text-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {value || <span className="dim">—</span>}
      </div>
      <span style={{ fontSize: 10, color: 'var(--text-4)' }}>{note}</span>
    </div>
  );

  // A plain function, not a component. Declared as a component, this would be a
  // NEW component type on every render, so React would unmount and remount the
  // select each time anything else in the form changed, dropping its focus.
  const picker = (k, options) => (native ? (
    <select className="in" id={'pr-' + k} name={k} value={form[k]} onChange={set(k)}>
      <option value="">— Select —</option>
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  ) : (
    <Select value={form[k]} placeholder="— Select —" options={options} onChange={setSel(k)} />
  ));

  return (
    <>
      <div className="fs">
        <div className="fs-t"><Icon name="calendar" size={13} />Identification</div>
        <div className="fg c4">
          <div className="fd">{lab('paper_submission_date', 'Paper submission date', true)}<input className="in" id="pr-paper_submission_date" name="paper_submission_date" type="date" value={form.paper_submission_date} onChange={set('paper_submission_date')} /></div>
          <div className="fd">{lab('event_code', 'Event code', true)}
            {picker('event_code', EVENTS.map((e) => e.event_code))}
          </div>
          {ro('Speaker email ref', form.speaker_email_ref, 'Set when the notification sends')}
          {ro('Research email ref', form.research_email_ref, 'Set when the notification sends')}
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="users" size={13} />Speaker &amp; company</div>
        <div className="fg c4">
          <div className="fd">{lab('speaker_name', 'Speaker name', true)}<input className="in" id="pr-speaker_name" name="speaker_name" value={form.speaker_name} onChange={set('speaker_name')} /></div>
          <div className="fd">{lab('company_name', 'Company name', true)}<input className="in" id="pr-company_name" name="company_name" value={form.company_name} onChange={set('company_name')} /></div>
          <div className="fd">{lab('email', 'Email address of the speaker', true)}<input className="in" id="pr-email" name="email" type="email" value={form.email} onChange={set('email')} /></div>
          <div className="fd" style={{ gridColumn: '1/3' }}>{lab('linkedin_speaker', 'LinkedIn profile of speaker', true)}<input className="in" id="pr-linkedin_speaker" name="linkedin_speaker" type="url" placeholder="https://linkedin.com/in/…" value={form.linkedin_speaker} onChange={set('linkedin_speaker')} /></div>
          <div className="fd" style={{ gridColumn: '3/-1' }}>{lab('linkedin_company', 'LinkedIn company profile')}<input className="in" id="pr-linkedin_company" name="linkedin_company" type="url" placeholder="https://linkedin.com/company/…" value={form.linkedin_company} onChange={set('linkedin_company')} /></div>
          <div className="fd">{lab('linkedin_followers', 'LinkedIn followers count', true)}<NumField id="pr-linkedin_followers" name="linkedin_followers" min={0} value={form.linkedin_followers} onChange={set('linkedin_followers')} /></div>
          <div className="fd" style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingTop: 18 }}>
            <input type="checkbox" className="ck" id="pr-nos" name="nos" checked={!!form.nos} onChange={(e) => setForm((f) => ({ ...f, nos: e.target.checked, ...scoreReset(e.target.checked) }))} />
            <label className="fd-l" htmlFor="pr-nos" style={{ marginBottom: 0 }}>NOS?</label>
          </div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="star" size={13} />Review scoring</div>
        <div className="fg c4">
          {PAPER_REVIEW_CRITERIA.map((c) => (
            <div className="fd" key={c.key}>
              {lab(c.key, c.label + ' (' + c.max + ')', true)}
              {/* NumField, not a bare number input: min/max on an <input> are a
                  spinner hint, not a restriction, so every one of these boxes
                  accepted 99999 and the derived total read 364994 / 45. */}
              <NumField id={'pr-' + c.key} name={c.key} min={0} max={c.max} value={form[c.key]} onChange={set(c.key)} disabled={!!form.nos} />
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
          <div className="fd">{lab('session_location_on_agenda', 'Session or location on agenda', true)}
            {picker('session_location_on_agenda', PAPER_SESSION_OPTIONS)}
          </div>
          <div className="fd" style={{ gridColumn: '2/-1' }}>{lab('theme', 'Theme', true)}<input className="in" id="pr-theme" name="theme" value={form.theme} onChange={set('theme')} /></div>
          {/* MR-only. Always on the CRM form; on the public MRE form only when
              the link's reviewer may write it, which is what config's
              show_internal reports. Absent rather than disabled there, because a
              reviewer outside MR/Admin has the value refused on save. */}
          {showInternal ? (
            <div className="fd" style={{ gridColumn: '1/3' }}>{lab('internal_footnotes', 'Internal footnotes')}<input className="in" id="pr-internal_footnotes" name="internal_footnotes" value={form.internal_footnotes} onChange={set('internal_footnotes')} /></div>
          ) : null}
          <div className="fd" style={{ gridColumn: showInternal ? '3/-1' : '1/3' }}>{lab('feedback_to_speaker', 'Feedback to speaker or request information')}<input className="in" id="pr-feedback_to_speaker" name="feedback_to_speaker" value={form.feedback_to_speaker} onChange={set('feedback_to_speaker')} /></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="note" size={13} />Proposal received<span className="req">*</span></div>
        <div className="fg">
          {/* aria-label rather than a lab() line: these two fields are titled by
              their section header above, so a visible label would read the name
              twice. The attribute gives the same name to the accessibility tree
              that the header gives to the eye. */}
          <div className="fd full"><textarea className="in" id="pr-proposal_received" name="proposal_received" aria-label="Proposal received" style={{ minHeight: 140 }} placeholder="Proposed session title, talking points…" value={form.proposal_received} onChange={set('proposal_received')} /></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="edit" size={13} />Agenda addition<span className="req">*</span></div>
        <div className="fg">
          <div className="fd full"><textarea className="in" id="pr-agenda_addition" name="agenda_addition" aria-label="Agenda addition" style={{ minHeight: 140 }} placeholder="Agenda copy, industry tags…" value={form.agenda_addition} onChange={set('agenda_addition')} /></div>
        </div>
      </div>

    </>
  );
}
