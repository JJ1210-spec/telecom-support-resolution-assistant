import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import "./styles/app.css";
import "./styles/layout.css";
import { AppLayout, ConsoleLayout, SiteLayout } from "./components/Layout";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Spinner } from "./components/ui";
import { AuthProvider, homeFor, useAuth } from "./hooks/useAuth";
import Landing from "./pages/Landing";
import { Login, Register } from "./pages/Auth";
import NewTicket from "./pages/customer/NewTicket";
import { CustomerInbox, InboxHome, TicketDetail } from "./pages/customer/Tickets";
import AdminTicket from "./pages/admin/AdminTicket";
import { IncidentsPage, OverviewPage, PlaygroundPage, QueuePage } from "./pages/admin/Console";
import { DriftPage, HealthPage, KnowledgePage, TaxonomyPage } from "./pages/admin/Admin";
function Guard({ roles, children }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading)
    return (
      <div className="container page">
        <Spinner />
      </div>
    );
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (!roles.includes(user.role)) return <Navigate to={homeFor(user)} replace />;
  return <>{children}</>;
}
function GuestOnly({ children }) {
  const { user, loading } = useAuth();
  if (loading) return null;
  return user ? <Navigate to={homeFor(user)} replace /> : <>{children}</>;
}
const ADMIN = ["admin"];
function Root() {
  const location = useLocation();
  return (
    <ErrorBoundary resetKey={location.pathname}>
      <App />
    </ErrorBoundary>
  );
}
function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route element={<SiteLayout />}>
        <Route
          path="/login"
          element={
            <GuestOnly>
              <Login />
            </GuestOnly>
          }
        />
        <Route
          path="/register"
          element={
            <GuestOnly>
              <Register />
            </GuestOnly>
          }
        />
      </Route>
      <Route
        element={
          <Guard roles={["customer"]}>
            <AppLayout />
          </Guard>
        }
      >
        <Route path="/tickets/new" element={<NewTicket />} />
        <Route path="/tickets" element={<CustomerInbox />}>
          <Route index element={<InboxHome />} />
          <Route path=":id" element={<TicketDetail />} />
        </Route>
      </Route>
      <Route
        path="/console"
        element={
          <Guard roles={ADMIN}>
            <ConsoleLayout />
          </Guard>
        }
      >
        <Route index element={<OverviewPage />} />
        <Route path="queue" element={<QueuePage />} />
        <Route path="tickets/:id" element={<AdminTicket />} />
        <Route path="incidents" element={<IncidentsPage />} />
        <Route path="playground" element={<PlaygroundPage />} />
        <Route path="knowledge" element={<KnowledgePage />} />
        <Route path="taxonomy" element={<TaxonomyPage />} />
        <Route path="drift" element={<DriftPage />} />
        <Route path="health" element={<HealthPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
createRoot(document.getElementById("root")).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <Root />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
