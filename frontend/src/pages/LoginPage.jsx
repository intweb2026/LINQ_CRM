import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useSession } from '../context/SessionContext';
import { useToast } from '../context/ToastContext';

/**
 * Read via `process.env.<NAME>` member access, at module scope, one variable at
 * a time. react-scripts substitutes the literal text at BUILD time with
 * webpack's DefinePlugin; aliasing `process.env` first defeats that match and
 * leaves the value undefined in the browser. See src/api/client.js.
 *
 * This is NOT a Vite project, so there is no import.meta.env — the variable is
 * REACT_APP_GOOGLE_CLIENT_ID and it must be present in the BUILD environment,
 * not merely the runtime one.
 */
const CLIENT_ID = process.env.REACT_APP_GOOGLE_CLIENT_ID || '';

/**
 * Resolve once Google Identity Services has finished loading.
 *
 * The GIS <script> in public/index.html is `async defer`, so window.google is
 * routinely ABSENT when this page first mounts — /login is the very first route
 * rendered on a cold load, which is exactly when the race is worst. Bailing out
 * on a missing window.google would leave the user staring at an empty card with
 * no button and no error, so poll briefly instead and report a real failure if
 * the script never arrives (blocked by an extension, offline, corporate proxy).
 */
function whenGisReady(timeoutMs = 10000) {
  return new Promise((resolve, reject) => {
    if (window.google?.accounts?.id) return resolve(window.google);
    const started = Date.now();
    const timer = setInterval(() => {
      if (window.google?.accounts?.id) {
        clearInterval(timer);
        resolve(window.google);
      } else if (Date.now() - started > timeoutMs) {
        clearInterval(timer);
        reject(new Error('gis-unavailable'));
      }
    }, 100);
  });
}

export default function LoginPage() {
  const { loginWithGoogle } = useSession();
  const toast = useToast();
  const nav = useNavigate();
  const btnRef = useRef(null);
  const [status, setStatus] = useState(CLIENT_ID ? 'loading' : 'no-client-id');

  // Google holds the callback we hand it for the life of the page, so it must
  // not close over a stale render. A ref keeps one indirection to the current
  // handler and lets the effect below run exactly once.
  const handlerRef = useRef(null);
  handlerRef.current = async function handleCredentialResponse(response) {
    try {
      const user = await loginWithGoogle(response.credential);
      toast('Signed in as ' + user.name, 'ok');
      // "/" resolves to the landing page inside the router — the first entry in
      // NAV order this role can see; see HomeRedirect in App.jsx. Deciding it
      // here would read a `canView` captured BEFORE loginWithGoogle awaited and
      // replaced the permission matrix, which now decides the destination rather
      // than merely which sections it shows.
      //
      // replace: true because a signed-in user who presses Back onto /login is
      // only bounced forward again, which traps the button on the landing page.
      nav('/', { replace: true });
    } catch (err) {
      const msg =
        err?.response?.data?.detail ||
        'Sign in failed — your Google account may not have access.';
      toast(msg, 'er');
    }
  };

  useEffect(() => {
    if (!CLIENT_ID) return;
    let cancelled = false;

    whenGisReady()
      .then((google) => {
        if (cancelled || !btnRef.current) return;
        google.accounts.id.initialize({
          client_id: CLIENT_ID,
          callback: (response) => handlerRef.current(response),
          auto_select: false,
        });
        google.accounts.id.renderButton(btnRef.current, {
          theme: 'outline',
          size: 'large',
          width: 320,
          text: 'signin_with',
          shape: 'rectangular',
        });
        setStatus('ready');
      })
      .catch(() => {
        if (!cancelled) setStatus('gis-failed');
      });

    return () => { cancelled = true; };
  }, []);

  return (
    <div style={{
      display: 'flex',
      minHeight: '100vh',
      alignItems: 'center',
      justifyContent: 'center',
      padding: 24,
      background: 'var(--canvas, #f2f4f7)',
      fontFamily: 'var(--font, system-ui, sans-serif)',
    }}>
      <div style={{
        width: '100%',
        maxWidth: 420,
        padding: 40,
        borderRadius: 'var(--r-lg, 13px)',
        background: 'var(--surface, #fff)',
        border: '1px solid var(--border, #e1e5ea)',
        boxShadow: 'var(--sh-lg, 0 12px 28px -6px rgba(19,26,35,.1))',
        textAlign: 'center',
      }}>
        {/* ── Branding ─────────────────────────────────── */}
        {/*
          lg-mk-img / lg-light / lg-dark are the ONE exception to the
          inline-styles rule: the pair of logos is toggled by
          `html[data-theme=dark] .lg-mk-img.lg-light{display:none}` in
          src/styles/overlays.css. Both class names are required — the base
          .lg-mk-img rule is display:none, so .lg-light alone shows nothing and
          .lg-mk-img alone would show both logos at once.
        */}
        <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 10 }}>
          <img src="/static/logo-light.png" alt="iQ Hub"
            className="lg-mk-img lg-light" style={{ height: 40 }} />
          <img src="/static/logo-dark.webp" alt="iQ Hub"
            className="lg-mk-img lg-dark" style={{ height: 40 }} />
        </div>
        <div style={{
          fontSize: 9.5,
          fontWeight: 650,
          letterSpacing: '.1em',
          textTransform: 'uppercase',
          color: 'var(--text-4, #98a2b3)',
          marginBottom: 26,
        }}>
          CRM Workspace
        </div>

        <h1 style={{
          fontSize: 24,
          fontWeight: 800,
          letterSpacing: '-.04em',
          margin: '0 0 8px',
          color: 'var(--text, #212b36)',
        }}>
          Welcome back
        </h1>
        <p style={{
          fontSize: 13,
          color: 'var(--text-3, #667085)',
          margin: '0 0 30px',
          lineHeight: 1.55,
        }}>
          Sign in with your organisation Google account to reach bookings,
          tickets, events and the rest of the pipeline.
        </p>

        {/* ── Google button container ──────────────────── */}
        <div style={{ display: 'flex', justifyContent: 'center', minHeight: 44 }}>
          <div ref={btnRef} />
        </div>

        {status === 'loading' && (
          <p style={{ fontSize: 11.5, color: 'var(--text-4, #98a2b3)', margin: '14px 0 0' }}>
            Loading Google Sign-In…
          </p>
        )}

        {status === 'gis-failed' && (
          <p style={{ fontSize: 12, color: 'var(--red, #d5322f)', margin: '16px 0 0', lineHeight: 1.5 }}>
            Could not reach Google Sign-In. Check your connection or any
            extension blocking accounts.google.com, then reload.
          </p>
        )}

        {status === 'no-client-id' && (
          <p style={{ fontSize: 12, color: 'var(--red, #d5322f)', margin: '16px 0 0', lineHeight: 1.5 }}>
            Google Sign-In is not configured. Set REACT_APP_GOOGLE_CLIENT_ID in
            the build environment and rebuild.
          </p>
        )}
      </div>
    </div>
  );
}
