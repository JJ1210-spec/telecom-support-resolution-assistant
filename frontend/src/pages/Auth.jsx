import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ErrorNote, Spinner } from "../components/ui";
import { homeFor, useAuth } from "../hooks/useAuth";
export function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const user = await login(email, password);
      const next = location.state?.from;
      navigate(next && next !== "/login" ? next : homeFor(user), { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign in failed");
    } finally {
      setBusy(false);
    }
  };
  return (
    <AuthShell title="Welcome back" sub="Sign in to track your tickets or open the support console.">
      <form className="stack" onSubmit={submit}>
        <label className="field">
          <span className="label">Email</span>
          <input
            className="input"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label className="field">
          <span className="label">Password</span>
          <input
            className="input"
            type="password"
            autoComplete="current-password"
            required
            minLength={10}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <ErrorNote error={error} />
        <button className="btn btn-primary btn-block" disabled={busy}>
          {busy ? <Spinner light /> : "Sign in"}
        </button>
        <p className="caption" style={{ textAlign: "center" }}>
          New here? <Link to="/register">Create an account</Link>
        </p>
      </form>
    </AuthShell>
  );
}
export function Register() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ name: "", email: "", password: "", region: "" });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await register({ ...form, region: form.region || undefined });
      navigate("/tickets/new", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the account");
    } finally {
      setBusy(false);
    }
  };
  return (
    <AuthShell
      title="Get help in minutes"
      sub="Create an account to track your ticket and updates in your dashboard."
    >
      <form className="stack" onSubmit={submit}>
        <label className="field">
          <span className="label">Full name</span>
          <input className="input" autoComplete="name" required value={form.name} onChange={set("name")} />
        </label>
        <label className="field">
          <span className="label">Email</span>
          <input
            className="input"
            type="email"
            autoComplete="email"
            required
            value={form.email}
            onChange={set("email")}
          />
        </label>
        <label className="field">
          <span className="label">
            Area / locality <span className="caption">(optional — helps us spot local outages)</span>
          </span>
          <input className="input" placeholder="e.g. Koramangala" value={form.region} onChange={set("region")} />
        </label>
        <label className="field">
          <span className="label">Password</span>
          <input
            className="input"
            type="password"
            autoComplete="new-password"
            required
            minLength={10}
            value={form.password}
            onChange={set("password")}
          />
          <span className="hint">At least 10 characters.</span>
        </label>
        <ErrorNote error={error} />
        <button className="btn btn-primary btn-block" disabled={busy}>
          {busy ? <Spinner light /> : "Create account"}
        </button>
        <p className="caption" style={{ textAlign: "center" }}>
          Already have an account? <Link to="/login">Sign in</Link>
        </p>
      </form>
    </AuthShell>
  );
}
function AuthShell({ title, sub, children }) {
  return (
    <div className="container page" style={{ maxWidth: 480 }}>
      <div className="card" style={{ marginTop: 32 }}>
        <h1 className="title-lg">{title}</h1>
        <p className="muted" style={{ margin: "8px 0 24px" }}>
          {sub}
        </p>
        {children}
      </div>
    </div>
  );
}
