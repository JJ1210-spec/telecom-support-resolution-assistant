import { Link } from "react-router-dom";
import { TopNav } from "../components/Layout";
import { BrandMark, Icon } from "../components/Icon";
import { useAuth, homeFor } from "../hooks/useAuth";

const FEATURES = [
  { icon: "spark", title: "Asks the right questions", body: "Each question is chosen to cut the most uncertainty about your issue, so most people answer only two or three taps." },
  { icon: "check", title: "Fixes the familiar instantly", body: "Issues we've solved many times come with grounded, step-by-step fixes. Tick what worked, chat about any step." },
  { icon: "user", title: "Humans for the hard ones", body: "Outages, billing disputes and anything unusual go straight to a specialist — with everything you've already tried." },
  { icon: "refresh", title: "Live until it's solved", body: "Your ticket stays open until you confirm the fix. If it didn't work, reopen the same ticket in one tap." },
  { icon: "book", title: "Gets smarter every case", body: "Every confirmed resolution is summarised into the knowledge base so the next customer gets the answer faster." },
  { icon: "radar", title: "Spots outages early", body: "Similar complaints from one area are grouped into an incident, so nobody troubleshoots a problem that isn't theirs." },
];

export default function Landing() {
  const { user } = useAuth();
  const cta = user ? homeFor(user) : "/register";
  return (
    <>
      <TopNav dark />
      <section className="hero">
        <div className="container hero-grid">
          <div>
            <span className="badge" style={{ background: "#16181c", color: "#a8acb3" }}>
              Telecom support · AI-assisted
            </span>
            <h1 className="display-mega" style={{ marginTop: 24 }}>
              Support that solves it the first time.
            </h1>
            <p className="lead">
              Tell us what's wrong in your own words. We'll narrow it down with a few quick questions, fix the familiar
              in minutes, and bring in a specialist when it really needs one.
            </p>
            <div className="row">
              <Link to={user?.role === "customer" ? "/tickets/new" : cta} className="btn btn-primary btn-lg">
                Get help now
              </Link>
              <Link to="/login" className="btn btn-outline-dark btn-lg">
                Agent sign in
              </Link>
            </div>
          </div>
          <div className="mock-stack" aria-hidden="true">
            <div className="mock" style={{ top: 0, left: "6%", right: 0 }}>
              <div className="row-between">
                <span className="caption" style={{ color: "#a8acb3" }}>TCK-2610-8F2A1C</span>
                <span className="badge badge-green">Solved 41× before</span>
              </div>
              <div className="title-md" style={{ marginTop: 12 }}>Internet keeps disconnecting</div>
              <div style={{ marginTop: 12 }}>
                {[
                  ["on", "Restart the router and wait 3 minutes"],
                  ["off", "Note when it drops and on which devices"],
                  ["", "Test with an Ethernet cable"],
                ].map(([state, text]) => (
                  <div className="mrow" key={text}>
                    <span className={`check ${state}`}>{state === "on" ? <Icon name="check" size={14} stroke={2.5} /> : state === "off" ? <Icon name="x" size={12} stroke={2.5} /> : null}</span>
                    <span style={{ flex: 1 }}>{text}</span>
                    <Icon name="chat" size={16} />
                  </div>
                ))}
              </div>
            </div>
            <div className="mock secondary" style={{ top: 250, left: 0, width: "62%" }}>
              <div className="caption" style={{ color: "#a8acb3" }}>Which of these is closest?</div>
              <div className="chips" style={{ marginTop: 12 }}>
                {["Drops at night", "Slow speed", "Weak in one room"].map((c, i) => (
                  <span key={c} className="chip" style={{ background: i === 0 ? "#0052ff" : "#0a0b0d", color: "#fff", borderColor: "#2a2d33" }}>
                    {c}
                  </span>
                ))}
              </div>
            </div>
            <div className="mock secondary" style={{ top: 300, right: 0, width: "40%" }}>
              <div className="caption" style={{ color: "#a8acb3" }}>Confidence</div>
              <div className="mono" style={{ fontSize: 32, marginTop: 4 }}>0.91</div>
              <div className="caption up" style={{ marginTop: 2 }}>+0.44 after 2 answers</div>
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container stack-lg">
          <div className="eyebrow">How it works</div>
          <h2 className="display-lg" style={{ maxWidth: 720 }}>
            Simple issues solved by you. Complex ones by people who know.
          </h2>
          <div className="grid-3" style={{ marginTop: 24 }}>
            {FEATURES.map((f) => (
              <div key={f.title} className="card card-hover">
                <span className="icon-plate">
                  <Icon name={f.icon} />
                </span>
                <h3 className="title-md" style={{ marginTop: 20 }}>
                  {f.title}
                </h3>
                <p className="muted" style={{ marginTop: 8 }}>
                  {f.body}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="band-dark">
        <div className="container section" style={{ textAlign: "center" }}>
          <h2 className="display-md">Your ticket stays live until it's solved.</h2>
          <p style={{ color: "var(--on-dark-soft)", marginTop: 16 }}>Acknowledged by email in seconds. Updated at every step.</p>
          <div className="row" style={{ justifyContent: "center", marginTop: 32 }}>
            <Link to={user?.role === "customer" ? "/tickets/new" : cta} className="btn btn-primary btn-lg">
              Raise a ticket
            </Link>
          </div>
        </div>
      </section>

      <footer className="site">
        <div className="container row-between">
          <span className="row">
            <BrandMark size={22} /> Resolve Desk
          </span>
          <span className="caption">Synthetic demo data · Never share passwords, OTPs or full card numbers with anyone.</span>
        </div>
      </footer>
    </>
  );
}
