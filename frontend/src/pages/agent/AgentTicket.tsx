import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api/client";
import type { AgentTicket as Ticket, Copilot, Message } from "../../api/types";
import { Thread } from "../../components/Chat";
import { Icon } from "../../components/Icon";
import { ConsoleHead } from "../../components/Layout";
import { ago, Cite, ErrorNote, Meter, pct, RouteBadge, SeverityBadge, Spinner, StatusBadge, Toast, useToast } from "../../components/ui";
import { useLiveEvents, useResource } from "../../hooks/useLive";

export default function AgentTicket() {
  const { id = "" } = useParams();
  const res = useResource(() => api.get<Ticket>(`/v1/agent/tickets/${id}`), [id]);
  const [busy, setBusy] = useState(false);
  const [toast, showToast] = useToast();
  const [draft, setDraft] = useState("");
  const [options, setOptions] = useState<string[]>([]);
  const [plan, setPlan] = useState<string[]>([]);
  const [resolveNote, setResolveNote] = useState("");
  const [copilotBusy, setCopilotBusy] = useState(false);
  useLiveEvents((e) => { if (e.ticket_id === id) void res.reload(); });

  const t = res.data;
  const act = async (fn: () => Promise<Ticket>, message?: string) => {
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
  if (res.loading && !t) return <Spinner />;
  if (!t) return <ErrorNote error={res.error} />;

  const triage = t.triage;
  const decision = t.decision;
  const customerSteps = t.steps.filter((s) => s.customer_visible);
  const agentSteps = t.steps.filter((s) => !s.customer_visible);
  const stepChats = (stepId: string) => t.messages.filter((m) => m.step_id === stepId);
  const general = t.messages.filter((m) => !m.step_id);
  const open = !["resolved", "closed"].includes(t.status);

  const sendMessage = (requestInfo: boolean, internal = false) =>
    act(async () => {
      const result = await api.post<Ticket>(`/v1/agent/tickets/${t.id}/messages`, { body: draft, options: requestInfo ? options : [], request_info: requestInfo, internal });
      setDraft("");
      setOptions([]);
      return result;
    }, internal ? "Internal note added" : requestInfo ? "Question sent — waiting for the customer" : "Reply sent");

  return (
    <div className="stack-lg">
      <ConsoleHead eyebrow={`Ticket ${t.id}`} title={triage?.intent_label || t.subject}>
        <StatusBadge status={t.status} label={t.status_label} />
        <RouteBadge route={t.route} />
        <SeverityBadge level={t.severity} />
        {!t.assignee_id && open && (
          <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => act(() => api.post(`/v1/agent/tickets/${t.id}/claim`), "Assigned to you")}>
            Claim ticket
          </button>
        )}
      </ConsoleHead>

      {t.analysis_state === "running" && <div className="card row"><Spinner /> Analysis in progress…</div>}

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1.35fr) minmax(0, 1.1fr)", gap: 20, alignItems: "start" }} className="agent-grid">
        {/* ---------------- left: context & explainability ---------------- */}
        <div className="stack">
          <div className="card-sm stack-sm">
            <div className="eyebrow">Customer</div>
            <div className="title-sm">{t.customer?.name || t.customer?.email}</div>
            <div className="caption">{t.customer?.email} {t.region ? `· ${t.region}` : ""}</div>
            <div className="caption">Opened {ago(t.created_at)} · SLA due {t.sla_due_at ? new Date(t.sla_due_at).toLocaleString() : "—"}</div>
            {t.reopen_count > 0 && <span className="badge badge-red" style={{ alignSelf: "flex-start" }}>Reopened {t.reopen_count}×</span>}
            {t.incident && <Link to="/console/incidents" className="badge badge-amber" style={{ alignSelf: "flex-start" }}>Incident {t.incident.id}</Link>}
            <details>
              <summary className="caption" style={{ cursor: "pointer" }}>Complaint</summary>
              <p className="body-sm" style={{ whiteSpace: "pre-wrap" }}>{t.complaint}</p>
            </details>
          </div>

          {triage && (
            <div className="card-sm stack-sm">
              <div className="row-between">
                <div className="eyebrow">Triage</div>
                <span className="caption">taxonomy v{triage.taxonomy_version}</span>
              </div>
              <div className="title-sm">{triage.intent_label}</div>
              <div className="caption mono">{triage.intent} · {triage.product}</div>
              <ConfidenceRow label="Combined confidence" value={triage.confidence} />
              <ConfidenceRow label="LLM" value={triage.llm_confidence} />
              <ConfidenceRow label="Similar past tickets agree" value={triage.knn_agreement} />
              {triage.clarify_agreement !== null && <ConfidenceRow label="Customer's intake answers" value={triage.clarify_agreement} />}
              {triage.evidence?.intent && <Quote text={triage.evidence.intent} />}
              <div className="divider" />
              <div className="row"><SeverityBadge level={triage.severity} /><span className="caption">Why this severity?</span></div>
              <ul className="body-sm" style={{ margin: 0, paddingLeft: 18 }}>
                {triage.severity_drivers.map((d) => <li key={d}>{d}</li>)}
              </ul>
              <div className="row">
                <span className={`badge ${triage.sentiment === "negative" ? "badge-red" : ""}`}>{triage.sentiment} {triage.sentiment_score?.toFixed(2)}</span>
                {triage.emotions?.map((e) => <span key={e} className="badge">{e}</span>)}
                {triage.churn_risk && <span className="badge badge-red">Churn risk</span>}
                {triage.prompt_injection && <span className="badge badge-red">Prompt injection</span>}
              </div>
              {triage.entities?.actions_tried?.length > 0 && <div className="caption">Already tried: {triage.entities.actions_tried.join(", ")}</div>}
              {triage.entities?.time_pattern && <div className="caption">Pattern: {triage.entities.time_pattern}</div>}
              {triage.notes?.map((n) => <div key={n} className="caption">⚑ {n}</div>)}
            </div>
          )}

          {decision && (
            <div className="card-sm stack-sm">
              <div className="row-between"><div className="eyebrow">Routing</div><RouteBadge route={decision.route} /></div>
              <ul className="body-sm" style={{ margin: 0, paddingLeft: 18 }}>
                {decision.reasons.map((r) => <li key={r}>{r}</li>)}
              </ul>
              <div className="caption">Recurrence {decision.recurrence} · top similarity <span className="num">{decision.top_similarity}</span> · decision confidence <span className="num">{decision.decision_confidence}</span></div>
              {decision.draft_meta?.probable_root_cause && (
                <div className="body-sm">
                  <strong>Probable cause:</strong> {decision.draft_meta.probable_root_cause.text}{" "}
                  {decision.draft_meta.probable_root_cause.citations.map((c) => <Cite key={c} id={c} />)}
                </div>
              )}
              {decision.degraded && decision.degraded.length > 0 && <div className="caption" style={{ color: "var(--warn)" }}>Degraded: {decision.degraded.join(", ")}</div>}
              {decision.warnings?.slice(0, 4).map((w) => <div key={w} className="caption">• {w}</div>)}
              {t.trace_id && <a className="caption" href={`/v1/agent/traces/${t.trace_id}`} target="_blank" rel="noreferrer">Trace {t.trace_id}</a>}
            </div>
          )}

          {t.intake?.answers?.length ? (
            <div className="card-sm stack-sm">
              <div className="eyebrow">Intake answers</div>
              {t.intake.answers.map((a) => (
                <div key={a.question_id} className="body-sm">
                  <div className="caption">{a.question}</div>
                  <div>{a.answer?.join(", ") || a.text || "—"} <span className="caption num">+{a.information_gain_bits} bits</span></div>
                </div>
              ))}
            </div>
          ) : null}
        </div>

        {/* ---------------- middle: steps, conversation, actions ---------------- */}
        <div className="stack">
          {customerSteps.length > 0 && (
            <div className="card-sm stack-sm">
              <div className="eyebrow">Customer step outcomes</div>
              {customerSteps.map((s) => (
                <div key={s.id} style={{ borderTop: "1px solid var(--hairline-soft)", paddingTop: 10 }}>
                  <div className="row-between">
                    <span className="body-sm" style={{ fontWeight: 500 }}>v{s.plan_version}.{s.position} {s.text}</span>
                    <span className={`badge ${s.status === "worked" ? "badge-green" : s.status === "did_not_work" ? "badge-red" : ""}`}>{s.status.replace(/_/g, " ")}</span>
                  </div>
                  {s.status_note && <div className="caption">“{s.status_note}”</div>}
                  <div>{s.citations?.map((c) => <Cite key={c} id={c} />)}</div>
                  {stepChats(s.id).length > 0 && (
                    <details style={{ marginTop: 6 }}>
                      <summary className="caption" style={{ cursor: "pointer" }}>Step chat ({stepChats(s.id).length})</summary>
                      <div style={{ marginTop: 8 }}><Thread messages={stepChats(s.id)} viewer="agent" /></div>
                    </details>
                  )}
                </div>
              ))}
            </div>
          )}

          {agentSteps.length > 0 && (
            <div className="card-sm stack-sm">
              <div className="eyebrow">AI-suggested agent checks</div>
              {agentSteps.map((s) => (
                <div key={s.id} className="row-between body-sm" style={{ alignItems: "flex-start" }}>
                  <span className="grow">{s.text}<br />{s.citations?.map((c) => <Cite key={c} id={c} />)}</span>
                  <button className="btn btn-xs btn-outline" onClick={() => setPlan((p) => [...p, s.text])}>+ plan</button>
                </div>
              ))}
            </div>
          )}

          <div className="card-sm stack">
            <div className="eyebrow">Conversation</div>
            <Thread messages={general as Message[]} viewer="agent" />
            {open && (
              <div className="stack-sm">
                <textarea className="textarea" style={{ minHeight: 90 }} placeholder="Reply to the customer, or ask for details…" value={draft} onChange={(e) => setDraft(e.target.value)} />
                <OptionsEditor options={options} setOptions={setOptions} />
                <div className="row">
                  <button className="btn btn-primary btn-sm" disabled={busy || !draft.trim()} onClick={() => sendMessage(options.length > 0)}>
                    {options.length ? "Ask with quick replies" : "Send reply"}
                  </button>
                  <button className="btn btn-secondary btn-sm" disabled={busy || !draft.trim()} onClick={() => sendMessage(true)}>Request info</button>
                  <button className="btn btn-text btn-sm" disabled={busy || !draft.trim()} onClick={() => sendMessage(false, true)}>Internal note</button>
                </div>
              </div>
            )}
          </div>

          {open && (
            <div className="card-sm stack-sm">
              <div className="eyebrow">Propose a solution</div>
              {plan.map((p, i) => (
                <div key={i} className="row">
                  <span className="step-num" style={{ width: 26, height: 26, fontSize: 12 }}>{i + 1}</span>
                  <input className="input grow" style={{ minHeight: 38, padding: "8px 12px" }} value={p} onChange={(e) => setPlan(plan.map((x, j) => (j === i ? e.target.value : x)))} />
                  <button className="btn btn-xs btn-secondary" onClick={() => setPlan(plan.filter((_, j) => j !== i))} aria-label="Remove"><Icon name="x" size={12} /></button>
                </div>
              ))}
              <div className="row">
                <button className="btn btn-xs btn-outline" onClick={() => setPlan([...plan, ""])}><Icon name="plus" size={12} /> Add step</button>
                <button className="btn btn-sm btn-primary" disabled={busy || !plan.some((p) => p.trim())} onClick={() =>
                  act(async () => {
                    const r = await api.post<Ticket>(`/v1/agent/tickets/${t.id}/propose`, { steps: plan.filter((p) => p.trim()), message: draft });
                    setPlan([]);
                    setDraft("");
                    return r;
                  }, "Solution sent — the customer will confirm")}>
                  Send to customer for confirmation
                </button>
              </div>
              <div className="divider" />
              <div className="eyebrow">Resolve directly</div>
              <textarea className="textarea" style={{ minHeight: 70 }} placeholder="Resolution note (required) — e.g. confirmed on call after line repair" value={resolveNote} onChange={(e) => setResolveNote(e.target.value)} />
              <button className="btn btn-sm btn-secondary" style={{ alignSelf: "flex-start" }} disabled={busy || resolveNote.trim().length < 3} onClick={() =>
                act(() => api.post(`/v1/agent/tickets/${t.id}/resolve`, { note: resolveNote }), "Resolved — summary will be added to the knowledge base")}>
                Mark resolved
              </button>
            </div>
          )}

          {t.resolution_summary && (
            <div className="card-sm stack-sm">
              <div className="eyebrow">Learned into knowledge base</div>
              <div className="title-sm">{String(t.resolution_summary.title ?? "")}</div>
              <div className="body-sm">Root cause: {String(t.resolution_summary.root_cause ?? "")}</div>
              <div className="caption">Searchable case <span className="mono">{String(t.resolution_summary.learned_record ?? "")}</span>
                {t.resolution_summary.kb_draft ? <> · KB draft <Link to="/console/knowledge">{String(t.resolution_summary.kb_draft)}</Link></> : " · covered by existing KB"}</div>
            </div>
          )}
        </div>

        {/* ---------------- right: copilot ---------------- */}
        <CopilotPanel
          copilot={t.copilot}
          busy={copilotBusy}
          onRefresh={async () => {
            setCopilotBusy(true);
            try {
              const brief = await api.post<Copilot>(`/v1/agent/tickets/${t.id}/copilot`);
              res.setData({ ...t, copilot: brief });
            } catch (err) {
              showToast(err instanceof Error ? err.message : "Copilot failed");
            } finally {
              setCopilotBusy(false);
            }
          }}
          onUsePlan={(text) => setPlan((p) => [...p, text])}
          onAsk={(q, opts) => { setDraft(q); setOptions(opts); }}
          onReply={(text) => setDraft(text)}
        />
      </div>
      <Toast message={toast} />
      <style>{`@media (max-width: 1280px) { .agent-grid { grid-template-columns: 1fr 1fr !important; } } @media (max-width: 900px) { .agent-grid { grid-template-columns: 1fr !important; } }`}</style>
    </div>
  );
}

