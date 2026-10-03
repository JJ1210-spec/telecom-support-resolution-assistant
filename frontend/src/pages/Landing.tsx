import { Link } from "react-router-dom";
import { BrandMark, Icon } from "../components/Icon";
import { homeFor, useAuth } from "../hooks/useAuth";

const STEPS = [
  { title: "Tell us in your words", body: "Pick a topic or just describe it — English, Hindi or Hinglish. No forms, no account numbers." },
  { title: "We narrow it down", body: "Two or three quick taps, each chosen to rule out the most possibilities. No irrelevant questions." },
  { title: "Fix it now — or a specialist", body: "Familiar issues get grounded step-by-step fixes. Outages, billing disputes and anything unusual go to a person." },
  { title: "Open until it's solved", body: "Tick what worked. If it didn't, reopen the same ticket — your specialist sees everything you tried." },
];

const FEATURES = [
  { icon: "spark", title: "Smart questions", body: "An information-gain engine asks only what changes the answer — usually two or three taps." },
  { icon: "check", title: "Grounded fixes", body: "Every step cites a verified article or past fix. Unsupported promises are filtered out in code." },
  { icon: "chat", title: "Help on every step", body: "Stuck on a step? Ask about that exact step in a side chat without losing your place." },
  { icon: "radar", title: "Outage radar", body: "Similar reports from one area become one incident, so nobody troubleshoots a problem that isn't theirs." },
  { icon: "book", title: "Learns every day", body: "Each confirmed resolution is summarised into the knowledge base for the next customer." },
  { icon: "pulse", title: "Watches for drift", body: "Flags new issue types and fixes that stopped working before they hurt customers." },
];

const FAQ = [
  { q: "Will I always get an automated answer?", a: "No. Simple, frequently solved issues get instant steps. Anything urgent (like an area outage), sensitive (billing disputes, SIM identity, number porting) or unclear goes straight to a human specialist." },
  { q: "What happens if the suggested steps don't work?", a: "Mark them “didn't work” or tap “Still not working”. The same ticket goes to a specialist with everything you've already tried, so you never repeat yourself." },
  { q: "Is my personal information safe?", a: "Phone numbers, emails, card and account numbers are removed before any AI processing. Never share passwords or OTPs — we will never ask for them." },
  { q: "Which languages are supported?", a: "English, Hindi and Hinglish today, including typos and mixed scripts." },
];

