import { useLocation, useNavigate } from 'react-router-dom';
import { Icon } from '../lib/icons';
import { nf } from '../lib/helpers';
import { NAV, homeFor } from '../lib/nav';
import * as bookingsApi from '../api/bookings';
import * as ticketsApi from '../api/tickets';
import * as eventsApi from '../api/events';
import * as usersApi from '../api/users';
import { useFetch } from '../hooks/useFetch';
import { useSession } from '../context/SessionContext';

export default function Sidebar({ collapsed, mobileOpen, onNavigate }) {
  const { canView } = useSession();
  const nav = useNavigate();
  const loc = useLocation();
  const home = homeFor(canView);
  // Compared against each item's `path`, not its `id` — see nav.js: 'paper_review'
  // is underscored where /paper-review is hyphenated, so the id comparison this
  // replaces left Paper Review and Proposal Submission permanently unhighlighted
  // while sitting on those very pages. "/" only exists for the instant before the
  // index route redirects, so it reads as the landing page.
  const activePath = '/' + (loc.pathname.split('/')[1] || home.path.slice(1));

  // The bookings badge is a COUNT — one row off the paginator, not every page of
  // ~35k delegates length-filtered in the browser. This component mounts in the
  // app shell, so the old version re-ran that walk on every route change.
  //
  // Each one is skipped unless its own item is rendered. Now that a module the user
  // cannot view is absent from the rail, an ungated fetch would be four requests
  // per mount asking for counts to put on rows that are not there, each answered
  // 403 by the very permission that hid the row.
  const { data: pendingBookings } = useFetch(bookingsApi.countPending, [], { initialData: 0, immediate: canView('bookings') });
  const { data: ticketStats } = useFetch(ticketsApi.stats, [], { initialData: {}, immediate: canView('ticket_central') });
  const { data: events } = useFetch(eventsApi.list, [], { initialData: [], immediate: canView('events') });
  const { data: users } = useFetch(usersApi.list, [], { initialData: [], immediate: canView('users') });
  const badges = {
    bookings: pendingBookings || 0,
    tickets: (ticketStats || {}).mr_submitted || 0,
    events: (events || []).length,
    users: (users || []).length,
  };

  return (
    <nav className={'rail' + (mobileOpen ? ' open' : '')} aria-label="Main navigation">
      <div className="rail-head">
        <div className="rail-mark" role="button" tabIndex={0} title={'iQ Hub — go to ' + home.label} onClick={() => nav(home.path)}>
          <img src="/static/logo-light.png" alt="iQ Hub" className="lg-light" />
          <img src="/static/logo-dark.webp" alt="iQ Hub" className="lg-dark" />
          <img src="/static/logo-icon.webp" alt="iQ Hub" className="lg-icon" />
        </div>
        <div className="rail-name"><span>CRM Workspace</span></div>
        <button className="rail-collapse" aria-label="Collapse navigation" title="Collapse" onClick={collapsed.toggle}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><path d="M15 6l-6 6 6 6" /></svg>
        </button>
      </div>
      <div className="rail-nav">
        {NAV.map((g) => {
          // A module the user cannot view is ABSENT, not shown greyed out behind a
          // padlock. The lock rendering this replaced listed every page in the
          // product to everybody, so a Sales user read eight rows they could never
          // open and a toast telling them so on each click — the sidebar answered
          // "what exists" when the only useful question is "where can I go". The
          // page guards are untouched: typing the URL still lands on NoAccessPage,
          // so this is the menu getting quieter, not the permission getting weaker.
          const vis = g.items.filter((i) => !i.mod || canView(i.mod));
          // The heading goes with them. An "Admin" label standing over nothing is
          // the same disclosure in smaller type.
          if (!vis.length) return null;
          return (
            <div className="rail-group" key={g.g}>
              <div className="rail-glabel">{g.g}</div>
              {vis.map((i) => {
                const b = i.hasBadge ? badges[i.id] : null;
                return (
                  <button key={i.id} className={'rail-item' + (activePath === i.path ? ' on' : '')} onClick={() => { nav(i.path); onNavigate?.(); }}>
                    <span className="rail-ic"><Icon name={i.ic} size={17} /></span>
                    <span className="rail-lb">{i.l}</span>
                    {b ? <span className="rail-bdg">{b > 999 ? '999+' : b}</span> : null}
                    <span className="rail-tip">{i.l}{b ? ' · ' + nf(b) : ''}</span>
                  </button>
                );
              })}
            </div>
          );
        })}
      </div>
    </nav>
  );
}
