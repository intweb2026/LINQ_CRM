import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { SessionProvider, useSession } from './context/SessionContext';
import { ToastProvider } from './context/ToastContext';
import { ConfirmProvider } from './context/ConfirmContext';
import AppShell from './components/AppShell';
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
import TeamPage from './pages/TeamPage';
import WebhookLogsPage from './pages/WebhookLogsPage';
import CompaniesPage from './pages/CompaniesPage';
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
  if (user) return <Navigate to="/dashboard" replace />;
  return <LoginPage />;
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
                <Route index element={<Navigate to="/dashboard" replace />} />
                <Route path="dashboard" element={<DashboardPage />} />
                <Route path="bookings" element={<BookingsPage />} />
                <Route path="bookings/:tab" element={<BookingsPage />} />
                <Route path="tickets" element={<TicketCentralPage />} />
                <Route path="tickets/:tab" element={<TicketCentralPage />} />
                <Route path="paper-review" element={<PaperReviewPage />} />
                <Route path="proposal-submission" element={<ProposalSubmissionPage />} />
                <Route path="events" element={<EventsPage />} />
                <Route path="companies" element={<CompaniesPage />} />
                <Route path="reports" element={<ReportsPage />} />
                <Route path="reports/:tab" element={<ReportsPage />} />
                <Route path="performance" element={<EventPerformancePage />} />
                <Route path="myteam" element={<TeamPage />} />
                <Route path="users" element={<UsersPage />} />
                <Route path="roles" element={<RolesPage />} />
                <Route path="teams" element={<TeamsManagementPage />} />
                <Route path="webhooks" element={<WebhookLogsPage />} />
                <Route path="googlesync" element={<GoogleSyncPage />} />
                <Route path="*" element={<Navigate to="/dashboard" replace />} />
              </Route>
              <Route path="*" element={<Navigate to="/dashboard" replace />} />
            </Routes>
          </ConfirmProvider>
        </ToastProvider>
      </SessionProvider>
    </BrowserRouter>
  );
}
