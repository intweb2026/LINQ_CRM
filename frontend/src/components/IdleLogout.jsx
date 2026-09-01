import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import Modal from './Modal';
import { useSession } from '../context/SessionContext';
import { IDLE_LIMIT_MS, idlePhase, readLastActive, stampActive } from '../lib/idle';

/**
 * What counts as "the user is still there".
 *
 * `scroll` is registered with capture:true because scroll does not bubble and
 * the app's scroller is #main, not the window (see AppShell) — without capture
 * a user reading a long table by scrolling would look idle. `focus` likewise:
 * it does not bubble, and returning to the tab is activity.
 */
const ACTIVITY_EVENTS = ['mousemove', 'mousedown', 'click', 'keydown', 'wheel', 'scroll', 'touchstart', 'focus'];

// One localStorage write per 30s of continuous activity, instead of one per
// mousemove event. localStorage is synchronous and mousemove fires at frame
// rate, so the unthrottled version would be a write on the main thread every
// few milliseconds while the mouse is moving.
const STAMP_THROTTLE_MS = 30 * 1000;

// The countdown in the warning needs second resolution; outside the warning this
// is one localStorage read per second, which costs nothing measurable.
const CHECK_EVERY_MS = 1000;

const mmss = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;

/**
 * Automatic sign-out after six hours of no user activity.
 *
 * Mounted by AppShell, which means it exists ONLY inside the authenticated
 * shell: the login page runs no timer, and there is nothing to tear down for a
 * signed-out visitor.
 *
 * The clock itself is in lib/idle.js — read the note there for why this is a
 * timestamp comparison on an interval rather than one long setTimeout.
 */
export default function IdleLogout() {
  const { logout } = useSession();
  const loc = useLocation();
  // Milliseconds left, while the warning is up; null the rest of the time. Only
  // this component re-renders, and only during the final two minutes.
  const [msLeft, setMsLeft] = useState(null);
  const lastStamp = useRef(0);
  const signingOut = useRef(false);

  const keepAlive = useCallback(() => {
    lastStamp.current = Date.now();
    stampActive(lastStamp.current);
    setMsLeft(null);
  }, []);

  useEffect(() => {
    // Mounting IS activity: it follows a login, a refresh or a navigation. It
    // also overwrites any stamp left behind by a previous session on this
    // browser, which would otherwise expire this one on its first tick.
    keepAlive();

    function onActivity() {
      if (Date.now() - lastStamp.current >= STAMP_THROTTLE_MS) keepAlive();
    }

    function check() {
      if (signingOut.current) return;
      const last = readLastActive();
      // Unreadable stamp (private-mode storage, a tab that cleared it): treat
      // the session as active and re-stamp. Failing open here is deliberate —
      // a storage quirk must not sign a working user out.
      if (last === null) { keepAlive(); return; }

      const now = Date.now();
      const phase = idlePhase(last, now);
      if (phase === 'expired') {
        signingOut.current = true;
        // A full document load, not a router redirect: it discards the token,
        // the permission matrix AND every page's in-memory copy of CRM rows in
        // one step, which is the point of an idle logout on a shared desk.
        logout().finally(() => window.location.replace('/login'));
        return;
      }
      setMsLeft(phase === 'warn' ? last + IDLE_LIMIT_MS - now : null);
    }

    /**
     * Another tab signed out, so this one is already dead.
     *
     * DRF keeps one token per user, so the key that tab just revoked was this
     * tab's credential too. Without this, the shell here keeps rendering — menus,
     * tables, a working-looking Save button — until the next request happens to
     * 401, which on a quiet page can be minutes. Following the other tab is both
     * the honest thing to show and the safe one. `storage` never fires in the tab
     * that did the writing, so this cannot re-enter.
     *
     * Covers the 401 interceptor's own token removal as well, and someone
     * clearing site data by hand.
     */
    function onStorage(e) {
      if (e.key === 'auth_token' && e.newValue === null) {
        signingOut.current = true;
        window.location.replace('/login');
      }
    }

    const opts = { passive: true, capture: true };
    ACTIVITY_EVENTS.forEach((e) => window.addEventListener(e, onActivity, opts));
    window.addEventListener('storage', onStorage);
    // A tab that was hidden had its interval throttled to roughly once a
    // minute, so check the moment it comes back rather than waiting for the
    // next throttled tick.
    document.addEventListener('visibilitychange', check);
    const timer = setInterval(check, CHECK_EVERY_MS);

    return () => {
      ACTIVITY_EVENTS.forEach((e) => window.removeEventListener(e, onActivity, opts));
      window.removeEventListener('storage', onStorage);
      document.removeEventListener('visibilitychange', check);
      clearInterval(timer);
    };
  }, [keepAlive, logout]);

  // Navigation in its own right, because a route change driven by code — the
  // redirect after a save, the command palette jumping to a record — fires none
  // of the DOM events above.
  useEffect(() => { keepAlive(); }, [loc.pathname, keepAlive]);

  if (msLeft === null) return null;

  return (
    <Modal
      size="sm"
      title="Are you still there?"
      sub="You have been inactive for a while"
      onClose={keepAlive}
      footer={<button className="btn btn-p" onClick={keepAlive}>Stay signed in</button>}
    >
      <p style={{ fontSize: 12.5, color: 'var(--text-3)', margin: 0 }}>
        For security you will be signed out in <b>{mmss(Math.max(0, Math.ceil(msLeft / 1000)))}</b>.
        Anything you have typed but not saved will be lost, so save your work now, or
        move the mouse to carry on where you left off.
      </p>
    </Modal>
  );
}
