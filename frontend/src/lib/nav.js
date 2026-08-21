// Badge counts are no longer inline functions closing over seed data — see
// Sidebar.jsx, which fetches real counts and looks them up by nav id.
//
// The sidebar and the command palette both read this array, so an entry here is
// what makes a page reachable without typing its URL.
//
// Two kinds of gate. `mod` names the ONE module a page needs, and is the normal
// case. `needsAny` names several and passes if the role holds any of them: it
// exists for Dashboard, which has no module of its own because it renders no
// data of its own — every panel on it is an aggregate over another module's
// tables and is already hidden per permission inside the page. So the question
// "may this role see Dashboard" is really "is there anything left on it", and
// the answer is yes exactly when one of DASH_MODULES is viewable. A role holding
// only Users/Teams/Permissions would otherwise land on a page of empty panels.
//
// An entry must set one or the other, never both. `mod: null` with no `needsAny`
// would mean ungated, which nothing is any more.
//
// `id` is a badge/lookup key, NOT the route: 'paper_review' is underscored where
// its path is hyphenated. Anything deriving state from the current URL therefore
// matches on `path` — see AppShell and Sidebar.
/**
 * The modules Dashboard actually renders a section for.
 *
 * Read off the canView() calls in DashboardPage: the action queue, the headline
 * stats, the charts and the recent-activity panels between them cover these four
 * and nothing else. 'performance' is deliberately absent — Event Performance is
 * its own page and contributes no panel here, so holding it alone would light up
 * a Dashboard with everything hidden.
 *
 * Exported because DashboardPage's own guard reads the same list. One definition,
 * so the rail and the page can never disagree about whether the page has content.
 */
export const DASH_MODULES = ['bookings', 'ticket_central', 'events', 'webhooks'];

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
    { id: 'dashboard', l: 'Dashboard', ic: 'grid', needsAny: DASH_MODULES, path: '/dashboard' },
    { id: 'performance', l: 'Event Performance', ic: 'gauge', mod: 'performance', path: '/performance' },
  ] },
  { g: 'Admin', items: [
    { id: 'users', l: 'Users', ic: 'users', mod: 'users', path: '/users', hasBadge: true },
    { id: 'roles', l: 'Permissions', ic: 'shield', mod: 'roles', path: '/roles' },
    { id: 'teams', l: 'Teams Management', ic: 'team', mod: 'teams', path: '/teams' },
    { id: 'webhooks', l: 'Webhooks', ic: 'webhook', mod: 'webhooks', path: '/webhooks' },
    // 'google_sync', not 'webhooks'. The backend split the two apart (see
    // CRM_MODULES in backend/accounts/models.py, backfilled by migration 0027)
    // and google_sync/views.py enforces the split module on every endpoint. This
    // entry kept pointing at the old shared key, so the rail showed Google Sync
    // to a webhooks-only role whose every request then 403'd, and hid it from a
    // role actually granted google_sync.
    { id: 'googlesync', l: 'Google Sync', ic: 'refresh', mod: 'google_sync', path: '/googlesync' },
  ] },
];

export const NAV_FLAT = NAV.flatMap((g) => g.items);

/**
 * The one answer to "may this role reach this page", for both gate shapes.
 *
 * The sidebar, the command palette and homeFor() all asked this question, and
 * before `needsAny` existed they each asked it with their own inline
 * `!i.mod || canView(i.mod)`. Three copies of a rule about who sees what is
 * three places for a module to stay visible after being revoked, so it lives
 * here instead.
 */
export function canAccess(item, canView) {
  if (item.needsAny) return item.needsAny.some((m) => canView(m));
  return !item.mod || canView(item.mod);
}

/**
 * Where a session opens, and where every "go home" affordance points.
 *
 * The first entry in NAV order the role can actually see. This was Dashboard
 * unconditionally, which was safe while Dashboard was ungated; now that it is
 * hidden from a role holding none of DASH_MODULES, a fixed destination would
 * drop exactly those roles onto a "No access" screen on login.
 *
 * NAV order is the priority order, so this is decided by where an entry sits in
 * the array above rather than by a second list that could drift from it.
 * Bookings first, Dashboard mid-table: a delegate-desk role lands on its work,
 * and a role that holds Dashboard but no Pipeline module still gets it.
 *
 * `canView` is passed in rather than imported because this module must stay free
 * of React — the command palette, the routes and the shell all read NAV outside
 * a component. Callers already hold it off useSession().
 *
 * The fallback is only reachable by a role that can view NOTHING, which is
 * already its own screen: /dashboard renders NoAccessPage there, so the button
 * on it does not point back at itself.
 *
 * Every caller that used to hardcode '/dashboard' (App routes, LoginPage, the
 * sidebar logo, NoAccessPage) still resolves through here, so the landing page
 * is decided in exactly one place.
 */
export function homeFor(canView) {
  const first = NAV_FLAT.find((i) => canAccess(i, canView));
  return first
    ? { path: first.path, label: first.l, ic: first.ic }
    : { path: '/dashboard', label: 'Dashboard', ic: 'grid' };
}