function ConfidenceRow({ label, value }: { label: string; value: number | null | undefined }) {
  const v = value ?? 0;
  return (
    <div className="prob-bar" style={{ gridTemplateColumns: "minmax(0, 1fr) 90px 40px" }}>
      <span className="caption">{label}</span>
      <Meter value={v} tone={v < 0.4 ? "bad" : v < 0.6 ? "warn" : undefined} />
      <span className="num caption" style={{ textAlign: "right" }}>{pct(v)}</span>
    </div>
  );
}

function Quote({ text }: { text: string }) {
  return <div className="body-sm" style={{ borderLeft: "3px solid var(--primary)", paddingLeft: 10, color: "var(--body)" }}>“{text}”</div>;
}

function OptionsEditor({ options, setOptions }: { options: string[]; setOptions: (o: string[]) => void }) {
  const [value, setValue] = useState("");
  return (
    <div className="stack-sm">
      <div className="chips">
        {options.map((o) => (
          <span key={o} className="chip selected" style={{ minHeight: 32 }}>
            {o}
            <button className="btn-text" style={{ border: 0, background: "none", cursor: "pointer" }} onClick={() => setOptions(options.filter((x) => x !== o))} aria-label={`Remove ${o}`}>×</button>
          </span>
        ))}
      </div>
      <div className="row">
        <input className="input grow" style={{ minHeight: 38, padding: "8px 12px" }} placeholder="Add a quick-reply choice for the customer (optional)" value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && value.trim()) { e.preventDefault(); setOptions([...options, value.trim()]); setValue(""); } }} />
        <button className="btn btn-xs btn-outline" disabled={!value.trim() || options.length >= 6} onClick={() => { setOptions([...options, value.trim()]); setValue(""); }}>Add</button>
      </div>
    </div>
  );
}

