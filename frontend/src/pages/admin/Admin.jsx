import { useState } from "react";
import { api } from "../../api/client";
import { Icon } from "../../components/Icon";
import { ConsoleHead } from "../../components/Layout";
import {
  ago,
  Bars,
  Empty,
  ErrorNote,
  Meter,
  pct,
  Spinner,
  Sparkline,
  Stat,
  Toast,
  useToast,
} from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { useResource } from "../../hooks/useLive";
export function KnowledgePage() {
  const { user } = useAuth();
  const [filter, setFilter] = useState("draft");
  const list = useResource(
    () => api.get(`/v1/admin/kb${filter === "all" || filter === "flagged" ? "" : `?status=${filter}`}`),
    [filter],
  );
  const [toast, showToast] = useToast();
  const articles = (list.data?.articles ?? []).filter((a) => filter !== "flagged" || a.review_reason);
  const decide = async (kb, action) => {
    try {
      await api.post(`/v1/admin/kb/${kb.kb_id}/decision`, { action });
      showToast(`${kb.kb_id} ${action === "publish" ? "published and indexed" : action + "d"}`);
      void list.reload();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed");
    }
  };
  return (
    <div className="stack-lg">
      <ConsoleHead eyebrow="Learning loop" title="Knowledge base" />
      <p className="muted" style={{ maxWidth: 780, marginTop: -12 }}>
        Every confirmed resolution is summarised and indexed as a searchable case immediately. When the fix isn't
        covered by an existing article, a draft article is proposed here for review. Articles whose steps keep failing
        for customers are flagged by solution-drift monitoring.
      </p>
      <div className="tabs">
        {["draft", "flagged", "published", "deprecated", "all"].map((f) => (
          <button key={f} className={`tab${filter === f ? " active" : ""}`} onClick={() => setFilter(f)}>
            {f[0].toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>
      {list.loading ? (
        <Spinner />
      ) : !articles.length ? (
        <div className="card">
          <Empty title="Nothing here">
            {filter === "draft" ? "Resolve a ticket with a novel fix to see a learned draft." : ""}
          </Empty>
        </div>
      ) : (
        <div className="stack">
          {articles.map((a) => (
            <div key={a.kb_id} className="card stack-sm">
              <div className="row-between">
                <div className="row">
                  <span className="mono caption">
                    {a.kb_id} · v{a.version}
                  </span>
                  <span
                    className={`badge ${a.status === "published" ? "badge-green" : a.status === "draft" ? "badge-blue" : ""}`}
                  >
                    {a.status}
                  </span>
                  {a.origin === "learned" && (
                    <span className="badge badge-blue">learned from {a.source_ticket_id}</span>
                  )}
                </div>
                <span className="caption">{ago(a.updated_at)}</span>
              </div>
              <div className="title-md">{a.title}</div>
              {a.review_reason && (
                <div className="alert alert-medium">
                  <Icon name="alert" size={18} /> {a.review_reason}
                </div>
              )}
              <div className="muted body-sm">{a.summary}</div>
              <div className="grid-2" style={{ gap: 16 }}>
                <div>
                  <div className="caption">Customer self-help</div>
                  <ol className="body-sm" style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                    {a.self_help.map((s) => (
                      <li key={s}>{s}</li>
                    ))}
                  </ol>
                  {!a.self_help.length && <div className="caption">None — agent-only article</div>}
                </div>
                <div>
                  <div className="caption">Agent checks</div>
                  <ol className="body-sm" style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                    {a.checks.map((s) => (
                      <li key={s}>{s}</li>
                    ))}
                  </ol>
                </div>
              </div>
              {a.escalation && <div className="caption">Escalate if: {a.escalation}</div>}
              {user?.role === "admin" && (
                <div className="row" style={{ marginTop: 6 }}>
                  {a.status !== "published" && (
                    <button className="btn btn-primary btn-sm" onClick={() => decide(a, "publish")}>
                      Publish
                    </button>
                  )}
                  {a.status === "published" && (
                    <button className="btn btn-secondary btn-sm" onClick={() => decide(a, "deprecate")}>
                      Deprecate
                    </button>
                  )}
                  {a.status === "draft" && (
                    <button className="btn btn-text btn-sm" onClick={() => decide(a, "reject")}>
                      Reject
                    </button>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      <Toast message={toast} />
    </div>
  );
}
export function TaxonomyPage() {
  const { user } = useAuth();
  const tax = useResource(() => api.get("/v1/admin/taxonomy"), []);
  const disc = useResource(() => api.get("/v1/admin/discovery"), []);
  const [busy, setBusy] = useState(false);
  const [toast, showToast] = useToast();
  const runDiscovery = async () => {
    setBusy(true);
    try {
      const r = await api.post("/v1/admin/discovery/run");
      showToast(`Clustered ${r.pooled} pooled tickets into ${r.clusters} proposal(s)`);
      void disc.reload();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed");
    } finally {
      setBusy(false);
    }
  };
  const decide = async (p, decision) => {
    try {
      await api.post(`/v1/admin/discovery/${p.id}/decision`, { decision });
      showToast(decision === "approve" ? `New class ${p.intent} published` : "Proposal rejected");
      void disc.reload();
      void tax.reload();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed");
    }
  };
  const pending = (disc.data?.proposals ?? []).filter((p) => p.status === "pending");
  return (
    <div className="stack-lg">
      <ConsoleHead eyebrow={`Taxonomy v${tax.data?.version ?? "…"}`} title="Taxonomy & discovery">
        {user?.role === "admin" && (
          <button className="btn btn-primary btn-sm" disabled={busy} onClick={runDiscovery}>
            {busy ? <Spinner light /> : "Run discovery"}
          </button>
        )}
      </ConsoleHead>
      <div className="grid-3">
        <Stat label="Active classes" value={tax.data?.classes.filter((c) => c.status === "active").length ?? "—"} />
        <Stat
          label="Discovery pool"
          value={disc.data?.pool.filter((p) => !p.proposal_id).length ?? "—"}
          sub="unclassified / OOD / low-confidence tickets"
        />
        <Stat label="Pending proposals" value={pending.length} />
      </div>
      {pending.length > 0 && (
        <div className="stack">
          <h2 className="title-md">Proposed new classes</h2>
          {pending.map((p) => (
            <div key={p.id} className="card stack-sm">
              <div className="row-between">
                <div className="row">
                  <span className="title-md">{p.label}</span>
                  <span className="mono caption">{p.intent}</span>
                </div>
                <span className="caption">
                  {p.cluster_size} tickets · cohesion <span className="num">{p.cohesion}</span>
                </span>
              </div>
              <div className="muted body-sm">{p.description}</div>
              <div className="caption">
                Nearest existing class: {p.nearest_intent ?? "—"} (similarity {p.nearest_similarity ?? "—"})
              </div>
              <ul className="body-sm" style={{ margin: 0, paddingLeft: 18 }}>
                {p.examples.slice(0, 4).map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
              {user?.role === "admin" && (
                <div className="row">
                  <button className="btn btn-primary btn-sm" onClick={() => decide(p, "approve")}>
                    Approve & publish
                  </button>
                  <button className="btn btn-secondary btn-sm" onClick={() => decide(p, "reject")}>
                    Reject (noise)
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      <div className="grid-2" style={{ alignItems: "start" }}>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Class</th>
                <th>Area</th>
                <th>Product</th>
                <th>Flags</th>
              </tr>
            </thead>
            <tbody>
              {tax.data?.classes.map((c) => (
                <tr key={c.intent}>
                  <td>
                    <div style={{ fontWeight: 500 }}>{c.label}</div>
                    <div className="mono caption">{c.intent}</div>
                  </td>
                  <td className="caption">{c.area}</td>
                  <td className="caption">{c.product}</td>
                  <td className="row" style={{ gap: 4 }}>
                    {c.sensitive && <span className="badge badge-amber">human only</span>}
                    {c.status !== "active" && <span className="badge">{c.status}</span>}
                    {c.version_added > 1 && <span className="badge badge-blue">v{c.version_added}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="stack">
          <div className="card-sm stack-sm">
            <div className="eyebrow">Discovery pool</div>
            {!disc.data?.pool.length && (
              <div className="caption">Empty — tickets land here when they don't fit any class.</div>
            )}
            {disc.data?.pool.slice(0, 12).map((p) => (
              <div
                key={p.ticket_id}
                className="body-sm"
                style={{ borderTop: "1px solid var(--hairline-soft)", paddingTop: 6 }}
              >
                <span className="badge" style={{ marginRight: 6 }}>
                  {p.reason.replace(/_/g, " ")}
                </span>
                {p.text.slice(0, 120)}
              </div>
            ))}
          </div>
          <div className="card-sm stack-sm">
            <div className="eyebrow">Version history</div>
            {tax.data?.history.map((h) => (
              <div key={h.version} className="body-sm">
                <span className="mono">v{h.version}</span> · {h.created_by} ·{" "}
                <span className="caption">{JSON.stringify(h.changelog).slice(0, 90)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
      <Toast message={toast} />
    </div>
  );
}
export function DriftPage() {
  const { user } = useAuth();
  const [days, setDays] = useState(7);
  const drift = useResource(() => api.get(`/v1/admin/drift?days=${days}`), [days]);
  const [toast, showToast] = useToast();
  const m = drift.data?.metrics;
  const num = (k) => (typeof m?.[k] === "number" ? m[k] : null);
  const series = (k) => (drift.data?.history ?? []).map((h) => Number(h[k] ?? 0));
  const act = async (actionId) => {
    try {
      if (actionId === "run_discovery") {
        const r = await api.post("/v1/admin/discovery/run");
        showToast(`Discovery found ${r.clusters} candidate class(es)`);
      } else if (actionId.startsWith("review_kb:")) {
        const kb = actionId.split(":")[1];
        await api.post(`/v1/admin/drift/flag-kb/${kb}`, {
          note: "Customer success rate for this article's steps dropped (solution drift)",
        });
        showToast(`${kb} flagged for review in Knowledge base`);
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed");
    }
  };
  const intentRows = Object.entries(m?.intent_distribution ?? {})
    .map(([k, v]) => ({
      label: k,
      value: v,
      ref: (m?.reference_intent_distribution ?? {})[k] ?? 0,
    }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 8);
  return (
    <div className="stack-lg">
      <ConsoleHead eyebrow="Monitoring" title="Data drift">
        <select
          className="select"
          style={{ width: 140, minHeight: 40, padding: "6px 12px" }}
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
        >
          {[1, 7, 30].map((d) => (
            <option key={d} value={d}>
              Last {d} day{d > 1 ? "s" : ""}
            </option>
          ))}
        </select>
        <button className="btn btn-secondary btn-sm" onClick={() => void drift.reload()}>
          <Icon name="refresh" size={14} /> Recompute
        </button>
      </ConsoleHead>
      {!drift.data ? (
        <Spinner />
      ) : (
        <>
          <div className="stack-sm">
            {drift.data.alerts.length === 0 && (
              <div className="alert alert-info">
                <Icon name="check" size={18} /> No drift alerts in this window.
              </div>
            )}
            {drift.data.alerts.map((a, i) => (
              <div key={i} className={`alert alert-${a.level}`}>
                <Icon name="alert" size={18} />
                <div className="grow">
                  <strong>{a.message}</strong>
                  <div className="caption" style={{ color: "inherit" }}>
                    {a.metric} = <span className="num">{a.value ?? "—"}</span> (threshold {a.threshold}) · Recommended:{" "}
                    {a.action}
                  </div>
                </div>
                {user?.role === "admin" && a.action_id !== "none" && a.action_id !== "open_incidents" && (
                  <button className="btn btn-xs btn-outline" onClick={() => act(a.action_id)}>
                    Take action
                  </button>
                )}
              </div>
            ))}
          </div>
          <div className="grid-4">
            <Stat
              label="PSI · issue mix vs corpus"
              value={num("psi_intent")?.toFixed(3) ?? "—"}
              sub="> 0.2 = significant shift"
              tone={(num("psi_intent") ?? 0) > 0.2 ? "down" : undefined}
            />
            <Stat
              label="Out-of-distribution rate"
              value={pct(num("ood_rate"))}
              sub={`closest past case below threshold · mean top-1 ${num("mean_top_similarity") ?? "—"}`}
            />
            <Stat
              label="Unclassified ('other')"
              value={pct(num("other_rate"))}
              sub={`low-confidence ${pct(num("low_confidence_rate"))}`}
            />
            <Stat
              label="Complaint centroid shift"
              value={num("centroid_shift")?.toFixed(4) ?? "—"}
              sub={`${num("tickets_in_window")} tickets in window`}
            />
          </div>
          <div className="grid-2" style={{ alignItems: "start" }}>
            <div className="card stack">
              <h2 className="title-md">Issue mix: now vs historical corpus</h2>
              {intentRows.length ? (
                intentRows.map((r) => (
                  <div key={r.label} className="stack-sm" style={{ gap: 4 }}>
                    <div className="row-between body-sm">
                      <span>{r.label}</span>
                      <span className="num caption">
                        {pct(r.value)} vs {pct(r.ref)}
                      </span>
                    </div>
                    <Meter value={r.value} />
                    <div className="meter" style={{ height: 3 }}>
                      <span style={{ width: `${r.ref * 100}%`, background: "var(--muted-soft)" }} />
                    </div>
                  </div>
                ))
              ) : (
                <div className="caption">No analysed tickets in this window yet.</div>
              )}
            </div>
            <div className="card stack">
              <h2 className="title-md">Trend</h2>
              {[
                ["psi_intent", "PSI issue mix", 0.2],
                ["ood_rate", "OOD rate", 0.15],
                ["other_rate", "Unclassified rate", 0.1],
              ].map(([k, label, th]) => (
                <div key={k} className="row-between">
                  <span className="body-sm">{label}</span>
                  <Sparkline values={series(k)} threshold={th} />
                </div>
              ))}
              <p className="caption">Each recompute stores a snapshot; dashed line = alert threshold.</p>
            </div>
          </div>
          <div className="card stack">
            <h2 className="title-md">Solution drift — are our fixes still working?</h2>
            <p className="muted body-sm" style={{ marginTop: -8 }}>
              Success rate of each KB article's steps from customers' "worked / didn't work" ticks. A fix that suddenly
              fails (e.g. after a firmware or policy change) is flagged for review.
            </p>
            {!drift.data.solution_drift.length ? (
              <div className="caption">No step outcomes recorded yet.</div>
            ) : (
              <div className="table-wrap" style={{ border: 0 }}>
                <table className="table">
                  <thead>
                    <tr>
                      <th>Article</th>
                      <th>All-time</th>
                      <th>Recent</th>
                      <th>Attempts</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {drift.data.solution_drift.map((r) => (
                      <tr key={r.kb_id}>
                        <td>
                          <div style={{ fontWeight: 500 }}>{r.title}</div>
                          <div className="mono caption">{r.kb_id}</div>
                        </td>
                        <td className="num">{pct(r.success_rate)}</td>
                        <td className={`num ${r.flagged ? "down" : ""}`}>{pct(r.recent_success_rate)}</td>
                        <td className="num">{r.attempts}</td>
                        <td>{r.flagged && <span className="badge badge-red">flagged</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
      <ErrorNote error={drift.error} />
      <Toast message={toast} />
    </div>
  );
}
export function HealthPage() {
  const health = useResource(() => api.get("/v1/admin/health"), [], 15000);
  const evals = useResource(() => api.get("/v1/admin/evals"), []);
  const emails = useResource(() => api.get("/v1/admin/emails"), []);
  const [tab, setTab] = useState("system");
  const [preview, setPreview] = useState(null);
  const [toast, showToast] = useToast();
  const h = health.data;
  const latency = h
    ? Object.entries(h.metrics.latency_ms)
        .filter(([k]) => /analysis|retrieval|llm_latency|http_latency\{.*(tickets|intake).*\}/.test(k))
        .slice(0, 14)
    : [];
  const run = evals.data?.runs[0];
  return (
    <div className="stack-lg">
      <ConsoleHead eyebrow="Reliability" title="System health" />
      <div className="tabs">
        {["system", "evals", "emails"].map((t) => (
          <button key={t} className={`tab${tab === t ? " active" : ""}`} onClick={() => setTab(t)}>
            {{ system: "Services & quotas", evals: "Evaluation", emails: "Email outbox" }[t]}
          </button>
        ))}
      </div>
      {tab === "system" &&
        (!h ? (
          <Spinner />
        ) : (
          <>
            <div className="grid-4">
              <Stat
                label="Database"
                value={h.components.database ? "Up" : "Down"}
                tone={h.components.database ? "up" : "down"}
                sub={String(h.config.database)}
              />
              <Stat
                label="Vector index"
                value={h.components.vector_store.ok ? "Up" : "Down"}
                tone={h.components.vector_store.ok ? "up" : "down"}
                sub={`${h.components.vector_store.backend} · ${h.index_counts.tickets} cases · ${h.index_counts.kb} KB sections`}
              />
              <Stat
                label="Cache / quota store"
                value={h.components.kv.ok ? "Up" : "Down"}
                tone={h.components.kv.ok ? "up" : "down"}
                sub={h.components.kv.backend}
              />
              <Stat
                label="Notification channel"
                value={h.outbox.channel}
                sub={`${h.outbox.dead_letters.length} dead letters`}
                tone={h.outbox.dead_letters.length ? "down" : undefined}
              />
            </div>
            <div className="grid-2" style={{ alignItems: "start" }}>
              <div className="card stack">
                <h2 className="title-md">LLM providers (daily quota meters)</h2>
                {h.providers.map((p) => (
                  <div key={p.provider + p.model} className="stack-sm" style={{ gap: 4 }}>
                    <div className="row-between body-sm">
                      <span>
                        <strong>{p.provider}</strong> <span className="mono caption">{p.model}</span>
                      </span>
                      <span className="row" style={{ gap: 6 }}>
                        <span className={`badge ${p.breaker === "closed" ? "badge-green" : "badge-red"}`}>
                          breaker {p.breaker}
                        </span>
                        <span className="num caption">
                          {p.used_today}/{p.daily_limit}
                        </span>
                      </span>
                    </div>
                    <Meter
                      value={p.used_today}
                      max={p.daily_limit}
                      tone={
                        p.used_today / p.daily_limit > 0.9
                          ? "bad"
                          : p.used_today / p.daily_limit > 0.7
                            ? "warn"
                            : undefined
                      }
                    />
                  </div>
                ))}
                <p className="caption">
                  Calls fail over to the next provider before 90% of a free-tier quota is used, and when a breaker
                  opens.
                </p>
              </div>
              <div className="card stack">
                <h2 className="title-md">Latency (this instance)</h2>
                {latency.length ? (
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Metric</th>
                        <th>n</th>
                        <th>p50</th>
                        <th>p95</th>
                      </tr>
                    </thead>
                    <tbody>
                      {latency.map(([k, v]) => (
                        <tr key={k}>
                          <td className="caption" style={{ maxWidth: 260, overflowWrap: "anywhere" }}>
                            {k}
                          </td>
                          <td className="num">{v.count}</td>
                          <td className="num">{v.p50}</td>
                          <td className="num">{v.p95}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <div className="caption">No traffic recorded on this instance yet.</div>
                )}
              </div>
            </div>
            <div className="card stack">
              <div className="row-between">
                <h2 className="title-md">Outbox & dead-letter queue</h2>
                <button
                  className="btn btn-secondary btn-sm"
                  disabled={!h.outbox.dead_letters.length}
                  onClick={async () => {
                    const r = await api.post("/v1/admin/outbox/replay");
                    showToast(`Replayed ${r.replayed}`);
                    void health.reload();
                  }}
                >
                  Replay dead letters
                </button>
              </div>
              <div className="row">
                {h.outbox.counts.map((c) => (
                  <span key={c.topic + c.status} className="badge">
                    {c.topic} · {c.status} · <span className="num">{c.count}</span>
                  </span>
                ))}
              </div>
              {h.outbox.dead_letters.map((d) => (
                <div key={d.id} className="caption mono">
                  {d.id} {d.topic} ×{d.attempts}: {d.last_error}
                </div>
              ))}
            </div>
            <details className="card-sm">
              <summary className="title-sm" style={{ cursor: "pointer" }}>
                Active configuration (no secrets)
              </summary>
              <pre className="code">{JSON.stringify(h.config, null, 2)}</pre>
            </details>
          </>
        ))}
      {tab === "evals" &&
        (!run ? (
          <div className="card">
            <Empty title="No evaluation runs yet">
              Run <span className="mono">telecom-assistant eval</span> to benchmark triage, retrieval, routing safety
              and latency.
            </Empty>
          </div>
        ) : (
          <EvalView run={run} />
        ))}
      {tab === "emails" && (
        <div className="grid-2" style={{ alignItems: "start" }}>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Template</th>
                  <th>To</th>
                  <th>Status</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {emails.data?.emails.map((e) => (
                  <tr key={e.event_id} className="clickable" onClick={() => setPreview(e)}>
                    <td>
                      <div style={{ fontWeight: 500 }}>{e.template.replace(/_/g, " ")}</div>
                      <div className="mono caption">{e.ticket_id}</div>
                    </td>
                    <td className="caption">{e.to_address}</td>
                    <td>
                      <span className={`badge ${e.status === "failed" ? "badge-red" : "badge-green"}`}>
                        {e.status} · {e.transport}
                      </span>
                    </td>
                    <td className="caption">{ago(e.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="card-sm">
            {preview ? (
              <>
                <div className="title-sm" style={{ marginBottom: 10 }}>
                  {preview.subject}
                </div>
                <iframe
                  title="Email preview"
                  srcDoc={preview.html}
                  sandbox=""
                  style={{ width: "100%", height: 560, border: "1px solid var(--hairline)", borderRadius: 12 }}
                />
              </>
            ) : (
              <div className="caption">Select an email to preview the rendered message.</div>
            )}
          </div>
        </div>
      )}
      <Toast message={toast} />
    </div>
  );
}
function EvalView({ run }) {
  const m = run.metrics;
  const t = m.triage ?? {};
  const c = m.clarification ?? {};
  const r = m.routing ?? {};
  const g = m.generation ?? {};
  const retrieval = m.retrieval ?? {};
  const latency = (m.health ?? {}).latency_ms ?? {};
  const n = (v) => (typeof v === "number" ? v : null);
  return (
    <div className="stack-lg">
      <div className="caption">
        Run <span className="mono">{run.run_id}</span> · git {run.git_sha ?? "—"} · {ago(run.created_at)}
      </div>
      <div className="grid-4">
        <Stat label="Intent macro-F1" value={n(t.intent_macro_f1)?.toFixed(3) ?? "—"} sub="target ≥ 0.80" />
        <Stat label="P1 recall" value={pct(n(t.p1_recall))} sub="target ≥ 95%" />
        <Stat
          label="Unsafe self-service routes"
          value={Array.isArray(r.unsafe_self_service) ? r.unsafe_self_service.length : "—"}
          sub="target 0"
          tone={Array.isArray(r.unsafe_self_service) && r.unsafe_self_service.length ? "down" : "up"}
        />
        <Stat
          label="Citation validity"
          value={pct(n(g.citation_validity))}
          sub={`step support ${pct(n(g.step_support_rate))}`}
        />
      </div>
      <div className="grid-2" style={{ alignItems: "start" }}>
        <div className="card stack">
          <h2 className="title-md">Adaptive clarification</h2>
          <Bars
            rows={[
              { label: "Intent accuracy — complaint only", value: n(c.intent_accuracy_before_questions) ?? 0 },
              { label: "Intent accuracy — after questions", value: n(c.intent_accuracy_after_questions) ?? 0 },
            ]}
            format={(v) => pct(v)}
          />
          <div className="caption">
            Mean {String(c.mean_questions)} questions · {String(c.mean_information_gain_bits)} bits gained per ticket
          </div>
        </div>
        <div className="card stack">
          <h2 className="title-md">Retrieval ablation</h2>
          <table className="table">
            <thead>
              <tr>
                <th>Mode</th>
                <th>KB R@5</th>
                <th>MRR@10</th>
                <th>Ticket hit@5</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(retrieval).map(([k, v]) => (
                <tr key={k}>
                  <td>{k}</td>
                  <td className="num">{v["kb_recall@5"]?.toFixed(3)}</td>
                  <td className="num">{v["kb_mrr@10"]?.toFixed(3)}</td>
                  <td className="num">{v["ticket_intent_hit@5"]?.toFixed(3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card stack">
          <h2 className="title-md">Triage</h2>
          <Bars
            rows={[
              ["Intent accuracy", t.intent_accuracy],
              ["Product accuracy", t.product_accuracy],
              ["Severity accuracy", t.severity_accuracy],
              ["Sentiment agreement", t.sentiment_agreement],
            ].map(([l, v]) => ({ label: l, value: n(v) ?? 0 }))}
            format={(v) => pct(v)}
          />
          <div className="caption">
            By language:{" "}
            {Object.entries(t.by_language ?? {})
              .map(([k, v]) => `${k} ${pct(v)}`)
              .join(" · ")}
          </div>
        </div>
        <div className="card stack">
          <h2 className="title-md">Routing & latency</h2>
          <div className="caption">Distribution: {JSON.stringify(r.distribution)}</div>
          <div className="caption">
            Abstention precision {pct(n(r.abstention_precision))} · recall {pct(n(r.abstention_recall))}
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>Stage</th>
                <th>p50 ms</th>
                <th>p95 ms</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(latency).map(([k, v]) => (
                <tr key={k}>
                  <td>{k}</td>
                  <td className="num">{v.p50}</td>
                  <td className="num">{v.p95}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
