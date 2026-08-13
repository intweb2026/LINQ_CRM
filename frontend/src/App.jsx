import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { SessionProvider, useSession } from './context/SessionContext';
import { ToastProvider } from './context/ToastContext';
import { ConfirmProvider } from './context/ConfirmContext';
import AppShell from './components/AppShell';
import { homeFor } from './lib/nav';
import LoginPage from './pages/LoginPage';
import DashboardPage from './pages/DashboardPage';
import BookingsPage from './pages/BookingsPage';
import TicketCentralPage from './pages/TicketCentralPage';
import EventsPage from './pages/EventsPage';
import ReportsPage from './pages/ReportsPage';
import EventPerformancePage from './pages/EventPerformancePage';
import GoogleSyncPage from './pages/GoogleSyncPage';
import UsersPage from './pages/UsersPage';
import RolesPage from './pages/RolesPage';
import TeamsManagementPage from './pages/TeamsManagementPage';
import WebhookLogsPage from './pages/WebhookLogsPage';
import PaperReviewPage from './pages/PaperReviewPage';
import ProposalSubmissionPage from './pages/ProposalSubmissionPage';

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
                <Route path="roles" element={<RolesPage />} />
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
