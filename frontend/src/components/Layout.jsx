import { useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../hooks/useAuth";
import { useLiveEvents, useResource } from "../hooks/useLive";
import { ErrorBoundary } from "./ErrorBoundary";
import { BrandMark, Icon } from "./Icon";
function initials(name, email) {
  const source = (name || email || "?").trim();
  return source
    .split(/\s+/)
    .map((p) => p[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}
export function TopNav({ dark = false }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  return (
    <header className={`topnav${dark ? " dark" : ""}`}>
      <div className="container">
        <Link to="/" className="brand">
          <BrandMark />
          Resolve Desk
        </Link>
        <nav className="navlinks hide-mobile" aria-label="Main">
          {user?.role === "customer" && (
            <>
              <NavLink
                to="/tickets"
                className={({ isActive }) =>
                  `navlink${isActive && !location.pathname.endsWith("/new") ? " active" : ""}`
                }
              >
                My tickets
              </NavLink>
              <NavLink to="/tickets/new" className={({ isActive }) => `navlink${isActive ? " active" : ""}`}>
                New request
              </NavLink>
            </>
          )}
          {user && user.role !== "customer" && (
            <NavLink to="/console" className="navlink">
              Console
            </NavLink>
          )}
        </nav>
        <div className="row" style={{ marginLeft: "auto" }}>
          {user ? (
            <>
              <span className="avatar hide-mobile" title={user.email}>
                {initials(user.name, user.email)}
              </span>
              <button
                className={`btn btn-sm ${dark ? "btn-dark" : "btn-secondary"}`}
                onClick={async () => {
                  await logout();
                  navigate("/");
                }}
              >
                Sign out
              </button>
            </>
          ) : (
            <>
              <Link to="/login" className={`btn btn-sm ${dark ? "btn-dark" : "btn-secondary"}`}>
                Sign in
              </Link>
              <Link to="/register" className="btn btn-sm btn-primary">
                Get help
              </Link>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
/** Normal scrolling page (sign in / register). */
export function SiteLayout() {
  const location = useLocation();
  return (
    <>
      <TopNav />
      <main>
        <ErrorBoundary resetKey={location.pathname}>
          <Outlet />
        </ErrorBoundary>
      </main>
    </>
  );
}
/** Full-height app shell for the customer portal: fixed header, panels scroll on their own. */
export function AppLayout() {
  const location = useLocation();
  return (
    <div className="app">
      <TopNav />
      <main className="app-body">
        <ErrorBoundary resetKey={location.pathname}>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  );
}
const CONSOLE_LINKS = [
  { to: "/console", label: "Overview", icon: "grid", section: "Support" },
  { to: "/console/queue", label: "Queue", icon: "inbox" },
  { to: "/console/incidents", label: "Incident radar", icon: "radar" },
  { to: "/console/playground", label: "Playground", icon: "play" },
  { to: "/console/knowledge", label: "Knowledge base", icon: "book", section: "Knowledge" },
  { to: "/console/taxonomy", label: "Taxonomy & discovery", icon: "tree" },
  { to: "/console/drift", label: "Data drift", icon: "pulse", section: "Reliability" },
  { to: "/console/health", label: "System health", icon: "shield" },
];
export function ConsoleLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const flush = location.pathname.startsWith("/console/tickets/");
  const [tick, setTick] = useState(0);
  const counts = useResource(() => api.get("/v1/agent/queue?scope=human"), [tick]);
  useLiveEvents((event) => {
    if (["created", "analyzed", "escalated", "status", "reopened"].includes(event.kind)) setTick((t) => t + 1);
  });
  return (
    <div className="console">
      <aside className="sidebar" aria-label="Console navigation">
        <Link to="/console" className="brand" style={{ color: "#fff" }}>
          <BrandMark />
          Resolve Desk
        </Link>
        {CONSOLE_LINKS.map((link) => (
          <SideLink
            key={link.to}
            {...link}
            count={link.to === "/console/queue" ? counts.data?.counts.human_queue : undefined}
          />
        ))}
        <div style={{ marginTop: "auto" }} />
        <div className="side-link" style={{ cursor: "default" }}>
          <span className="avatar" style={{ background: "#2a2d33", color: "#fff" }}>
            {initials(user?.name, user?.email)}
          </span>
          <span style={{ minWidth: 0 }}>
            <span style={{ display: "block", color: "#fff", overflow: "hidden", textOverflow: "ellipsis" }}>
              {user?.name || user?.email}
            </span>
            <span style={{ fontSize: 12 }}>{user?.role}</span>
          </span>
        </div>
        <button
          className="side-link"
          style={{ background: "none", border: 0, cursor: "pointer", textAlign: "left" }}
          onClick={async () => {
            await logout();
            navigate("/");
          }}
        >
          <Icon name="logout" size={18} /> Sign out
        </button>
      </aside>
      <main className={`console-main${flush ? " flush" : ""}`}>
        <ErrorBoundary resetKey={location.pathname}>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  );
}
function SideLink({ to, label, icon, count, section }) {
  return (
    <>
      {section && <div className="side-section">{section}</div>}
      <NavLink to={to} end={to === "/console"} className={({ isActive }) => `side-link${isActive ? " active" : ""}`}>
        <Icon name={icon} size={18} />
        {label}
        {count !== undefined && count > 0 && <span className="count">{count}</span>}
      </NavLink>
    </>
  );
}
export function ConsoleHead({ eyebrow, title, children }) {
  return (
    <div className="console-head">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1 className="display-sm" style={{ marginTop: 6 }}>
          {title}
        </h1>
      </div>
      {children && <div className="row">{children}</div>}
    </div>
  );
}
