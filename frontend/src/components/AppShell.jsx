import { Suspense, useEffect, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import Sidebar from './Sidebar';
import Topbar from './Topbar';
import CommandPalette from './CommandPalette';
import { NAV, homeFor } from '../lib/nav';
import { useSession } from '../context/SessionContext';
import { useToast } from '../context/ToastContext';
import * as bookingsApi from '../api/bookings';
import { plur } from '../lib/helpers';

// 'paper-review' -> 'Paper Review'. The last-resort name for a shell route with no
// NAV entry. Every route has one today, so this is defensive: it keeps the next
// such page named after itself rather than inheriting whatever label a hardcoded
// fallback happens to carry.
const titleFromSegment = (s) => s.replace(/-/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

export default function AppShell() {
  const { canView, user, isAdmin } = useSession();
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

  // "/" reads as the landing page for the one render before the index route
  // redirects, so the crumb and tab title never flash a page the user is not on.
  const seg = loc.pathname.split('/')[1] || homeFor(canView, user?.username, isAdmin).path.slice(1);
  // Matched on `path`, not on `id`. The two are NOT interchangeable: 'paper_review'
  // is underscored where /paper-review is hyphenated, so an id comparison never
  // matched those entries and they fell through to the fallback below — which used
  // to be a hardcoded "Home / Dashboard", i.e. Paper Review and Proposal Submission
  // both announced themselves as the Dashboard in the breadcrumb and the tab title.
  // Now that Dashboard is a nav item of its own, that mislabel would point at a
  // real, visible page.
  let group = 'Home', label = titleFromSegment(seg);
  NAV.forEach((g) => g.items.forEach((it) => { if (it.path === '/' + seg) { group = g.g; label = it.l; } }));

  // #main is the scroller now (position:fixed with its own overflow-y, see
  // components.css), not the window — window.scrollTo alone left a page
  // opened mid-scroll however the previous one was left, because nothing
  // above ever asks the actual scroll container to reset.
  useEffect(() => {
    document.title = 'IQ-Hub — ' + label;
    window.scrollTo(0, 0);
    const main = document.getElementById('main');
    if (main) main.scrollTop = 0;
    setMobileOpen(false);
  }, [label]);

  return (
    <div id="app">
      <Sidebar collapsed={{ toggle: () => setCollapsedRail((v) => !v) }} mobileOpen={mobileOpen} onNavigate={() => setMobileOpen(false)} />
      <Topbar
        crumb={{ group, label }} theme={theme}
        onToggleTheme={() => setTheme((t) => (t === 'light' ? 'dark' : 'light'))}
        onBurger={() => setMobileOpen((v) => !v)}
        onOpenPalette={() => setPaletteOpen(true)}
      />
      {/* The route pages are lazy (see App.jsx), so the boundary belongs HERE
          rather than around <AppShell/> in the router. Above the shell, the
          fallback would replace the sidebar and topbar too, so every navigation
          to a not-yet-fetched chunk would blank the whole frame and rebuild it.
          Inside, only the content area waits, and the chrome the user is
          navigating with stays put.

          The fallback is empty on purpose: a chunk already in the browser cache
          resolves within a frame, and a spinner that appears and vanishes on
          every navigation reads as the app being slower than it is. */}
      <main id="main"><Suspense fallback={null}><Outlet /></Suspense></main>
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  );
}
