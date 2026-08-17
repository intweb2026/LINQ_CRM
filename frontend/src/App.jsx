import { lazy } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { SessionProvider, useSession } from './context/SessionContext';
import { ToastProvider } from './context/ToastContext';
import { ConfirmProvider } from './context/ConfirmContext';
import AppShell from './components/AppShell';
import { homeFor } from './lib/nav';
import LoginPage from './pages/LoginPage';

/**
 * EVERY PAGE IS SPLIT OUT OF THE MAIN BUNDLE.
 *
 * These were static imports, so webpack emitted the entire application as ONE
 * chunk: 898 KB of JavaScript, which the browser had to download, parse and
 * execute in full before it could render anything at all. A user who only ever
 * opens Bookings still paid for the import wizard, the report builder, the
 * webhook log viewer and every modal in the app, on every cold load.
 *
 * lazy() makes each route its own chunk, fetched when it is first visited and
 * cached from then on. The initial download becomes the shell — router,
 * providers, AppShell, LoginPage — plus one page.
 *
 * LoginPage stays a STATIC import on purpose. It is the first thing an
 * unauthenticated visitor sees, so splitting it would add a round trip to
 * precisely the render that has nothing else to wait for.
 */
const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const BookingsPage = lazy(() => import('./pages/BookingsPage'));
const TicketCentralPage = lazy(() => import('./pages/TicketCentralPage'));
const EventsPage = lazy(() => import('./pages/EventsPage'));
const ReportsPage = lazy(() => import('./pages/ReportsPage'));
const EventPerformancePage = lazy(() => import('./pages/EventPerformancePage'));
const GoogleSyncPage = lazy(() => import('./pages/GoogleSyncPage'));
const UsersPage = lazy(() => import('./pages/UsersPage'));
const TeamPermissionsPage = lazy(() => import('./pages/TeamPermissionsPage'));
const TeamsManagementPage = lazy(() => import('./pages/TeamsManagementPage'));
const WebhookLogsPage = lazy(() => import('./pages/WebhookLogsPage'));
const PaperReviewPage = lazy(() => import('./pages/PaperReviewPage'));
const ProposalSubmissionPage = lazy(() => import('./pages/ProposalSubmissionPage'));

function RequireAuth({ children }) {
  const { user, permsLoaded } = useSession();
  if (!user) return <Navigate to="/login" replace />;
  // Wait for the permission matrix to resolve (login or page-refresh
  // rehydration) before rendering module-gated pages, so canView() checks
  // never see a false negative from a not-yet-loaded permission matrix.
  if (!permsLoaded) return null;
  return children;
}

function LoginRoute() {
  const { user } = useSession();
  // "/" rather than the landing page itself: HomeRedirect below decides that, and
  // it can only decide correctly once the permission matrix has loaded.
  if (user) return <Navigate to="/" replace />;
  return <LoginPage />;
}

/**
 * The index route — the page a session opens on.
 *
 * This renders INSIDE RequireAuth, which holds the tree until `permsLoaded`, so
 * homeFor() is guaranteed to read a resolved permission matrix. That is why
 * /login and the catch-alls send the user to "/" instead of computing a
 * destination themselves: at those points the matrix may still be in flight, and
 * a not-yet-loaded matrix denies every module — which would bounce a
 * Reports-capable user onto Dashboard on every cold page load.
 */
function HomeRedirect() {
  const { canView } = useSession();
  return <Navigate to={homeFor(canView).path} replace />;
}

export default function App() {
  return (
    <BrowserRouter>
      <SessionProvider>
        <ToastProvider>
          <ConfirmProvider>
            <Routes>
              <Route path="/login" element={<LoginRoute />} />
              {/* The Suspense boundary these lazy pages need is INSIDE AppShell,
                  around its <Outlet/>, not here. Wrapping <AppShell/> would put
                  the sidebar and topbar behind the fallback too, so every
                  navigation to a not-yet-fetched chunk would blank the whole
                  frame. See components/AppShell.jsx. */}
              <Route path="/" element={<RequireAuth><AppShell /></RequireAuth>}>
                <Route index element={<HomeRedirect />} />
                <Route path="dashboard" element={<DashboardPage />} />
                <Route path="bookings" element={<BookingsPage />} />
                <Route path="bookings/:tab" element={<BookingsPage />} />
                <Route path="tickets" element={<TicketCentralPage />} />
                <Route path="tickets/:tab" element={<TicketCentralPage />} />
                <Route path="paper-review" element={<PaperReviewPage />} />
                <Route path="proposal-submission" element={<ProposalSubmissionPage />} />
                <Route path="events" element={<EventsPage />} />
                <Route path="reports" element={<ReportsPage />} />
                <Route path="reports/:tab" element={<ReportsPage />} />
                <Route path="performance" element={<EventPerformancePage />} />
                <Route path="users" element={<UsersPage />} />
                <Route path="roles" element={<TeamPermissionsPage />} />
                <Route path="teams" element={<TeamsManagementPage />} />
                <Route path="webhooks" element={<WebhookLogsPage />} />
                <Route path="googlesync" element={<GoogleSyncPage />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Route>
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </ConfirmProvider>
        </ToastProvider>
      </SessionProvider>
    </BrowserRouter>
  );
}
