import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Icon } from '../lib/icons';
import PaperReviewFields, { BLANK, buildPayload, firstMissing } from './paperReview/PaperReviewFields';
import * as formApi from '../api/paperReviewForm';
import { apiErrorMessage } from '../api/client';

/**
 * The MRE paper review form, opened from a personal link with no CRM login.
 *
 * Replaces the Zoho public form URL: the reviewer opens
 * /paper-review/submit?crm_key=… , fills in the same fields the CRM form shows,
 * and their submission runs both ADD workflows — see
 * backend/paper_review/public_form.py.
 *
 * OUTSIDE RequireAuth in App.jsx, deliberately, and it must stay there. It also
 * renders its own frame rather than AppShell's: the reviewer has no session, so
 * a sidebar of modules they cannot open, and a topbar naming a user who is not
 * signed in, would both be furniture describing a CRM they have no access to.
 *
 * No useSession, no useToast, no useConfirm. Every message this page has to give
 * is about the one thing on screen, so it says it in the page rather than in a
 * toast that can be missed, and it holds no dependency on providers that assume
 * a logged-in user.
 */
export default function PublicPaperReviewFormPage() {
  const [params] = useSearchParams();
  const key = params.get(formApi.KEY_PARAM) || '';

  const [state, setState] = useState({ status: 'loading' });
  const [form, setForm] = useState({ ...BLANK });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState(null);

  useEffect(() => {
    if (!key) {
      setState({ status: 'invalid', message: 'This link is incomplete. Ask for the form link again.' });
      return;
    }
    let live = true;
    formApi.config(key)
      .then((data) => { if (live) setState({ status: 'ready', data }); })
      .catch((err) => {
        if (live) setState({
          status: 'invalid',
          message: apiErrorMessage(err, 'This form link is not valid. Ask for a new one.'),
        });
      });
    return () => { live = false; };
  }, [key]);

  async function submit() {
    const missing = firstMissing(form);
    if (missing) { setError(missing); return; }
    setError('');
    setSaving(true);
    try {
      const result = await formApi.submit(key, buildPayload(form));
      setDone(result);
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not submit — check the form and try again'));
      setSaving(false);
      return;
    }
    setSaving(false);
  }

  const page = {
    minHeight: '100vh', background: 'var(--bg, #f6f7f9)',
    padding: '28px 18px 60px',
  };
  const card = {
    maxWidth: 1080, margin: '0 auto', background: 'var(--surface-1, #fff)',
    border: '1px solid var(--border, #e3e6ea)', borderRadius: 10, padding: 22,
  };

  if (state.status === 'loading') {
    return <div style={page}><div style={card}>Loading the form…</div></div>;
  }

  if (state.status === 'invalid') {
    return (
      <div style={page}>
        <div style={card}>
          <h1 style={{ fontSize: 18, margin: '0 0 8px' }}>Paper review form</h1>
          <p style={{ fontSize: 13, color: 'var(--text-3)', margin: 0 }}>{state.message}</p>
        </div>
      </div>
    );
  }

  // The receipt. It names the review id and the proposal submission id, so a
  // "did it go through?" question has something to quote, and it offers another
  // blank form rather than leaving the reviewer to reload the link by hand.
  if (done) {
    return (
      <div style={page}>
        <div style={card}>
          <h1 style={{ fontSize: 18, margin: '0 0 10px' }}>
            <Icon name="check" size={16} /> Review submitted
          </h1>
          <p style={{ fontSize: 13, color: 'var(--text-2)', margin: '0 0 6px' }}>
            {done.speaker_name} · {done.event_code} · {done.proposal_score} of {state.data.rubric_total}, grade {done.grade || '—'}
          </p>
          <p style={{ fontSize: 12, color: 'var(--text-3)', margin: '0 0 18px' }}>
            Reference: review #{done.id}, proposal submission #{done.proposal_submission?.id}.
            The production team has been notified.
          </p>
          <button className="btn btn-p" onClick={() => { setDone(null); setForm({ ...BLANK }); }}>
            <Icon name="plus" size={14} />Submit another review
          </button>
        </div>
      </div>
    );
  }

  const { data } = state;
  return (
    <div style={page}>
      <div style={card}>
        <h1 style={{ fontSize: 18, margin: '0 0 4px' }}>Paper review</h1>
        <p style={{ fontSize: 12.5, color: 'var(--text-3)', margin: '0 0 18px' }}>
          {data.reviewer} · {data.events.length === 1
            ? data.events[0].event_code
            : `${data.events.length} events`}
        </p>

        {/* showInternal={false}: internal_footnotes is MR-internal and the public
            serializer does not accept it. See PaperReviewFields. */}
        <PaperReviewFields form={form} setForm={setForm} events={data.events} showInternal={false} />

        {error ? (
          <p style={{ fontSize: 12.5, color: 'var(--red)', margin: '14px 0 0' }}>{error}</p>
        ) : null}

        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 18 }}>
          <button className="btn btn-p" disabled={saving} onClick={submit}>
            <Icon name="check" size={15} />{saving ? 'Submitting…' : 'Submit review'}
          </button>
        </div>
      </div>
    </div>
  );
}
