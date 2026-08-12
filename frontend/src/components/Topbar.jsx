import { Icon } from '../lib/icons';
import { avc, ini } from '../lib/helpers';
import { useSession } from '../context/SessionContext';
import { useConfirm } from '../context/ConfirmContext';

export default function Topbar({ crumb, theme, onToggleTheme, onBurger, onOpenPalette }) {
  const { user, roleLabel, logout } = useSession();
  const confirm = useConfirm();

  async function handleLogout() {
    const ok = await confirm({ title: 'Sign out?', ok: 'Sign out', body: <p style={{ fontSize: 12.5, color: 'var(--text-3)' }}>You can sign back in any time.</p> });
    if (ok) logout();
  }

  return (
    <header className="top">
      <button className="top-burger" aria-label="Toggle navigation" onClick={onBurger}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round"><path d="M4 6h16M4 12h16M4 18h16" /></svg>
      </button>
      <nav className="top-crumb" aria-label="Breadcrumb">
        <span>{crumb.group}</span><span className="sp">›</span><span className="cur">{crumb.label}</span>
      </nav>
      <div className="top-sp" />
      <div className="top-search" role="button" tabIndex={0} aria-label="Open search" onClick={onOpenPalette}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpenPalette(); } }}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7.5" /><path d="m21 21-4.3-4.3" /></svg>
        <span>Search invoice, delegate, company…</span><kbd>⌘K</kbd>
      </div>
      <button className="top-btn" title="Toggle theme" aria-label="Toggle theme" onClick={onToggleTheme}>
        <Icon name={theme === 'light' ? 'moon' : 'sun'} size={17} />
      </button>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginLeft: 6 }}>
        <button className="rail-user" style={{ width: 'auto' }}>
          <span className="av av-sm" style={{ background: avc(user.name) }}>{ini(user.name)}</span>
          <span className="rail-utx"><b>{user.username}</b><span>{roleLabel}</span></span>
        </button>
        <button className="rail-out" title="Sign out" aria-label="Sign out" onClick={handleLogout}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round"><path d="M15 20H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9M16 16l4-4-4-4M20 12H9" /></svg>
        </button>
      </div>
    </header>
  );
}
