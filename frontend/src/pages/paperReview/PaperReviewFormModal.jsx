import { useState } from 'react';
import Modal from '../../components/Modal';
import { Icon } from '../../lib/icons';
import PaperReviewFields, { BLANK, buildPayload, firstMissing } from './PaperReviewFields';
import * as paperReviewApi from '../../api/paperReview';
import { apiErrorMessage } from '../../api/client';
import { useFetch } from '../../hooks/useFetch';
import { useToast } from '../../context/ToastContext';
import { useConfirm } from '../../context/ConfirmContext';

// The fields, the required-field checks, the payload shaping and the derived
// score/grade preview all live in PaperReviewFields, shared with the public MRE
// form page. This file is the CRM frame around them: permitted events, save,
// delete, toasts.

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

  async function save() {
    const missing = firstMissing(form);
    if (missing) { toast(missing, 'er'); return; }

    setSaving(true);
    const payload = buildPayload(form);
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
      <PaperReviewFields form={form} setForm={setForm} events={EVENTS} />
    </Modal>
  );
}
