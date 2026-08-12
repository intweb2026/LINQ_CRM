import { useLocation, useNavigate } from 'react-router-dom';
import { Icon } from '../lib/icons';
import { nf } from '../lib/helpers';
import { NAV } from '../lib/nav';
import * as bookingsApi from '../api/bookings';
import * as ticketsApi from '../api/tickets';
import * as eventsApi from '../api/events';
import * as usersApi from '../api/users';
import { useFetch } from '../hooks/useFetch';
import { useSession } from '../context/SessionContext';
import { useToast } from '../context/ToastContext';

export default function Sidebar({ collapsed, mobileOpen, onNavigate }) {
  const { canView } = useSession();
  const toast = useToast();
  const nav = useNavigate();
  const loc = useLocation();
  const activeId = loc.pathname.replace('/', '').split('/')[0] || 'dashboard';

  // The bookings badge is a COUNT — one row off the paginator, not every page of
  // ~35k delegates length-filtered in the browser. This component mounts in the
  // app shell, so the old version re-ran that walk on every route change.
  const { data: pendingBookings } = useFetch(bookingsApi.countPending, [], { initialData: 0 });
  const { data: ticketStats } = useFetch(ticketsApi.stats, [], { initialData: {} });
  const { data: events } = useFetch(eventsApi.list, [], { initialData: [] });
  const { data: users } = useFetch(usersApi.list, [], { initialData: [] });
  const badges = {
    bookings: pendingBookings || 0,
    tickets: (ticketStats || {}).mr_submitted || 0,
    events: (events || []).length,
    users: (users || []).length,
  };

  return (
    <nav className={'rail' + (mobileOpen ? ' open' : '')} aria-label="Main navigation">
      <div className="rail-head">
        <div className="rail-mark" role="button" tabIndex={0} title="iQ Hub — go to Dashboard" onClick={() => nav('/dashboard')}>
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
          const vis = g.items.filter((i) => !i.mod || canView(i.mod));
          const locked = g.items.filter((i) => i.mod && !canView(i.mod));
          if (!vis.length && !locked.length) return null;
          return (
            <div className="rail-group" key={g.g}>
              <div className="rail-glabel">{g.g}</div>
              {vis.map((i) => {
                const b = i.hasBadge ? badges[i.id] : null;
                return (
                  <button key={i.id} className={'rail-item' + (activeId === i.id ? ' on' : '')} onClick={() => { nav(i.path); onNavigate?.(); }}>
                    <span className="rail-ic"><Icon name={i.ic} size={17} /></span>
                    <span className="rail-lb">{i.l}</span>
                    {b ? <span className="rail-bdg">{b > 999 ? '999+' : b}</span> : null}
                    <span className="rail-tip">{i.l}{b ? ' · ' + nf(b) : ''}</span>
                  </button>
                );
              })}
              {locked.map((i) => (
                <button key={i.id} className="rail-item locked" title="No access" onClick={() => toast('You do not have access to ' + i.l, 'wn')}>
                  <span className="rail-ic"><Icon name={i.ic} size={17} /></span>
                  <span className="rail-lb">{i.l}</span>
                  <span className="rail-lock"><Icon name="lock" size={13} /></span>
                  <span className="rail-tip">{i.l} — no access</span>
                </button>
              ))}
            </div>
          );
        })}
      </div>
    </nav>
  );
}