function CopilotPanel({ copilot, busy, onRefresh, onUsePlan, onAsk, onReply }: {
  copilot: Copilot | null; busy: boolean; onRefresh: () => void; onUsePlan: (t: string) => void; onAsk: (q: string, opts: string[]) => void; onReply: (t: string) => void;
}) {
  return (
    <aside className="card-dark stack" style={{ padding: 22 }}>
      <div className="row-between">
        <div className="row"><Icon name="spark" /><span className="title-sm">Copilot</span></div>
        <button className="btn btn-xs btn-outline-dark" disabled={busy} onClick={onRefresh}>{busy ? <Spinner light /> : <><Icon name="refresh" size={13} /> Refresh</>}</button>
      </div>
      {!copilot ? (
        <p style={{ color: "var(--on-dark-soft)" }} className="body-sm">No brief yet. Copilot analyses the ticket history, what the customer tried and similar past incidents.</p>
      ) : (
        <>
          <p className="body-sm" style={{ color: "#dfe2e6" }}>{copilot.summary}</p>
          {copilot.risk_flags?.length > 0 && <div className="row">{copilot.risk_flags.map((f) => <span key={f} className="badge badge-red">{f}</span>)}</div>}
          <Section title="Likely root causes">
            {copilot.likely_root_causes.map((c, i) => (
              <div key={i} className="body-sm">
                <span className={`badge ${c.likelihood === "high" ? "badge-red" : c.likelihood === "medium" ? "badge-amber" : ""}`} style={{ marginRight: 6 }}>{c.likelihood}</span>
                {c.text} <span>{c.citations.map((id) => <Cite key={id} id={id} />)}</span>
              </div>
            ))}
          </Section>
          <Section title="Next best actions">
            {copilot.next_actions.map((a, i) => (
              <div key={i} className="row-between body-sm" style={{ alignItems: "flex-start" }}>
                <span className="grow"><span className="caption" style={{ color: "var(--on-dark-soft)" }}>{a.owner} · </span>{a.text}<br />{a.citations.map((id) => <Cite key={id} id={id} />)}</span>
                <button className="btn btn-xs btn-dark" onClick={() => onUsePlan(a.text)}>+ plan</button>
              </div>
            ))}
          </Section>
          {copilot.clarifying_questions?.length > 0 && (
            <Section title="Ask the customer">
              {copilot.clarifying_questions.map((q, i) => (
                <div key={i} className="row-between body-sm">
                  <span className="grow">{q.text} {q.options.length > 0 && <span className="caption" style={{ color: "var(--on-dark-soft)" }}>({q.options.join(" / ")})</span>}</span>
                  <button className="btn btn-xs btn-dark" onClick={() => onAsk(q.text, q.options)}>Use</button>
                </div>
              ))}
            </Section>
          )}
          {copilot.customer_reply_draft && (
            <Section title="Reply draft">
              <p className="body-sm" style={{ color: "#dfe2e6", margin: 0 }}>{copilot.customer_reply_draft}</p>
              <button className="btn btn-xs btn-dark" style={{ alignSelf: "flex-start" }} onClick={() => onReply(copilot.customer_reply_draft)}>Insert</button>
            </Section>
          )}
          <Section title="Similar past incidents">
            {copilot.similar_incidents.map((s) => (
              <details key={s.id} className="body-sm">
                <summary style={{ cursor: "pointer" }}>
                  <span className="mono" style={{ fontSize: 12 }}>{s.id}</span> {s.title} <span className="num caption" style={{ color: "var(--on-dark-soft)" }}>{s.similarity?.toFixed(2)}</span>
                  {s.origin === "learned" && <span className="badge badge-blue" style={{ marginLeft: 6 }}>learned</span>}
                </summary>
                <div style={{ color: "var(--on-dark-soft)", marginTop: 6 }}>
                  Root cause: {s.root_cause || "—"}
                  <ol style={{ margin: "6px 0 0", paddingLeft: 18 }}>{s.steps.map((x) => <li key={x}>{x}</li>)}</ol>
                </div>
              </details>
            ))}
          </Section>
          <div className="caption" style={{ color: "#6b7079" }}>{copilot.model ?? (copilot.degraded ? "fallback (LLM unavailable)" : "")} · {ago(copilot.generated_at)}</div>
        </>
      )}
    </aside>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="stack-sm" style={{ borderTop: "1px solid var(--surface-dark-line)", paddingTop: 12 }}>
      <div className="eyebrow" style={{ color: "#8a8f98" }}>{title}</div>
      {children}
    </div>
  );
}
