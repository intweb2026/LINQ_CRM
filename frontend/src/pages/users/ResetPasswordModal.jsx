import { useState } from 'react';
import Modal from '../../components/Modal';
import { Icon } from '../../lib/icons';
import { useToast } from '../../context/ToastContext';
import { apiErrorMessage } from '../../api/client';
import * as usersApi from '../../api/users';

/**
 * Set a user's password directly.
 *
 * The drawer button used to announce "Password reset link sent to …" off a bare
 * toast — no request was made and no email exists to send one. This calls the
 * endpoint that is actually there (PATCH users/{id}/reset-password/), which sets
 * the password rather than mailing a link, so the copy says so: whoever does
 * this has to pass the new password on themselves.
 */
export default function ResetPasswordModal({ user: u, onClose }) {
  const toast = useToast();
  const [pw, setPw] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);

  async function save() {
    if (pw.length < 8) { toast('Password must be at least 8 characters', 'er'); return; }
    if (pw !== confirm) { toast('The two passwords do not match', 'er'); return; }
    setBusy(true);
    try {
      await usersApi.resetPassword(u.id, pw);
      onClose();
      toast('Password reset for ' + u.name, 'ok');
    } catch (err) {
      toast(apiErrorMessage(err, 'Could not reset the password.'), 'er');
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal size="sm" title="Reset password" sub={u.name + ' · @' + u.username} onClose={onClose}
      footer={<>
        <button className="btn btn-s" onClick={onClose} disabled={busy}>Cancel</button>
        <button className="btn btn-p" onClick={save} disabled={busy}><Icon name="key" size={15} />{busy ? 'Saving…' : 'Set password'}</button>
      </>}>
      <div className="fd" style={{ marginBottom: 12 }}>
        <label className="fd-l">New password<span className="req">*</span></label>
        <input className="in" type="password" value={pw} onChange={(e) => setPw(e.target.value)} placeholder="8 characters minimum" autoComplete="new-password" />
      </div>
      <div className="fd">
        <label className="fd-l">Confirm password<span className="req">*</span></label>
        <input className="in" type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" />
      </div>
      <p style={{ fontSize: 11.5, color: 'var(--text-4)', marginTop: 12, lineHeight: 1.5 }}>
        No email is sent — pass the new password to {u.name} yourself. Day-to-day sign-in uses an emailed one-time code, so most accounts never need one.
      </p>
    </Modal>
  );
}