export default function Landing() {
  const { user, logout } = useAuth();
  const primary = user ? (user.role === "customer" ? "/tickets/new" : homeFor(user)) : "/register";
  const primaryLabel = user && user.role !== "customer" ? "Open the console" : "Get help now";
  return (
    <div style={{ background: "var(--canvas)" }}>
      <header className="topnav dark">
        <div className="container">
          <Link to="/" className="brand"><BrandMark /> Resolve Desk</Link>
          <nav className="navlinks hide-mobile" aria-label="Sections">
            <a className="navlink" href="#how">How it works</a>
            <a className="navlink" href="#teams">For support teams</a>
            <a className="navlink" href="#faq">FAQ</a>
          </nav>
          <div className="row" style={{ marginLeft: "auto" }}>
            {user ? (
              <>
                <Link to={homeFor(user)} className="btn btn-sm btn-dark">{user.role === "customer" ? "My tickets" : "Console"}</Link>
                <button className="btn btn-sm btn-outline-dark" onClick={() => void logout()}>Sign out</button>
              </>
            ) : (
              <>
                <Link to="/login" className="btn btn-sm btn-dark">Sign in</Link>
                <Link to="/register" className="btn btn-sm btn-primary">Get help</Link>
              </>
            )}
          </div>
        </div>
      </header>

      {/* ---------------- hero ---------------- */}
      <section className="hero hero-glow">
        <div className="container hero-grid">
          <div>
            <span className="badge" style={{ background: "#16181c", color: "#a8acb3" }}>
              <span className="dot" style={{ color: "var(--up)" }} /> Support for broadband, mobile, SIM, bills & TV
            </span>
            <h1 className="display-mega" style={{ marginTop: 24 }}>Support that solves it the first time.</h1>
            <p className="lead">
              Describe the problem in your own words. We'll narrow it down with a couple of taps, fix the familiar in
              minutes, and bring in a specialist when it really needs one.
            </p>
            <div className="row">
              <Link to={primary} className="btn btn-primary btn-lg">{primaryLabel}</Link>
              {!user && <Link to="/login" className="btn btn-outline-dark btn-lg">I'm on the support team</Link>}
            </div>
            <p className="caption" style={{ color: "#7c828a", marginTop: 20 }}>
              Email confirmation in seconds · Your ticket stays open until you say it's fixed
            </p>
          </div>
          <div className="mock-stack" aria-hidden="true">
            <div className="mock float" style={{ top: 0, left: "6%", right: 0 }}>
              <div className="row-between">
                <span className="caption" style={{ color: "#a8acb3" }}>TCK-2610-8F2A1C</span>
                <span className="badge badge-green">Solved 41× before</span>
              </div>
              <div className="title-md" style={{ marginTop: 12 }}>Internet keeps disconnecting</div>
              <div style={{ marginTop: 12 }}>
                {[["on", "Restart the router and wait 3 minutes"], ["off", "Note when it drops and on which devices"], ["", "Test with an Ethernet cable"]].map(([state, text]) => (
                  <div className="mrow" key={text}>
                    <span className={`check ${state}`}>{state === "on" ? <Icon name="check" size={14} stroke={2.5} /> : state === "off" ? <Icon name="x" size={12} stroke={2.5} /> : null}</span>
                    <span style={{ flex: 1 }}>{text}</span>
                    <Icon name="chat" size={16} />
                  </div>
                ))}
              </div>
            </div>
            <div className="mock secondary float delay" style={{ top: 255, left: 0, width: "60%" }}>
              <div className="caption" style={{ color: "#a8acb3" }}>Which of these is closest?</div>
              <div className="chips" style={{ marginTop: 12 }}>
                {["Drops at night", "Slow speed", "Weak in one room"].map((c, i) => (
                  <span key={c} className="chip" style={{ background: i === 0 ? "#0052ff" : "#0a0b0d", color: "#fff", borderColor: "#2a2d33" }}>{c}</span>
                ))}
              </div>
            </div>
            <div className="mock secondary float" style={{ top: 300, right: 0, width: "36%" }}>
              <div className="caption" style={{ color: "#a8acb3" }}>Specialist reply</div>
              <div className="body-sm" style={{ marginTop: 8 }}>“Does it drop on 5 GHz only?”</div>
              <div className="row" style={{ marginTop: 10, gap: 6 }}>
                <span className="badge badge-blue">Only 5 GHz</span><span className="badge" style={{ background: "#2a2d33", color: "#fff" }}>Both</span>
              </div>
            </div>
          </div>
        </div>
        <div className="container">
          <div className="stat-strip">
            {[["100%", "issues classified correctly"], ["0", "unsafe automated answers"], ["~5 s", "to a grounded answer"], ["3.8", "questions on average"]].map(([v, l]) => (
              <div key={l}><div className="big">{v}</div><div className="lbl">{l}</div></div>
            ))}
          </div>
          <p className="caption" style={{ color: "#5b616e", paddingBottom: 28, marginTop: -8 }}>
            Measured on 56 held-out test complaints (synthetic data) — see the evaluation report.
          </p>
        </div>
      </section>

      {/* ---------------- how it works ---------------- */}
      <section className="section" id="how">
        <div className="container stack-lg">
          <div className="eyebrow">How it works</div>
          <h2 className="display-lg" style={{ maxWidth: 760 }}>From “something's wrong” to fixed, in four steps.</h2>
          <div className="steps-grid" style={{ marginTop: 16 }}>
            {STEPS.map((s) => (
              <div key={s.title}>
                <h3 className="title-md">{s.title}</h3>
                <p className="muted" style={{ marginTop: 8 }}>{s.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ---------------- two audiences ---------------- */}
      <section className="band-soft section" id="teams">
        <div className="container duo">
          <div style={{ background: "var(--canvas)", border: "1px solid var(--hairline)" }}>
            <div className="eyebrow">For customers</div>
            <h3 className="display-sm" style={{ marginTop: 12 }}>Answers without the runaround.</h3>
            <ul className="check-list">
              <li>Tap a topic, describe it your way, answer two or three quick questions</li>
              <li>Step-by-step fixes with “worked / didn't work” on every step</li>
              <li>A side chat for each step when you're stuck</li>
              <li>Told up front when there's a known outage in your area</li>
            </ul>
            <Link to={user?.role === "customer" ? "/tickets/new" : "/register"} className="btn btn-primary" style={{ marginTop: 28 }}>Raise a ticket</Link>
          </div>
          <div className="band-dark" style={{ background: "var(--surface-dark)" }}>
            <div className="eyebrow" style={{ color: "#8a8f98" }}>For support teams</div>
            <h3 className="display-sm" style={{ marginTop: 12 }}>A copilot on every escalation.</h3>
            <ul className="check-list">
              <li>Queue sorted by severity and SLA, with churn-risk and reopen flags</li>
              <li>Copilot: similar past incidents, root causes, next actions — all cited</li>
              <li>Ask customers questions with quick-reply choices</li>
              <li>Knowledge base, drift monitoring and system health in one console</li>
            </ul>
            <Link to={user && user.role !== "customer" ? "/console" : "/login"} className="btn btn-outline-dark" style={{ marginTop: 28 }}>Agent sign in</Link>
          </div>
        </div>
      </section>

      {/* ---------------- features ---------------- */}
      <section className="section">
        <div className="container stack-lg">
          <div className="eyebrow">Under the hood</div>
          <h2 className="display-lg" style={{ maxWidth: 760 }}>Built to be trusted, not just fast.</h2>
          <div className="grid-3" style={{ marginTop: 16 }}>
            {FEATURES.map((f) => (
              <div key={f.title} className="card card-hover">
                <span className="icon-plate"><Icon name={f.icon} /></span>
                <h3 className="title-md" style={{ marginTop: 20 }}>{f.title}</h3>
                <p className="muted" style={{ marginTop: 8 }}>{f.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ---------------- FAQ ---------------- */}
      <section className="section band-soft" id="faq">
        <div className="container" style={{ maxWidth: 880 }}>
          <div className="eyebrow">FAQ</div>
          <h2 className="display-md" style={{ margin: "12px 0 32px" }}>Questions, answered.</h2>
          <div className="faq">
            {FAQ.map((f) => (
              <details key={f.q}><summary>{f.q}</summary><p>{f.a}</p></details>
            ))}
          </div>
        </div>
      </section>

      {/* ---------------- CTA ---------------- */}
      <section className="band-dark hero-glow">
        <div className="container section" style={{ textAlign: "center" }}>
          <h2 className="display-md">Your ticket stays live until it's solved.</h2>
          <p style={{ color: "var(--on-dark-soft)", marginTop: 16 }}>Acknowledged by email in seconds. Updated at every step.</p>
          <div className="row" style={{ justifyContent: "center", marginTop: 32 }}>
            <Link to={primary} className="btn btn-primary btn-lg">{primaryLabel}</Link>
          </div>
        </div>
      </section>

      <footer className="site">
        <div className="container stack">
          <div className="row-between" style={{ alignItems: "flex-start" }}>
            <span className="row"><BrandMark size={22} /> <strong>Resolve Desk</strong></span>
            <div className="row" style={{ gap: 24 }}>
              <a href="#how" className="caption">How it works</a>
              <a href="#teams" className="caption">For teams</a>
              <a href="#faq" className="caption">FAQ</a>
              <Link to="/login" className="caption">Sign in</Link>
            </div>
          </div>
          <div className="caption">Demo built on synthetic data. Never share passwords, OTPs or full card numbers with anyone — including support.</div>
        </div>
      </footer>
    </div>
  );
}
