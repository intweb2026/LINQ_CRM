import { Suspense, useEffect, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import Sidebar from './Sidebar';
import Topbar from './Topbar';
import CommandPalette from './CommandPalette';
import IdleLogout from './IdleLogout';
import { homeFor, navEntryFor } from '../lib/nav';
import { isTheme, isRailPos } from '../lib/constants';
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
  // isTheme, not a bare read: the stored value is now one of six names
  // rather than a light/dark flag, and an unknown one — a theme renamed or
  // removed, a hand-edited key — would be written to html[data-theme] and
  // match no CSS block at all, leaving the app on the :root tokens with no
  // way for the user to tell why. Anything off the list falls back to light.
  const [theme, setTheme] = useState(() => {
    const stored = localStorage.getItem('iqhub_theme');
    return isTheme(stored) ? stored : 'light';
  });

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('iqhub_theme', theme);
  }, [theme]);

  // Where the rail sits: 'left' (the default), 'top' or 'bottom'. Same
  // storage-and-validate shape as the theme above, for the same reason — an
  // unknown value would put a class on <body> that no CSS matches and leave
  // the rail in a half-applied arrangement.
  const [railPos, setRailPos] = useState(() => {
    const stored = localStorage.getItem('iqhub_rail_pos');
    return isRailPos(stored) ? stored : 'left';
  });

  useEffect(() => {
    const horizontal = railPos !== 'left';
    // COLLAPSE ONLY APPLIES TO A VERTICAL RAIL. rail-min is what narrows the
    // rail to 62px and hides its labels; in a bar the labels are the only
    // thing identifying an item, so the two states would fight. Held here
    // rather than by disabling the button, so the user's collapse preference
    // survives a trip through top/bottom and back.
    document.body.classList.toggle('rail-min', collapsedRail && !horizontal);
    document.body.classList.toggle('rail-h', horizontal);
    document.body.classList.toggle('rail-top', railPos === 'top');
    document.body.classList.toggle('rail-bottom', railPos === 'bottom');
    localStorage.setItem('iqhub_rail_pos', railPos);
  }, [collapsedRail, railPos]);

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
  // Through navEntryFor, so the crumb and the rail can never disagree about
  // which page you are on. They did: both took the first path segment, so every
  // Credit Control sub-page announced itself as the Dashboard.
  const matched = navEntryFor(loc.pathname === '/' ? '/' + seg : loc.pathname);
  let group = 'Home', label = titleFromSegment(seg);
  if (matched) { group = matched.group; label = matched.item.l; }

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
        crumb={{ group, label }} theme={theme} onPickTheme={setTheme}
        railPos={railPos} onPickRailPos={setRailPos}
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
      {/* Six-hour inactivity sign-out. Mounted HERE rather than in App.jsx so
          it only ever runs for an authenticated session. */}
      <IdleLogout />
    </div>
  );
}
