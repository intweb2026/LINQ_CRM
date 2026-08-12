import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Icon } from '../lib/icons';
import { useSession } from '../context/SessionContext';
import { useToast } from '../context/ToastContext';
import * as authApi from '../api/auth';

export default function LoginPage() {
  const { login, loginWithCode } = useSession();
  const toast = useToast();
  const nav = useNavigate();
  const [mode, setMode] = useState('pw');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [email, setEmail] = useState('');
  const [otpSent, setOtpSent] = useState(false);
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);

  async function doLogin(loginMode) {
    if (busy) return;
    try {
      setBusy(true);
      let user;
      if (loginMode === 'pw') {
        if (!username.trim()) { toast('Enter a username', 'er'); return; }
        if (!password) { toast('Enter a password', 'er'); return; }
        user = await login({ username: username.trim(), password });
      } else {
        if (!code.trim()) { toast('Enter the code sent to your email', 'er'); return; }
        user = await loginWithCode(email.trim(), code.trim());
      }
      toast('Signed in as ' + user.name, 'ok');
      nav('/dashboard');
    } catch {
      toast('Sign in failed — check your details and try again', 'er');
    } finally {
      setBusy(false);
    }
  }

  async function sendCode() {
    if (!email.trim()) { toast('Enter your email first', 'er'); return; }
    try {
      await authApi.sendCode(email.trim());
      setOtpSent(true);
      toast('Code sent to ' + email, 'nf');
    } catch {
      toast('Could not send code — try again', 'er');
    }
  }

  return (
    <div className="lgn">
      <div className="lg-l">
        <div className="lg-mk">
          <img src="/static/logo-light.png" alt="iQ Hub" className="lg-mk-img lg-light" />
          <img src="/static/logo-dark.webp" alt="iQ Hub" className="lg-mk-img lg-dark" />
          <div><span>CRM Workspace</span></div>
        </div>
        <h1>Welcome back</h1>
        <p>Sign in to reach bookings, tickets, events and the rest of the pipeline.</p>
        <div className="lg-fm">
          <div className="lg-seg">
            <button className={mode === 'pw' ? 'on' : ''} onClick={() => setMode('pw')}>Password</button>
            <button className={mode === 'otp' ? 'on' : ''} onClick={() => setMode('otp')}>Email code</button>
          </div>
          {mode === 'pw' ? (
            <>
              <div className="fd" style={{ marginBottom: 12 }}>
                <label className="fd-l">Username</label>
                <input className="in" placeholder="e.g. HP" autoComplete="username" value={username}
                  onChange={(e) => setUsername(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && doLogin('pw')} />
              </div>
              <div className="fd">
                <label className="fd-l">Password</label>
                <input className="in" type="password" placeholder="••••••••" autoComplete="current-password" value={password}
                  onChange={(e) => setPassword(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && doLogin('pw')} />
              </div>
            </>
          ) : (
            <>
              <div className="fd" style={{ marginBottom: 12 }}>
                <label className="fd-l">Work email</label>
                <input className="in" placeholder="you@iq-hub.com" value={email} onChange={(e) => setEmail(e.target.value)} />
              </div>
              <button className="btn btn-s" style={{ alignSelf: 'flex-start' }} onClick={sendCode}><Icon name="mail" size={14} />Send code</button>
              {otpSent && (
                <div className="fd" style={{ marginTop: 12 }}>
                  <label className="fd-l">6-digit code</label>
                  <input className="in" placeholder="••••••" maxLength={6} value={code} onChange={(e) => setCode(e.target.value)} />
                </div>
              )}
            </>
          )}
          <button className="btn btn-p" style={{ height: 38, marginTop: 2 }} disabled={busy} onClick={() => doLogin(mode)}><Icon name="lock" size={15} />{busy ? 'Signing in…' : 'Sign in'}</button>
        </div>
      </div>
      <div className="lg-r">
        <h2>One workspace for the whole event pipeline — delegates, sponsors, speakers, and the teams behind them.</h2>
        <div className="lg-ft">
          <div><span className="i"><Icon name="receipt" size={15} /></span><div><b>Bookings, unified</b><span>Delegate, sponsor and speaker pipelines in one table.</span></div></div>
          <div><span className="i"><Icon name="ticket" size={15} /></span><div><b>Research to mining, tracked</b><span>Every ticket moves through MR → DMD with a full trail.</span></div></div>
          <div><span className="i"><Icon name="shield" size={15} /></span><div><b>Permissioned by design</b><span>View, create, update and delete controlled per module.</span></div></div>
        </div>
      </div>
    </div>
  );
}
