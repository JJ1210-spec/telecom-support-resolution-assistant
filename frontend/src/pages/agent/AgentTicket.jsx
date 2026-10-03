import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api/client";
import { Thread } from "../../components/Chat";
import { Icon } from "../../components/Icon";
import {
  ago,
  Cite,
  ErrorNote,
  Meter,
  pct,
  RouteBadge,
  SeverityBadge,
  Spinner,
  StatusBadge,
  Toast,
  useToast,
} from "../../components/ui";
import { useLiveEvents, useResource } from "../../hooks/useLive";
export default function AgentTicket() {
  const { id = "" } = useParams();
  const res = useResource(() => api.get(`/v1/agent/tickets/${id}`), [id]);
  const [busy, setBusy] = useState(false);
  const [toast, showToast] = useToast();
  const [draft, setDraft] = useState("");
  const [options, setOptions] = useState([]);
  const [plan, setPlan] = useState([]);
  const [resolveNote, setResolveNote] = useState("");
  const [copilotBusy, setCopilotBusy] = useState(false);
  const [contextTab, setContextTab] = useState("triage");
  const [workTab, setWorkTab] = useState("conversation");
  useLiveEvents((e) => {
    if (e.ticket_id === id) void res.reload();
  });
  const t = res.data;
  const act = async (fn, message) => {
    setBusy(true);
    try {
      res.setData(await fn());
      if (message) showToast(message);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Action failed");
    } finally {
      setBusy(false);
    }
  };
  if (!t) {
    return (
      <div style={{ display: "grid", placeItems: "center", flex: 1 }}>
        {res.loading ? <Spinner /> : <ErrorNote error={res.error} />}
      </div>
    );
  }
  const triage = t.triage;
  const decision = t.decision;
  const customerSteps = t.steps.filter((s) => s.customer_visible);
  const agentSteps = t.steps.filter((s) => !s.customer_visible);
  const stepChats = (stepId) => t.messages.filter((m) => m.step_id === stepId);
  const general = t.messages.filter((m) => !m.step_id);
  const open = !["resolved", "closed"].includes(t.status);
  const failed = customerSteps.filter((s) => s.status === "did_not_work").length;
  const sendMessage = (requestInfo, internal = false) =>
    act(
      async () => {
        const result = await api.post(`/v1/agent/tickets/${t.id}/messages`, {
          body: draft,
          options: requestInfo ? options : [],
          request_info: requestInfo,
          internal,
        });
        setDraft("");
        setOptions([]);
        return result;
      },
      internal ? "Internal note added" : requestInfo ? "Question sent — waiting for the customer" : "Reply sent",
    );
  const addToPlan = (text) => {
    setPlan((p) => [...p, text]);
    setWorkTab("resolve");
    showToast("Added to the solution plan");
  };
  return (
    <>
      <header className="ws-head" style={{ background: "var(--canvas)" }}>
        <div className="row-between" style={{ alignItems: "flex-start" }}>
          <div className="grow" style={{ minWidth: 0 }}>
            <div className="row caption" style={{ gap: 8 }}>
              <Link to="/console/queue" className="caption">
                <Icon name="back" size={14} /> Queue
              </Link>
              <span className="mono">{t.id}</span>
              {t.trace_id && (
                <a href={`/v1/agent/traces/${t.trace_id}`} target="_blank" rel="noreferrer" className="mono">
                  trace
                </a>
              )}
            </div>
            <h1
              className="title-lg"
              style={{ marginTop: 6, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            >
              {triage?.intent_label || t.subject}
            </h1>
            <div className="row caption" style={{ marginTop: 6, gap: 8 }}>
              <span>{t.customer?.name || t.customer?.email}</span>
              {t.region && <span>· {t.region}</span>}
              <span>· opened {ago(t.created_at)}</span>
              {t.sla_due_at && open && <span>· SLA {new Date(t.sla_due_at).toLocaleString()}</span>}
              {t.reopen_count > 0 && <span className="badge badge-red">Reopened {t.reopen_count}×</span>}
              {t.incident && (
                <Link to="/console/incidents" className="badge badge-amber">
                  Incident {t.incident.id}
                </Link>
              )}
            </div>
          </div>
          <div className="row">
            <StatusBadge status={t.status} label={t.status_label} />
            <RouteBadge route={t.route} />
            <SeverityBadge level={t.severity} />
            {!t.assignee_id && open && (
              <button
                className="btn btn-primary btn-sm"
                disabled={busy}
                onClick={() => act(() => api.post(`/v1/agent/tickets/${t.id}/claim`), "Assigned to you")}
              >
                Claim ticket
              </button>
            )}
            {t.assignee && <span className="caption">Assigned to {t.assignee.name || t.assignee.email}</span>}
          </div>
        </div>
      </header>
      {t.analysis_state === "running" && (
        <div className="ws-strip">
          <Spinner /> Analysis in progress…
        </div>
      )}

      <div className="agent-body">
        {/* ---------------- context ---------------- */}
        <section className="panel context soft">
          <div className="panel-head">
            <div className="tabs">
              {["triage", "routing", "customer"].map((k) => (
                <button key={k} className={`tab${contextTab === k ? " active" : ""}`} onClick={() => setContextTab(k)}>
                  {{ triage: "Triage", routing: "Routing", customer: "Customer" }[k]}
                </button>
              ))}
            </div>
          </div>
          <div className="panel-body stack">
            {contextTab === "triage" &&
              (triage ? (
                <>
                  <div>
                    <div className="title-sm">{triage.intent_label}</div>
                    <div className="caption mono">
                      {triage.intent} · {triage.product} · taxonomy v{triage.taxonomy_version}
                    </div>
                  </div>
                  <div className="stack-sm">
                    <ConfidenceRow label="Combined confidence" value={triage.confidence} />
                    <ConfidenceRow label="LLM" value={triage.llm_confidence} />
                    <ConfidenceRow label="Similar past tickets" value={triage.knn_agreement} />
                    {triage.clarify_agreement !== null && (
                      <ConfidenceRow label="Intake answers" value={triage.clarify_agreement} />
                    )}
                  </div>
                  {triage.evidence?.intent && <Quote text={triage.evidence.intent} />}
                  <div className="divider" />
                  <div className="row">
                    <SeverityBadge level={triage.severity} />
                    <span className="title-sm">Why this severity?</span>
                  </div>
                  <ul className="body-sm" style={{ margin: 0, paddingLeft: 18 }}>
                    {triage.severity_drivers.map((d) => (
                      <li key={d}>{d}</li>
                    ))}
                  </ul>
                  <div className="row">
                    <span className={`badge ${triage.sentiment === "negative" ? "badge-red" : ""}`}>
                      {triage.sentiment} {triage.sentiment_score?.toFixed(2)}
                    </span>
                    {triage.emotions?.map((e) => (
                      <span key={e} className="badge">
                        {e}
                      </span>
                    ))}
                    {triage.churn_risk && <span className="badge badge-red">Churn risk</span>}
                    {triage.prompt_injection && <span className="badge badge-red">Prompt injection</span>}
                  </div>
                  {(triage.entities?.actions_tried?.length ?? 0) > 0 && (
                    <div className="caption">Already tried: {triage.entities.actions_tried.join(", ")}</div>
                  )}
                  {triage.entities?.time_pattern && (
                    <div className="caption">Pattern: {triage.entities.time_pattern}</div>
                  )}
                  {triage.notes?.map((n) => (
                    <div key={n} className="caption">
                      ⚑ {n}
                    </div>
                  ))}
                </>
              ) : (
                <p className="caption">Triage not available yet.</p>
              ))}

            {contextTab === "routing" &&
              (decision ? (
                <>
                  <div className="row-between">
                    <span className="title-sm">Decision</span>
                    <RouteBadge route={decision.route} />
                  </div>
                  <ul className="body-sm" style={{ margin: 0, paddingLeft: 18 }}>
                    {decision.reasons.map((r) => (
                      <li key={r}>{r}</li>
                    ))}
                  </ul>
                  <dl className="kv" style={{ gridTemplateColumns: "150px minmax(0,1fr)" }}>
                    <dt>Recurrence</dt>
                    <dd className="num">{decision.recurrence}</dd>
                    <dt>Top similarity</dt>
                    <dd className="num">{decision.top_similarity}</dd>
                    <dt>Decision confidence</dt>
                    <dd className="num">{decision.decision_confidence}</dd>
                    <dt>Citation coverage</dt>
                    <dd className="num">{pct(decision.draft_meta?.citation_coverage)}</dd>
                  </dl>
                  {decision.draft_meta?.probable_root_cause && (
                    <div className="body-sm">
                      <strong>Probable cause:</strong> {decision.draft_meta.probable_root_cause.text}{" "}
                      {decision.draft_meta.probable_root_cause.citations.map((c) => (
                        <Cite key={c} id={c} />
                      ))}
                    </div>
                  )}
                  {decision.draft_meta?.escalate_if?.length ? (
                    <div className="body-sm">
                      <strong>Escalate if:</strong> {decision.draft_meta.escalate_if.join("; ")}
                    </div>
                  ) : null}
                  {decision.degraded && decision.degraded.length > 0 && (
                    <div className="caption" style={{ color: "var(--warn)" }}>
                      Degraded: {decision.degraded.join(", ")}
                    </div>
                  )}
                  {decision.warnings?.slice(0, 5).map((w) => (
                    <div key={w} className="caption">
                      • {w}
                    </div>
                  ))}
                </>
              ) : (
                <p className="caption">No routing decision yet.</p>
              ))}

            {contextTab === "customer" && (
              <>
                <dl className="kv" style={{ gridTemplateColumns: "100px minmax(0,1fr)" }}>
                  <dt>Name</dt>
                  <dd>{t.customer?.name || "—"}</dd>
                  <dt>Email</dt>
                  <dd>{t.customer?.email}</dd>
                  <dt>Area</dt>
                  <dd>{t.region ?? "—"}</dd>
                </dl>
                <div>
                  <div className="caption">Complaint</div>
                  <p className="body-sm" style={{ whiteSpace: "pre-wrap", marginTop: 4 }}>
                    {t.complaint}
                  </p>
                </div>
                {t.intake?.answers?.length ? (
                  <div className="stack-sm">
                    <div className="caption">Intake answers</div>
                    {t.intake.answers.map((a) => (
                      <div key={a.question_id} className="body-sm">
                        <div className="caption">{a.question}</div>
                        <div>
                          {a.answer?.join(", ") || a.text || "—"}{" "}
                          <span className="caption num">+{a.information_gain_bits} bits</span>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : null}
              </>
            )}
          </div>
        </section>

        {/* ---------------- work area ---------------- */}
        <section className="panel">
          <div className="panel-head">
            <div className="tabs">
              <button
                className={`tab${workTab === "conversation" ? " active" : ""}`}
                onClick={() => setWorkTab("conversation")}
              >
                Conversation<span className="count">{general.length}</span>
              </button>
              <button className={`tab${workTab === "steps" ? " active" : ""}`} onClick={() => setWorkTab("steps")}>
                Steps
                {failed ? (
                  <span className="count" style={{ background: "var(--down)", color: "#fff" }}>
                    {failed} failed
                  </span>
                ) : (
                  <span className="count">{t.steps.length}</span>
                )}
              </button>
              {open && (
                <button
                  className={`tab${workTab === "resolve" ? " active" : ""}`}
                  onClick={() => setWorkTab("resolve")}
                >
                  Solve{plan.length ? <span className="count">{plan.length}</span> : null}
                </button>
              )}
            </div>
          </div>

          {workTab === "conversation" && (
            <>
              <div className="panel-body">
                {general.length ? (
                  <Thread messages={general} viewer="agent" />
                ) : (
                  <p className="caption">No messages yet.</p>
                )}
              </div>
              {open && (
                <div className="panel-foot stack-sm">
                  <textarea
                    className="textarea"
                    style={{ minHeight: 72 }}
                    placeholder="Reply to the customer, or ask for details…"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                  />
                  <OptionsEditor options={options} setOptions={setOptions} />
                  <div className="row">
                    <button
                      className="btn btn-primary btn-sm"
                      disabled={busy || !draft.trim()}
                      onClick={() => sendMessage(options.length > 0)}
                    >
                      {options.length ? "Ask with quick replies" : "Send reply"}
                    </button>
                    <button
                      className="btn btn-secondary btn-sm"
                      disabled={busy || !draft.trim()}
                      onClick={() => sendMessage(true)}
                    >
                      Request info
                    </button>
                    <button
                      className="btn btn-text btn-sm"
                      disabled={busy || !draft.trim()}
                      onClick={() => sendMessage(false, true)}
                    >
                      Internal note
                    </button>
                  </div>
                </div>
              )}
            </>
          )}

          {workTab === "steps" && (
            <div className="panel-body stack">
              <div className="eyebrow">Customer step outcomes</div>
              {customerSteps.length === 0 && <p className="caption">No steps were shown to the customer.</p>}
              {customerSteps.map((s) => (
                <div key={s.id} style={{ borderBottom: "1px solid var(--hairline-soft)", paddingBottom: 12 }}>
                  <div className="row-between" style={{ alignItems: "flex-start" }}>
                    <span className="body-sm grow" style={{ fontWeight: 500 }}>
                      v{s.plan_version}.{s.position} {s.text}
                    </span>
                    <span
                      className={`badge ${s.status === "worked" ? "badge-green" : s.status === "did_not_work" ? "badge-red" : ""}`}
                    >
                      {s.status.replace(/_/g, " ")}
                    </span>
                  </div>
                  {s.status_note && <div className="caption">“{s.status_note}”</div>}
                  <div>
                    {s.citations?.map((c) => (
                      <Cite key={c} id={c} />
                    ))}
                  </div>
                  {stepChats(s.id).length > 0 && (
                    <details style={{ marginTop: 6 }}>
                      <summary className="caption" style={{ cursor: "pointer" }}>
                        Step chat ({stepChats(s.id).length})
                      </summary>
                      <div style={{ marginTop: 8 }}>
                        <Thread messages={stepChats(s.id)} viewer="agent" />
                      </div>
                    </details>
                  )}
                </div>
              ))}
              {agentSteps.length > 0 && (
                <>
                  <div className="eyebrow" style={{ marginTop: 8 }}>
                    AI-suggested agent checks
                  </div>
                  {agentSteps.map((s) => (
                    <div key={s.id} className="row-between body-sm" style={{ alignItems: "flex-start" }}>
                      <span className="grow">
                        {s.text}
                        <br />
                        {s.citations?.map((c) => (
                          <Cite key={c} id={c} />
                        ))}
                      </span>
                      {open && (
                        <button className="btn btn-xs btn-outline" onClick={() => addToPlan(s.text)}>
                          + plan
                        </button>
                      )}
                    </div>
                  ))}
                </>
              )}
            </div>
          )}

          {workTab === "resolve" && open && (
            <div className="panel-body stack">
              <div>
                <div className="title-sm">Propose a solution</div>
                <p className="caption">
                  The customer gets these steps as a checklist and confirms whether they worked. If not, the ticket
                  comes back to you.
                </p>
              </div>
              {plan.map((p, i) => (
                <div key={i} className="row">
                  <span className="step-num" style={{ width: 26, height: 26, fontSize: 12 }}>
                    {i + 1}
                  </span>
                  <input
                    className="input grow"
                    style={{ minHeight: 38, padding: "8px 12px" }}
                    value={p}
                    onChange={(e) => setPlan(plan.map((x, j) => (j === i ? e.target.value : x)))}
                  />
                  <button
                    className="btn btn-xs btn-secondary"
                    onClick={() => setPlan(plan.filter((_, j) => j !== i))}
                    aria-label="Remove"
                  >
                    <Icon name="x" size={12} />
                  </button>
                </div>
              ))}
              <div className="row">
                <button className="btn btn-xs btn-outline" onClick={() => setPlan([...plan, ""])}>
                  <Icon name="plus" size={12} /> Add step
                </button>
                <span className="caption">Tip: use “+ plan” on Copilot actions or agent checks.</span>
              </div>
              <textarea
                className="textarea"
                style={{ minHeight: 70 }}
                placeholder="Message to go with the solution (optional)"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
              />
              <button
                className="btn btn-sm btn-primary"
                style={{ alignSelf: "flex-start" }}
                disabled={busy || !plan.some((p) => p.trim())}
                onClick={() =>
                  act(async () => {
                    const r = await api.post(`/v1/agent/tickets/${t.id}/propose`, {
                      steps: plan.filter((p) => p.trim()),
                      message: draft,
                    });
                    setPlan([]);
                    setDraft("");
                    setWorkTab("conversation");
                    return r;
                  }, "Solution sent — the customer will confirm")
                }
              >
                Send to customer for confirmation
              </button>
              <div className="divider" />
              <div className="title-sm">Resolve directly</div>
              <textarea
                className="textarea"
                style={{ minHeight: 70 }}
                placeholder="Resolution note (required) — e.g. confirmed on call after line repair"
                value={resolveNote}
                onChange={(e) => setResolveNote(e.target.value)}
              />
              <button
                className="btn btn-sm btn-secondary"
                style={{ alignSelf: "flex-start" }}
                disabled={busy || resolveNote.trim().length < 3}
                onClick={() =>
                  act(
                    () => api.post(`/v1/agent/tickets/${t.id}/resolve`, { note: resolveNote }),
                    "Resolved — summary will be added to the knowledge base",
                  )
                }
              >
                Mark resolved
              </button>
            </div>
          )}
          {!open && workTab === "resolve" && (
            <div className="panel-body">
              <p className="caption">This ticket is resolved.</p>
            </div>
          )}

          {t.resolution_summary && workTab !== "conversation" && (
            <div className="panel-foot stack-sm" style={{ background: "var(--surface-soft)" }}>
              <div className="eyebrow">Learned into knowledge base</div>
              <div className="body-sm">
                <strong>{String(t.resolution_summary.title ?? "")}</strong> —{" "}
                {String(t.resolution_summary.root_cause ?? "")}
              </div>
              <div className="caption">
                Searchable case <span className="mono">{String(t.resolution_summary.learned_record ?? "")}</span>
                {t.resolution_summary.kb_draft ? (
                  <>
                    {" "}
                    · KB draft <Link to="/console/knowledge">{String(t.resolution_summary.kb_draft)}</Link>
                  </>
                ) : (
                  " · covered by existing KB"
                )}
              </div>
            </div>
          )}
        </section>

        {/* ---------------- copilot ---------------- */}
        <CopilotPanel
          copilot={t.copilot}
          busy={copilotBusy}
          canPlan={open}
          onRefresh={async () => {
            setCopilotBusy(true);
            try {
              const brief = await api.post(`/v1/agent/tickets/${t.id}/copilot`);
              res.setData({ ...t, copilot: brief });
            } catch (err) {
              showToast(err instanceof Error ? err.message : "Copilot failed");
            } finally {
              setCopilotBusy(false);
            }
          }}
          onUsePlan={addToPlan}
          onAsk={(q, opts) => {
            setDraft(q);
            setOptions(opts);
            setWorkTab("conversation");
          }}
          onReply={(text) => {
            setDraft(text);
            setWorkTab("conversation");
          }}
        />
      </div>
      <Toast message={toast} />
    </>
  );
}
function ConfidenceRow({ label, value }) {
  const v = value ?? 0;
  return (
    <div className="prob-bar" style={{ gridTemplateColumns: "minmax(0, 1fr) 90px 40px" }}>
      <span className="caption">{label}</span>
      <Meter value={v} tone={v < 0.4 ? "bad" : v < 0.6 ? "warn" : undefined} />
      <span className="num caption" style={{ textAlign: "right" }}>
        {pct(v)}
      </span>
    </div>
  );
}
function Quote({ text }) {
  return (
    <div className="body-sm" style={{ borderLeft: "3px solid var(--primary)", paddingLeft: 10, color: "var(--body)" }}>
      “{text}”
    </div>
  );
}
function OptionsEditor({ options, setOptions }) {
  const [value, setValue] = useState("");
  const add = () => {
    if (!value.trim() || options.length >= 6) return;
    setOptions([...options, value.trim()]);
    setValue("");
  };
  return (
    <div className="stack-sm">
      {options.length > 0 && (
        <div className="chips">
          {options.map((o) => (
            <span key={o} className="chip selected" style={{ minHeight: 32 }}>
              {o}
              <button
                style={{ border: 0, background: "none", cursor: "pointer", color: "inherit" }}
                onClick={() => setOptions(options.filter((x) => x !== o))}
                aria-label={`Remove ${o}`}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="row">
        <input
          className="input grow"
          style={{ minHeight: 38, padding: "8px 12px" }}
          placeholder="Optional quick-reply choice for the customer"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
        />
        <button className="btn btn-xs btn-outline" disabled={!value.trim() || options.length >= 6} onClick={add}>
          Add choice
        </button>
      </div>
    </div>
  );
}
function CopilotPanel({ copilot, busy, canPlan, onRefresh, onUsePlan, onAsk, onReply }) {
  return (
    <section className="panel dark" aria-label="Copilot">
      <div className="panel-head plain">
        <div className="row">
          <Icon name="spark" />
          <span className="title-sm">Copilot</span>
        </div>
        <button className="btn btn-xs btn-outline-dark" disabled={busy} onClick={onRefresh}>
          {busy ? (
            <Spinner light />
          ) : (
            <>
              <Icon name="refresh" size={13} /> Refresh
            </>
          )}
        </button>
      </div>
      <div className="panel-body stack">
        {!copilot ? (
          <p style={{ color: "var(--on-dark-soft)" }} className="body-sm">
            No brief yet. Copilot analyses the ticket history, what the customer tried and similar past incidents. Click
            Refresh.
          </p>
        ) : (
          <>
            <p className="body-sm" style={{ color: "#dfe2e6", margin: 0 }}>
              {copilot.summary}
            </p>
            {copilot.risk_flags?.length > 0 && (
              <div className="row">
                {copilot.risk_flags.map((f) => (
                  <span key={f} className="badge badge-red wrap">
                    {f}
                  </span>
                ))}
              </div>
            )}
            <Section title="Likely root causes">
              {copilot.likely_root_causes.map((c, i) => (
                <div key={i} className="body-sm">
                  <span
                    className={`badge ${c.likelihood === "high" ? "badge-red" : c.likelihood === "medium" ? "badge-amber" : ""}`}
                    style={{ marginRight: 6 }}
                  >
                    {c.likelihood}
                  </span>
                  {c.text}{" "}
                  <span>
                    {c.citations.map((id) => (
                      <Cite key={id} id={id} />
                    ))}
                  </span>
                </div>
              ))}
            </Section>
            <Section title="Next best actions">
              {copilot.next_actions.map((a, i) => (
                <div key={i} className="row-between body-sm" style={{ alignItems: "flex-start" }}>
                  <span className="grow">
                    <span className="caption" style={{ color: "var(--on-dark-soft)" }}>
                      {a.owner} ·{" "}
                    </span>
                    {a.text}
                    <br />
                    {a.citations.map((id) => (
                      <Cite key={id} id={id} />
                    ))}
                  </span>
                  {canPlan && (
                    <button className="btn btn-xs btn-dark" onClick={() => onUsePlan(a.text)}>
                      + plan
                    </button>
                  )}
                </div>
              ))}
            </Section>
            {copilot.clarifying_questions?.length > 0 && (
              <Section title="Ask the customer">
                {copilot.clarifying_questions.map((q, i) => (
                  <div key={i} className="row-between body-sm">
                    <span className="grow">
                      {q.text}{" "}
                      {q.options.length > 0 && (
                        <span className="caption" style={{ color: "var(--on-dark-soft)" }}>
                          ({q.options.join(" / ")})
                        </span>
                      )}
                    </span>
                    {canPlan && (
                      <button className="btn btn-xs btn-dark" onClick={() => onAsk(q.text, q.options)}>
                        Use
                      </button>
                    )}
                  </div>
                ))}
              </Section>
            )}
            {copilot.customer_reply_draft && (
              <Section title="Reply draft">
                <p className="body-sm" style={{ color: "#dfe2e6", margin: 0 }}>
                  {copilot.customer_reply_draft}
                </p>
                {canPlan && (
                  <button
                    className="btn btn-xs btn-dark"
                    style={{ alignSelf: "flex-start" }}
                    onClick={() => onReply(copilot.customer_reply_draft)}
                  >
                    Insert
                  </button>
                )}
              </Section>
            )}
            <Section title="Similar past incidents">
              {copilot.similar_incidents.map((s) => (
                <details key={s.id} className="body-sm">
                  <summary style={{ cursor: "pointer" }}>
                    <span className="mono" style={{ fontSize: 12 }}>
                      {s.id}
                    </span>{" "}
                    {s.title}{" "}
                    <span className="num caption" style={{ color: "var(--on-dark-soft)" }}>
                      {s.similarity?.toFixed(2)}
                    </span>
                    {s.origin === "learned" && (
                      <span className="badge badge-blue" style={{ marginLeft: 6 }}>
                        learned
                      </span>
                    )}
                  </summary>
                  <div style={{ color: "var(--on-dark-soft)", marginTop: 6 }}>
                    Root cause: {s.root_cause || "—"}
                    <ol style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                      {s.steps.map((x) => (
                        <li key={x}>{x}</li>
                      ))}
                    </ol>
                  </div>
                </details>
              ))}
            </Section>
            <div className="caption" style={{ color: "#6b7079" }}>
              {copilot.model ?? (copilot.degraded ? "fallback (LLM unavailable)" : "")} · {ago(copilot.generated_at)}
            </div>
          </>
        )}
      </div>
    </section>
  );
}
function Section({ title, children }) {
  return (
    <div className="stack-sm" style={{ borderTop: "1px solid var(--surface-dark-line)", paddingTop: 12 }}>
      <div className="eyebrow" style={{ color: "#8a8f98" }}>
        {title}
      </div>
      {children}
    </div>
  );
}
