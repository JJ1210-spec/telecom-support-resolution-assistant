import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../../api/client";
import { AdminSourceLink } from "../../components/AdminSourceLink";
import { Icon } from "../../components/Icon";
import { ConsoleHead } from "../../components/Layout";
import {
  ago,
  Bars,
  Empty,
  ErrorNote,
  pct,
  RouteBadge,
  SeverityBadge,
  Spinner,
  Stat,
  StatusBadge,
  Toast,
  useToast,
} from "../../components/ui";
import { useLiveEvents, useResource } from "../../hooks/useLive";
export function OverviewPage() {
  const stats = useResource(() => api.get("/v1/admin/stats"), []);
  useLiveEvents((e) => {
    if (["analyzed", "status", "created"].includes(e.kind)) void stats.reload();
  });
  const s = stats.data;
  return (
    <div className="stack-lg">
      <ConsoleHead eyebrow="Support operations" title="Overview">
        <Link to="/console/queue" className="btn btn-primary btn-sm">
          Open queue
        </Link>
      </ConsoleHead>
      {!s ? (
        <Spinner />
      ) : (
        <>
          <div className="grid-4">
            <Stat label="Tickets" value={s.tickets} sub={`${s.by_status.resolved ?? 0} resolved`} />
            <Stat
              label="Deflected by self-service"
              value={pct(s.deflection_rate)}
              sub={`${pct(s.self_service_rate)} routed to self-service`}
              tone="up"
            />
            <Stat
              label="Median time to resolve"
              value={s.median_hours_to_resolve === null ? "—" : `${s.median_hours_to_resolve}h`}
              sub={`${s.sla_breaches} SLA breaches`}
              tone={s.sla_breaches ? "down" : undefined}
            />
            <Stat
              label="CSAT"
              value={s.csat ?? "—"}
              sub={`${s.csat_responses} ratings · reopen ${pct(s.reopen_rate)}`}
            />
          </div>
          <div className="grid-3">
            <div className="card stack">
              <h2 className="title-md">Routing mix</h2>
              <Bars
                rows={["self_service", "assisted", "human"].map((r) => ({
                  label: r.replace("_", " "),
                  value: s.by_route[r] ?? 0,
                }))}
              />
              <p className="caption">
                Self-service resolves simple, recurring issues; assisted gives safe steps while an admin reviews;
                human handles P1, sensitive or unclear issues.
              </p>
            </div>
            <div className="card stack">
              <h2 className="title-md">Step outcomes</h2>
              <div className="mono" style={{ fontSize: 36 }}>
                {pct(s.step_success_rate)}
              </div>
              <div className="caption">
                of {s.steps_tried} tried steps worked for customers. Failing steps feed solution-drift alerts.
              </div>
              <Link to="/console/drift" className="btn btn-text" style={{ alignSelf: "flex-start" }}>
                View drift <Icon name="arrow" size={14} />
              </Link>
            </div>
            <div className="card stack">
              <h2 className="title-md">Top issues</h2>
              <Bars rows={s.top_issues.map((i) => ({ label: i.label, value: i.count }))} />
            </div>
          </div>
          <div className="card stack">
            <h2 className="title-md">Severity</h2>
            <div className="row">
              {["P1", "P2", "P3", "P4"].map((p) => (
                <div key={p} className="card-sm" style={{ minWidth: 120 }}>
                  <SeverityBadge level={p} />
                  <div className="mono" style={{ fontSize: 24, marginTop: 6 }}>
                    {s.by_severity[p] ?? 0}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
      <ErrorNote error={stats.error} />
    </div>
  );
}
export function QueuePage() {
  const [scope, setScope] = useState("human");
  const [query, setQuery] = useState("");
  const navigate = useNavigate();
  const queue = useResource(() => api.get(`/v1/admin/queue?scope=${scope}`), [scope]);
  useLiveEvents(() => void queue.reload());
  const rows = (queue.data?.tickets ?? []).filter(
    (t) =>
      !query ||
      `${t.id} ${t.subject} ${t.intent_label} ${t.customer_email}`.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <div className="stack-lg">
      <ConsoleHead eyebrow="Support" title="Queue">
        <input
          className="input search-pill"
          style={{ width: 260 }}
          placeholder="Search tickets"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </ConsoleHead>
      <div className="tabs">
        {["human", "mine", "open", "all"].map((s) => (
          <button key={s} className={`tab${scope === s ? " active" : ""}`} onClick={() => setScope(s)}>
            {{ human: "Needs a human", mine: "Assigned to me", open: "All open", all: "Everything" }[s]}
          </button>
        ))}
      </div>
      {queue.loading && !queue.data ? (
        <Spinner />
      ) : rows.length === 0 ? (
        <div className="card">
          <Empty title="Queue is clear">New escalations appear here in real time.</Empty>
        </div>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Ticket</th>
                <th>Issue</th>
                <th>Severity</th>
                <th>Route</th>
                <th>Status</th>
                <th>Customer</th>
                <th>SLA</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t.id} className="clickable" onClick={() => navigate(`/console/tickets/${t.id}`)}>
                  <td className="mono" style={{ fontSize: 12 }}>
                    {t.id}
                  </td>
                  <td style={{ maxWidth: 280 }}>
                    <div style={{ fontWeight: 500 }}>{t.intent_label || t.subject}</div>
                    <div className="row" style={{ marginTop: 4, gap: 6 }}>
                      {t.churn_risk && <span className="badge badge-red">churn risk</span>}
                      {(t.reopen_count ?? 0) > 0 && <span className="badge badge-amber">reopened</span>}
                      {t.incident_id && <span className="badge badge-amber">{t.incident_id}</span>}
                      {t.analysis_state === "running" && <span className="badge pulse">analyzing</span>}
                    </div>
                  </td>
                  <td>
                    <SeverityBadge level={t.severity} />
                  </td>
                  <td>
                    <RouteBadge route={t.route} />
                  </td>
                  <td>
                    <StatusBadge status={t.status} label={t.status_label} />
                  </td>
                  <td className="caption">{t.customer_email}</td>
                  <td className={`num ${t.sla_hours_left !== undefined && t.sla_hours_left < 2 ? "down" : ""}`}>
                    {t.sla_hours_left === undefined ? "—" : `${t.sla_hours_left}h`}
                  </td>
                  <td className="caption">{ago(t.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <ErrorNote error={queue.error} />
    </div>
  );
}
const SAMPLES = [
  "My broadband drops every evening around 8 and I've already restarted the router twice, I work from home and this is costing me",
  "Bank OTP texts are not arriving but normal SMS does",
  "mera recharge ho gaya lekin plan active nahi hua",
  "Our whole street has no internet since morning",
  "I need a guaranteed refund by tonight because my internet was slow yesterday.",
];
export function PlaygroundPage() {
  const [searchParams] = useSearchParams();
  const ticketId = searchParams.get("ticket");
  const [text, setText] = useState(ticketId ? "" : SAMPLES[0]);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const request = useRef(0);
  const editDraft = (value) => {
    request.current++;
    setBusy(false);
    setText(value);
    setResult(null);
    setError(null);
  };
  useEffect(() => {
    if (!ticketId) return;
    const current = ++request.current;
    setText("");
    setResult(null);
    setBusy(true);
    setError(null);
    api.get(`/v1/admin/tickets/${encodeURIComponent(ticketId)}`)
      .then((ticket) => {
        if (current !== request.current) return null;
        setText(ticket.complaint);
        return api.post("/v1/admin/analyze", { text: ticket.complaint, use_cache: false });
      })
      .then((analysis) => { if (analysis && current === request.current) setResult(analysis); })
      .catch((err) => { if (current === request.current) setError(err instanceof Error ? err.message : "Failed"); })
      .finally(() => { if (current === request.current) setBusy(false); });
    return () => { request.current++; };
  }, [ticketId]);
  const run = async () => {
    const current = ++request.current;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const analysis = await api.post("/v1/admin/analyze", { text, use_cache: false });
      if (current === request.current) setResult(analysis);
    } catch (err) {
      if (current === request.current) setError(err instanceof Error ? err.message : "Failed");
    } finally {
      if (current === request.current) setBusy(false);
    }
  };
  return (
    <div className="stack-lg">
      <ConsoleHead eyebrow="Explainability" title="Deep Analysis" />
      <div className="card stack">
        {ticketId && <div className="caption">Analyzing complaint from <Link to={`/console/tickets/${encodeURIComponent(ticketId)}`}>{ticketId}</Link></div>}
        <textarea className="textarea" aria-label="Complaint draft" value={text}
          onChange={(e) => editDraft(e.target.value)} />
        <div className="chips">
          {SAMPLES.map((s) => (
            <button key={s} className="chip" onClick={() => editDraft(s)}>
              {s.slice(0, 42)}…
            </button>
          ))}
        </div>
        <div className="row">
          <button className="btn btn-primary" disabled={busy || text.trim().length < 5} onClick={() => void run()}>
            {busy ? <Spinner light /> : "Analyze"}
          </button>
          <span className="caption">
            Runs the full pipeline: hybrid retrieval → triage → grounded draft → routing. Nothing is saved as a ticket.
          </span>
        </div>
        <ErrorNote error={error} />
      </div>
      {result && (
        <div className="grid-2" style={{ alignItems: "start" }}>
          <div className="stack">
            <div className="card-sm stack-sm">
              <div className="row-between">
                <span className="eyebrow">Decision</span>
                <RouteBadge route={result.decision.route} />
              </div>
              <ul className="body-sm" style={{ margin: 0, paddingLeft: 18 }}>
                {result.decision.reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
              <div className="caption">
                {result.triage.intent_label} · <SeverityBadge level={result.triage.severity} /> · confidence{" "}
                {pct(result.triage.confidence)} · {result.triage.language}
              </div>
              <div className="caption">Drivers: {result.triage.severity_drivers.join("; ")}</div>
            </div>
            <div className="card-sm stack-sm">
              <div className="eyebrow">Customer steps (self-help sources only)</div>
              {result.draft?.customer_steps.length ? (
                result.draft.customer_steps.map((s, i) => (
                  <div key={i} className="body-sm">
                    {i + 1}. {s.text}{" "}
                    {s.citations.map((c) => (
                      <AdminSourceLink key={c} id={c} />
                    ))}
                  </div>
                ))
              ) : (
                <div className="caption">
                  {result.draft?.abstain ? `Abstained: ${result.draft.abstain_reason}` : "None"}
                </div>
              )}
              <div className="eyebrow" style={{ marginTop: 10 }}>
                Admin steps
              </div>
              {result.draft?.admin_steps.map((s, i) => (
                <div key={i} className="body-sm">
                  {i + 1}. {s.text}{" "}
                  {s.citations.map((c) => (
                    <AdminSourceLink key={c} id={c} />
                  ))}
                </div>
              ))}
            </div>
            <div className="card-sm stack-sm">
              <div className="eyebrow">Latency & models</div>
              <div className="row">
                {Object.entries(result.latency_ms)
                  .filter(([, v]) => typeof v === "number")
                  .map(([k, v]) => (
                    <span key={k} className="badge">
                      <span className="num">
                        {k} {v}ms
                      </span>
                    </span>
                  ))}
              </div>
              <div className="caption mono">
                {JSON.stringify(result.models.prompts)} · {String(result.models.triage)}
              </div>
              {result.degraded.length > 0 && (
                <div className="caption" style={{ color: "var(--warn)" }}>
                  Degraded: {result.degraded.join(", ")}
                </div>
              )}
              {result.warnings.map((w) => (
                <div key={w} className="caption">
                  • {w}
                </div>
              ))}
              <a className="caption" href={`/v1/admin/traces/${result.trace_id}`} target="_blank" rel="noreferrer">
                Full trace {result.trace_id}
              </a>
            </div>
          </div>
          <div className="card-sm stack-sm">
            <div className="eyebrow">Retrieved evidence</div>
            {result.sources.map((s) => (
              <div key={s.id} style={{ borderTop: "1px solid var(--hairline-soft)", paddingTop: 8 }}>
                <div className="row-between">
                  <AdminSourceLink id={s.id} />
                  <span className="caption num">
                    sim {s.similarity?.toFixed(3) ?? "—"} · rerank {s.rerank?.toFixed(3) ?? "—"}
                  </span>
                </div>
                <div className="body-sm">{s.title}</div>
                <div className="caption">{s.snippet?.slice(0, 220)}</div>
                <AdminSourceLink id={s.id}>Open full {s.kind === "kb" ? "knowledge-base article" : "resolved case"}</AdminSourceLink>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
export function IncidentsPage() {
  const list = useResource(() => api.get("/v1/admin/incidents"), []);
  const [note, setNote] = useState({});
  const [toast, showToast] = useToast();
  useLiveEvents((e) => {
    if (e.kind === "incident_linked") void list.reload();
  });
  return (
    <div className="stack-lg">
      <ConsoleHead eyebrow="Novelty" title="Incident radar" />
      <p className="muted" style={{ maxWidth: 760, marginTop: -12 }}>
        When several customers in one area report the same problem within hours, their tickets are grouped into an
        incident. Customers are told it's a known issue (no pointless troubleshooting) and you resolve it once for
        everyone.
      </p>
      {list.loading ? (
        <Spinner />
      ) : !list.data?.incidents.length ? (
        <div className="card">
          <Empty title="No incidents detected">Radar groups ≥3 similar tickets from one region within 6 hours.</Empty>
        </div>
      ) : (
        <div className="grid-2">
          {list.data.incidents.map((i) => (
            <div key={i.id} className="card stack">
              <div className="row-between">
                <span className="mono caption">{i.id}</span>
                <span className={`badge ${i.status === "open" ? "badge-red" : "badge-green"}`}>{i.status}</span>
              </div>
              <div className="title-md">{i.title}</div>
              <div className="caption">
                {i.ticket_count} linked tickets · opened {ago(i.created_at)}
              </div>
              {i.status === "open" && (
                <>
                  <textarea
                    className="textarea"
                    style={{ minHeight: 70 }}
                    placeholder="What was fixed? (sent to every linked customer)"
                    value={note[i.id] ?? ""}
                    onChange={(e) => setNote({ ...note, [i.id]: e.target.value })}
                  />
                  <button
                    className="btn btn-primary btn-sm"
                    style={{ alignSelf: "flex-start" }}
                    disabled={(note[i.id] ?? "").trim().length < 3}
                    onClick={async () => {
                      try {
                        const r = await api.post(`/v1/admin/incidents/${i.id}/resolve`, { note: note[i.id] });
                        showToast(`Proposed the fix to ${r.tickets_updated} customers`);
                        void list.reload();
                      } catch (err) {
                        showToast(err instanceof Error ? err.message : "Failed");
                      }
                    }}
                  >
                    Resolve for all linked customers
                  </button>
                </>
              )}
            </div>
          ))}
        </div>
      )}
      <Toast message={toast} />
    </div>
  );
}
