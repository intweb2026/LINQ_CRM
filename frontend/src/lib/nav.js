// Badge counts are no longer inline functions closing over seed data — see
// Sidebar.jsx, which fetches real counts and looks them up by nav id.
//
// The sidebar and the command palette both read this array, so an entry here is
// what makes a page reachable without typing its URL. `mod: null` marks a page
// that is not module-gated — Dashboard renders for every role and hides the
// sections a role cannot see, which is also why it is the landing page for
// everybody (see homeFor below).
//
// `id` is a badge/lookup key, NOT the route: 'paper_review' is underscored where
// its path is hyphenated. Anything deriving state from the current URL therefore
// matches on `path` — see AppShell and Sidebar.
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
    { id: 'dashboard', l: 'Dashboard', ic: 'grid', mod: null, path: '/dashboard' },
    { id: 'performance', l: 'Event Performance', ic: 'gauge', mod: 'performance', path: '/performance' },
  ] },
  { g: 'Admin', items: [
    { id: 'users', l: 'Users', ic: 'users', mod: 'users', path: '/users', hasBadge: true },
    { id: 'roles', l: 'Permissions', ic: 'shield', mod: 'roles', path: '/roles' },
    { id: 'teams', l: 'Teams Management', ic: 'team', mod: 'teams', path: '/teams' },
    { id: 'webhooks', l: 'Webhooks', ic: 'webhook', mod: 'webhooks', path: '/webhooks' },
    { id: 'googlesync', l: 'Google Sync', ic: 'refresh', mod: 'webhooks', path: '/googlesync' },
  ] },
];

export const NAV_FLAT = NAV.flatMap((g) => g.items);

/**
 * Where a session opens, and where every "go home" affordance points.
 *
 * Dashboard, unconditionally. It used to be Reports for anyone holding that
 * module and Dashboard for everyone else; with the Reports page gone there is
 * one destination, and it is the only page that is not module-gated —
 * DashboardPage renders for anybody and hides sections per permission — so this
 * can never resolve to a "No access" screen. The parameter is gone with it.
 *
 * Every caller that used to hardcode '/dashboard' (App routes, LoginPage, the
 * sidebar logo, NoAccessPage) still resolves through here, so the landing page
 * is decided in exactly one place.
 */
export function homeFor() {
  return { path: '/dashboard', label: 'Dashboard', ic: 'grid' };
}
