// Badge counts are no longer inline functions closing over seed data — see
// Sidebar.jsx, which fetches real counts and looks them up by nav id.
// Dashboard is intentionally ABSENT from this list. There is no backend model
// behind it — Reports (mod 'reports', /reports) is the real reporting surface —
// so it is not offered in the sidebar or the command palette (both read this
// array). The /dashboard route itself still exists in App.jsx and stays the
// post-login landing page; AppShell falls back to the "Home / Dashboard"
// breadcrumb for it precisely because no NAV entry matches.
export const NAV = [
  { g: 'Pipeline', items: [
    { id: 'bookings', l: 'Bookings', ic: 'receipt', mod: 'bookings', path: '/bookings', hasBadge: true },
    { id: 'tickets', l: 'Ticket Central', ic: 'ticket', mod: 'ticket_central', path: '/tickets', hasBadge: true },
    { id: 'paper_review', l: 'Paper Review', ic: 'sheet', mod: 'paper_review', path: '/paper-review' },
    { id: 'proposal_submission', l: 'Proposal Submission', ic: 'upload', mod: 'proposal_submission', path: '/proposal-submission' },
  ] },
  { g: 'Catalogue', items: [
    { id: 'events', l: 'Events', ic: 'calendar', mod: 'events', path: '/events', hasBadge: true },
  ] },
  { g: 'Insights', items: [
    { id: 'reports', l: 'Reports', ic: 'chart', mod: 'reports', path: '/reports' },
    { id: 'performance', l: 'Event Performance', ic: 'gauge', mod: 'performance', path: '/performance' },
    { id: 'myteam', l: 'My Team', ic: 'users', mod: null, path: '/myteam' },
  ] },
  { g: 'Admin', items: [
    { id: 'users', l: 'Users', ic: 'users', mod: 'users', path: '/users', hasBadge: true },
    { id: 'roles', l: 'Roles', ic: 'shield', mod: 'roles', path: '/roles' },
    { id: 'teams', l: 'Teams Management', ic: 'team', mod: 'teams', path: '/teams' },
    { id: 'webhooks', l: 'Webhooks', ic: 'webhook', mod: 'webhooks', path: '/webhooks' },
    { id: 'googlesync', l: 'Google Sync', ic: 'refresh', mod: 'webhooks', path: '/googlesync' },
  ] },
];

export const NAV_FLAT = NAV.flatMap((g) => g.items);
