import { Icon } from '../lib/icons';
import { useSession } from '../context/SessionContext';
import { useConfirm } from '../context/ConfirmContext';
import Popover from './Popover';
import { THEMES, RAIL_POSITIONS } from '../lib/constants';

export default function Topbar({ crumb, theme, onPickTheme, railPos, onPickRailPos, onBurger, onOpenPalette }) {
  const { user, logout } = useSession();
  const confirm = useConfirm();
  // Falls back to the first entry rather than crashing on a theme that has
  // been dropped from THEMES while still sitting in localStorage.
  const current = THEMES.find((t) => t.id === theme) || THEMES[0];

  async function handleLogout() {
    const ok = await confirm({ title: 'Sign out?', ok: 'Sign out', body: <p>You can sign back in any time.</p> });
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
      {/* A labelled, boxy button. An icon alone could not say which of five
          themes was on, nor that a menu was behind it; the swatch answers the
          first and the caret the second. */}
      <Popover
        align="right"
        trigger={({ toggle, open }) => (
          <button className={'top-theme' + (open ? ' on' : '')}
            aria-label="Theme and layout" aria-haspopup="menu" onClick={toggle}>
            <i aria-hidden="true" style={{ background: current.dot }} />
            Theme
            {/* Inline rather than added to lib/icons: one caret, one caller. */}
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
              style={{ opacity: .55, flexShrink: 0 }}><path d="m6 9 6 6 6-6" /></svg>
          </button>
        )}
      >
        {({ close }) => (
          <>
            <div className="pop-t">Theme</div>
            {THEMES.map((t) => (
              <button key={t.id} type="button"
                className={'pop-i' + (t.id === theme ? ' cur' : '')}
                aria-current={t.id === theme}
                onClick={() => { onPickTheme(t.id); close(); }}>
                <span aria-hidden="true" style={{
                  width: 12, height: 12, flexShrink: 0, background: t.dot,
                  borderRadius: 'var(--r-tag)',
                  boxShadow: 'inset 0 0 0 1px rgba(0,0,0,.22)',
                }} />
                <span style={{ flex: 1, minWidth: 0 }}>
                  <b style={{ fontWeight: t.id === theme ? 700 : 550 }}>{t.label}</b>
                  <span style={{ display: 'block', fontSize: 10.5, color: 'var(--text-4)' }}>{t.note}</span>
                </span>
                {t.id === theme && <Icon name="check" size={14} />}
              </button>
            ))}
            {/* Left, top or bottom. The panel stays open on a placement change:
                it is the one setting people try all three of before settling,
                and closing after each would mean reopening the menu twice. */}
            <div className="pop-t" style={{ borderTop: '1px solid var(--border)', marginTop: 4, paddingTop: 9 }}>Sidebar</div>
            <div className="pop-seg" role="group" aria-label="Sidebar position">
              {RAIL_POSITIONS.map((r) => (
                <button key={r.id} type="button" aria-pressed={r.id === railPos}
                  onClick={() => onPickRailPos(r.id)}>{r.label}</button>
              ))}
            </div>
          </>
        )}
      </Popover>
      {/* NO AVATAR AND NO HANDLE. A coloured initials disc said nothing the
          name beside it did not, and the line under it was the login handle,
          "arthur.pina" where a person's name belongs. `user.name` is the
          `user.name` is the server's full name (accounts/models.py
          User.get_full_name), and it goes here whole: "Arthur Pina", not
          "Arthur" and not the handle.

          Sign out is a LABELLED, BOXY BUTTON rather than a bare icon. An
          unlabelled door glyph is the one control on this bar people cannot
          guess at, and it is the one with a consequence. */}
      <div className="top-me">
        <span className="top-me-n" title={user.name}>{user.name}</span>
        <button className="top-out" onClick={handleLogout}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M15 20H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9M16 16l4-4-4-4M20 12H9" /></svg>
          Sign out
        </button>
      </div>
    </header>
  );
}
