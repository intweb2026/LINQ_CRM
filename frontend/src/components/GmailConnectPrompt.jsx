import { useCallback, useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import Modal from './Modal';
import { Icon } from '../lib/icons';
import { apiErrorMessage } from '../api/client';
import { gmailConnectUrl, gmailStatus } from '../api/gmail';
import { useSession } from '../context/SessionContext';
import { useToast } from '../context/ToastContext';

/**
 * The once-per-login "connect your Gmail" prompt, plus the report of how the
 * round trip through Google went.
 *
 * WHAT THIS DOES AND DOES NOT DO. It cannot be handed a token to save; only
 * Google can mint a Gmail credential, and only in exchange for a consent
 * click. So this asks for that click at the one moment the user is already
 * thinking about signing in, and the token lands in `gmail_accounts` through
 * the existing callback (see gmail_integration/views.py). Nothing is typed or
 * pasted, and there is nothing here for a user to copy from anywhere.
 *
 * ASKED ONCE PER SIGN-IN, NOT ONCE PER PAGE. The dismissal is kept in
 * sessionStorage keyed by user id, so declining silences it for the rest of
 * the session and the next sign-in asks again — which is the point, an
 * unconnected account is a feature that does not work yet. It is deliberately
 * NOT localStorage: that would make "Not now" a permanent answer to a question
 * whose answer changes.
 *
 * ONLY THE PEOPLE WHO NEED IT. Sending QR badges is Pre-Event Docs and QR
 * Attendance work, so nobody outside those modules is asked for access to
 * their mailbox.
 *
 * Mounted in AppShell, so it only ever runs inside an authenticated session.
 */
const DISMISS_KEY = (userId) => `gmail_prompt_dismissed_${userId}`;

function dismissed(userId) {
  try {
    return sessionStorage.getItem(DISMISS_KEY(userId)) === '1';
  } catch {
    // A browser with storage blocked would otherwise throw on every mount. The
    // cost of guessing "not dismissed" here is one extra prompt, so guess that.
    return false;
  }
}

function remember(userId) {
  try {
    sessionStorage.setItem(DISMISS_KEY(userId), '1');
  } catch {}
}

export default function GmailConnectPrompt() {
  const { user, canView, permsLoaded } = useSession();
  const toast = useToast();
  const loc = useLocation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const userId = user?.user_id ?? user?.id;
  const mayNeedIt = canView('pre_event_docs') || canView('attendance');

  const close = useCallback(() => {
    if (userId) remember(userId);
    setOpen(false);
  }, [userId]);

  // The outcome of a connection attempt, reported wherever the user came back
  // to. Read and then STRIPPED from the URL, so a refresh or a shared link does
  // not re-announce a connection that happened once.
  useEffect(() => {
    const params = new URLSearchParams(loc.search);
    const ok = params.get('gmail_connected');
    const failed = params.get('gmail_error');
    if (!ok && !failed) return;

    toast(
      ok ? 'Gmail connected. QR codes can now be emailed.'
         : 'Gmail could not be connected. Please try again.',
      ok ? 'ok' : 'er',
      5000,
    );
    if (userId) remember(userId);
    params.delete('gmail_connected');
    params.delete('gmail_error');
    const query = params.toString();
    navigate(loc.pathname + (query ? `?${query}` : ''), { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loc.search]);

  // permsLoaded gates the canView() call: asked before the matrix resolves it
  // answers false for everybody, and the prompt would never appear.
  useEffect(() => {
    if (!userId || !permsLoaded || !mayNeedIt || dismissed(userId)) return;
    let live = true;
    gmailStatus()
      // `can_connect` is false when the server has no usable Google OAuth
      // configuration. Asking then would offer a button whose only outcome is
      // Google's "Access blocked, this app's request is invalid" screen, which
      // the user can do nothing about, so the prompt stays away and the QR
      // modal reports it at the point somebody actually tries to send.
      .then((s) => { if (live && !s.connected && s.can_connect) setOpen(true); })
      // Silent on failure. This is an unprompted background check, and a person
      // signing in to do something else should not be shown an error about a
      // feature they have not asked for yet.
      .catch(() => {});
    return () => { live = false; };
  }, [userId, permsLoaded, mayNeedIt]);

  async function connect() {
    setBusy(true);
    setError('');
    try {
      // Comes back to the page the prompt was raised on, rather than to
      // Pre-Event Docs; the backend signs this path into the state parameter.
      const returnTo = loc.pathname + loc.search;
      const { authorize_url: url } = await gmailConnectUrl(returnTo);
      window.location.href = url;
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not start the Gmail connection.'));
      setBusy(false);
    }
  }

  if (!open) return null;

  return (
    <Modal
      size="sm" title="Connect your Gmail"
      sub="One time, so QR codes can be emailed from your own address"
      onClose={close}
      footer={<>
        <button className="btn btn-s" onClick={close} disabled={busy}>Not now</button>
        <button className="btn btn-p" onClick={connect} disabled={busy}>
          <Icon name="mail" size={15} />
          {busy ? 'Opening…' : 'Connect Gmail'}
        </button>
      </>}
    >
      <p>
        Check-in QR codes are emailed to attendees from the sender's own Gmail
        account, so yours has to be linked once before your first send. Google
        will ask you to approve it, then bring you straight back here.
      </p>
      <p className="qe-note">
        The CRM is granted permission to <b>send</b> mail as you, and nothing
        else. It cannot read, search or delete anything in your mailbox. You can
        revoke this at any time from your Google account.
      </p>
      {error ? <p className="qe-err">{error}</p> : null}
    </Modal>
  );
}
