import { useEffect, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import Sidebar from './Sidebar';
import Topbar from './Topbar';
import CommandPalette from './CommandPalette';
import { NAV } from '../lib/nav';
import { useSession } from '../context/SessionContext';
import { useToast } from '../context/ToastContext';
import * as bookingsApi from '../api/bookings';
import { plur } from '../lib/helpers';

export default function AppShell() {
  const { canView } = useSession();
  const toast = useToast();
  const loc = useLocation();
  const [collapsedRail, setCollapsedRail] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [theme, setTheme] = useState(() => localStorage.getItem('iqhub_theme') || 'light');

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('iqhub_theme', theme);
  }, [theme]);

  useEffect(() => {
    document.body.classList.toggle('rail-min', collapsedRail);
  }, [collapsedRail]);

  useEffect(() => {
    const t = setTimeout(() => {
      if (!canView('bookings')) { toast('Welcome to IQ-Hub', 'nf', 3000); return; }
      // A count, not the rows: this used to walk every page of ~35k delegates
      // just to length-filter them for one number, on every app mount.
      bookingsApi.countPending().then((p) => {
        toast(p ? plur(p, 'booking') + ' still pending confirmation' : 'Welcome to IQ-Hub', p ? 'wn' : 'nf', p ? 5000 : 3000);
      }).catch(() => {});
    }, 700);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    function onKey(e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setPaletteOpen(true); return; }
      const t = document.activeElement && document.activeElement.tagName;
      if (e.key === '/' && t !== 'INPUT' && t !== 'TEXTAREA' && t !== 'SELECT') { e.preventDefault(); setPaletteOpen(true); }
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  const activeId = loc.pathname.replace('/', '').split('/')[0] || 'dashboard';
  let group = 'Home', label = 'Dashboard';
  NAV.forEach((g) => g.items.forEach((it) => { if (it.id === activeId) { group = g.g; label = it.l; } }));

  useEffect(() => { document.title = 'IQ-Hub — ' + label; window.scrollTo(0, 0); setMobileOpen(false); }, [label]);

  return (
    <div id="app">
      <Sidebar collapsed={{ toggle: () => setCollapsedRail((v) => !v) }} mobileOpen={mobileOpen} onNavigate={() => setMobileOpen(false)} />
      <Topbar
        crumb={{ group, label }} theme={theme}
        onToggleTheme={() => setTheme((t) => (t === 'light' ? 'dark' : 'light'))}
        onBurger={() => setMobileOpen((v) => !v)}
        onOpenPalette={() => setPaletteOpen(true)}
      />
      <main id="main"><Outlet /></main>
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  );
}
