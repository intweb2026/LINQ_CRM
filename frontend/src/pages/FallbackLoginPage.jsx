import { useState, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useSession } from '../context/SessionContext';
import { useToast } from '../context/ToastContext';

export default function FallbackLoginPage() {
  const { user, loginWithFallback } = useSession();
  const toast = useToast();
  const nav = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);

  // ── Gate check ────────────────────────────────────────────────────
  // If someone types /loginpage directly in the browser (no gate flag
  // in location.state), redirect them to the normal Google login page.
  const gateOk = location.state?.gate === true;

  useEffect(() => {
    if (!gateOk) nav('/login', { replace: true });
  }, [gateOk, nav]);

  // If already signed in, go home.
  useEffect(() => {
    if (user) nav('/', { replace: true });
  }, [user, nav]);

  if (!gateOk) return null; // will redirect on next tick

  // ── Submit ────────────────────────────────────────────────────────
  async function handleSubmit(e) {
    e.preventDefault();
    if (!username.trim()) { toast('Enter a username', 'er'); return; }
    if (!password) { toast('Enter a password', 'er'); return; }
    try {
      setBusy(true);
      const u = await loginWithFallback({
        username: username.trim(),
        password,
      });
      toast('Signed in as ' + (u.name || u.username), 'ok');
      nav('/', { replace: true });
    } catch {
      toast('Sign in failed — check your credentials', 'er');
    } finally {
      setBusy(false);
    }
  }

  // ── Styles ────────────────────────────────────────────────────────
  const pageStyle = {
    display: 'flex',
    minHeight: '100vh',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'var(--bg-primary, #f5f5f5)',
  };

  const cardStyle = {
    width: '100%',
    maxWidth: 400,
    padding: 36,
    borderRadius: 12,
    background: 'var(--bg-card, #fff)',
    boxShadow: '0 2px 16px rgba(0,0,0,0.08)',
  };

  const headerStyle = {
    textAlign: 'center',
    marginBottom: 24,
  };

  const subtitleStyle = {
    fontSize: 12,
    fontWeight: 600,
    letterSpacing: 1.5,
    textTransform: 'uppercase',
    color: 'var(--text-secondary, #888)',
    marginBottom: 8,
  };

  const titleStyle = {
    fontSize: 20,
    fontWeight: 700,
    margin: '0 0 4px',
    color: 'var(--text-primary, #1a1a1a)',
  };

  const hintStyle = {
    fontSize: 13,
    color: 'var(--text-secondary, #888)',
    margin: 0,
  };

  const labelStyle = {
    display: 'block',
    fontSize: 13,
    fontWeight: 600,
    marginBottom: 6,
    color: 'var(--text-secondary, #555)',
  };

  const inputStyle = {
    width: '100%',
    padding: '10px 12px',
    fontSize: 14,
    border: '1px solid var(--border, #ccc)',
    borderRadius: 8,
    marginBottom: 16,
    boxSizing: 'border-box',
    background: 'var(--bg-input, #fff)',
    color: 'var(--text-primary, #1a1a1a)',
  };

  const btnStyle = {
    width: '100%',
    padding: '12px 0',
    fontSize: 15,
    fontWeight: 600,
    border: 'none',
    borderRadius: 8,
    cursor: busy ? 'not-allowed' : 'pointer',
    opacity: busy ? 0.6 : 1,
    background: 'var(--accent, #0066cc)',
    color: '#fff',
  };

  // ── Render ────────────────────────────────────────────────────────
  return (
    <div style={pageStyle}>
      <div style={cardStyle}>
        <div style={headerStyle}>
          <div style={subtitleStyle}>IQ-Hub CRM</div>
          <h1 style={titleStyle}>Emergency Sign In</h1>
          <p style={hintStyle}>
            Use this only when Google Sign-In is unavailable.
          </p>
        </div>

        <form onSubmit={handleSubmit}>
          <label style={labelStyle}>Username</label>
          <input
            style={inputStyle}
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
          />

          <label style={labelStyle}>Password</label>
          <input
            style={inputStyle}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />

          <button type="submit" style={btnStyle} disabled={busy}>
            {busy ? 'Signing in…' : 'Sign In'}
          </button>
        </form>
      </div>
    </div>
  );
}
