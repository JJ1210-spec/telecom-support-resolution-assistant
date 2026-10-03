import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api/client";
import type { CustomerTicket, Step, TicketRow } from "../../api/types";
import { Composer, Thread } from "../../components/Chat";
import { Icon } from "../../components/Icon";
import { ago, Empty, ErrorNote, SeverityBadge, Spinner, StatusBadge, Toast, useToast } from "../../components/ui";
import { useLiveEvents, useResource } from "../../hooks/useLive";

export function MyTickets() {
  const list = useResource(() => api.get<{ tickets: TicketRow[] }>("/v1/tickets"), []);
  useLiveEvents(() => void list.reload());
  const tickets = list.data?.tickets ?? [];
  return (
    <div className="container page stack-lg">
      <div className="row-between">
        <div>
          <div className="eyebrow">Support</div>
          <h1 className="display-sm" style={{ marginTop: 6 }}>Your tickets</h1>
        </div>
        <Link to="/tickets/new" className="btn btn-primary">
          <Icon name="plus" size={18} /> Get help
        </Link>
      </div>
      {list.loading && <Spinner />}
      <ErrorNote error={list.error} />
      {!list.loading && !tickets.length && (
        <div className="card">
          <Empty title="No tickets yet">Tell us what's wrong and we'll get you sorted.</Empty>
        </div>
      )}
      <div className="stack-sm">
        {tickets.map((t) => (
          <Link key={t.id} to={`/tickets/${t.id}`} className="card-sm card-hover row-between" style={{ color: "inherit", textDecoration: "none" }}>
            <div className="grow">
              <div className="row">
                <span className="mono caption">{t.id}</span>
                <SeverityBadge level={t.severity} />
              </div>
              <div className="title-sm" style={{ marginTop: 6 }}>{t.intent_label || t.subject}</div>
              <div className="caption" style={{ marginTop: 2 }}>Updated {ago(t.updated_at)}</div>
            </div>
            <StatusBadge status={t.status} label={t.status_label} />
          </Link>
        ))}
      </div>
    </div>
  );
}

const STAGES = ["Received", "Diagnosed", "Being solved", "Confirm fix", "Resolved"];
function stageOf(status: string): number {
  return { analyzing: 0, self_service: 2, escalated: 1, in_progress: 2, awaiting_customer: 2, solution_proposed: 3, resolved: 4, closed: 4 }[status] ?? 0;
}
const PROGRESS_TEXT: Record<string, string> = {
  retrieving: "Searching thousands of past cases…",
  triaging: "Understanding your issue…",
  drafting: "Preparing grounded steps…",
};

export function TicketDetail() {
  const { id = "" } = useParams();
  const ticket = useResource(() => api.get<CustomerTicket>(`/v1/tickets/${id}`), [id]);
  const [stage, setStage] = useState<string | null>(null);
  const [chatStep, setChatStep] = useState<Step | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, showToast] = useToast();
  const [reopenOpen, setReopenOpen] = useState(false);

  useLiveEvents((event) => {
    if (event.ticket_id !== id) return;
    if (event.kind === "analysis_progress") setStage(String(event.stage));
    else void ticket.reload();
  });

  const t = ticket.data;
  const act = async (fn: () => Promise<CustomerTicket>, message?: string) => {
    setBusy(true);
    try {
      ticket.setData(await fn());
      if (message) showToast(message);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  };

  if (ticket.loading && !t) return <div className="container page"><Spinner /></div>;
  if (!t) return <div className="container page"><ErrorNote error={ticket.error ?? "Ticket not found"} /></div>;

  const analyzing = t.analysis_state === "running";
  const open = !["resolved", "closed"].includes(t.status);
  const anyWorked = t.steps.some((s) => s.status === "worked");
  const generalMessages = t.messages.filter((m) => !m.step_id);
  const current = stageOf(t.status);

  return (
    <div className="container page stack-lg">
      <Link to="/tickets" className="btn btn-text" style={{ alignSelf: "flex-start", color: "var(--body)" }}>
        <Icon name="back" size={16} /> All tickets
      </Link>

      <section className="status-hero">
        <div className="row-between">
          <span className="mono meta">{t.id}</span>
          <span className="row">
            {t.issue && <SeverityBadge level={t.issue.severity} />}
            <StatusBadge status={t.status} label={t.status_label} />
          </span>
        </div>
        <h1 className="display-sm" style={{ marginTop: 14 }}>{t.issue?.label || t.subject}</h1>
        <p className="meta" style={{ marginTop: 8 }}>
          Opened {ago(t.created_at)}
          {t.assignee ? ` · Handled by ${t.assignee}` : ""}
          {t.reopen_count ? ` · Reopened ${t.reopen_count}×` : ""}
        </p>
        <div className="progress-track">
          {STAGES.map((s, i) => <span key={s} className={i < current ? "done" : i === current ? "now" : ""} title={s} />)}
        </div>
        <div className="row-between meta" style={{ marginTop: 8, fontSize: 12 }}>
          {STAGES.map((s) => <span key={s}>{s}</span>)}
        </div>
      </section>

      {analyzing && (
        <div className="card row">
          <Spinner />
          <div>
            <div className="title-sm pulse">{PROGRESS_TEXT[stage ?? ""] ?? "Analyzing your request…"}</div>
            <div className="caption">This usually takes a few seconds. Your ticket is already saved and you'll get an email.</div>
          </div>
        </div>
      )}

      {t.incident && (
        <div className="alert alert-medium">
          <Icon name="radar" />
          <div>
            <strong>{t.incident.status === "resolved" ? "Area issue resolved" : "Known issue in your area"}</strong>
            <div>{t.incident.public_note}</div>
          </div>
        </div>
      )}

      <div className="grid-2" style={{ gridTemplateColumns: "minmax(0, 1.4fr) minmax(0, 1fr)", alignItems: "start" }}>
        <div className="stack-lg">
          {t.why && !analyzing && (
            <div className="card-flat">
              <div className="row">
                <span className="icon-plate blue"><Icon name={t.route === "self_service" ? "bolt" : "user"} /></span>
                <div className="grow">
                  <div className="title-sm">{t.route === "self_service" ? "Quick fix available" : t.route === "assisted" ? "Specialist + quick checks" : "With a specialist"}</div>
                  <div className="muted body-sm">{t.why}</div>
                </div>
              </div>
            </div>
          )}

          {t.steps.length > 0 && (
            <section className="card">
              <div className="row-between">
                <h2 className="title-md">{t.steps[0].origin === "agent" ? "Your specialist's solution" : "Try these steps"}</h2>
                <span className="caption">Tick each one as you go</span>
              </div>
              <div style={{ marginTop: 8 }}>
                {t.steps.map((s) => (
                  <StepRow
                    key={s.id}
                    step={s}
                    disabled={busy || !open}
                    chatCount={t.messages.filter((m) => m.step_id === s.id).length}
                    onStatus={(status) =>
                      act(() => api.post(`/v1/tickets/${t.id}/steps/${s.id}/feedback`, { status }),
                        status === "worked" ? "Great — glad that helped" : status === "did_not_work" ? "Thanks — noted for your specialist" : undefined)
                    }
                    onChat={() => setChatStep(s)}
                  />
                ))}
              </div>
              {open && anyWorked && (
                <div className="alert alert-info" style={{ marginTop: 16, alignItems: "center" }}>
                  <Icon name="check" />
                  <span className="grow">One of the steps worked. Is your issue completely solved?</span>
                  <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => act(() => api.post(`/v1/tickets/${t.id}/confirm`, { solved: true }), "Ticket resolved — thank you!")}>
                    Yes, it's solved
                  </button>
                </div>
              )}
            </section>
          )}

          <section className="card stack">
            <h2 className="title-md">Conversation</h2>
            {generalMessages.length ? (
              <Thread
                messages={generalMessages}
                viewer="customer"
                disabled={busy}
                onQuickReply={(option) => act(() => api.post(`/v1/tickets/${t.id}/messages`, { body: option, answered_option: option }))}
              />
            ) : (
              <p className="muted body-sm">No messages yet.</p>
            )}
            {t.status !== "closed" && (
              <Composer busy={busy} placeholder="Message support…" onSend={(body) => act(() => api.post(`/v1/tickets/${t.id}/messages`, { body }), "Message sent")} />
            )}
          </section>
        </div>

        <aside className="stack-lg">
          {open && !analyzing && (
            <div className="card stack">
              <h2 className="title-md">Is it fixed?</h2>
              <button className="btn btn-primary btn-block" disabled={busy} onClick={() => act(() => api.post(`/v1/tickets/${t.id}/confirm`, { solved: true }), "Ticket resolved — thank you!")}>
                Yes, my issue is solved
              </button>
              {["self_service", "solution_proposed", "awaiting_customer"].includes(t.status) && (
                <button className="btn btn-secondary btn-block" disabled={busy} onClick={() => setReopenOpen(true)}>
                  No, it's still not working
                </button>
              )}
              {t.status === "self_service" && (
                <button className="btn btn-text" disabled={busy} onClick={() => act(() => api.post(`/v1/tickets/${t.id}/escalate`, { text: "Customer asked for a specialist" }), "A specialist will take it from here")}>
                  Talk to a specialist instead
                </button>
              )}
              <p className="caption">Your ticket stays open until you confirm it's solved.</p>
            </div>
          )}

          {!open && (
            <div className="card stack">
              <span className="badge badge-green" style={{ alignSelf: "flex-start" }}>Resolved {ago(t.resolved_at)}</span>
              {t.resolution?.root_cause && (
                <div>
                  <div className="caption">What caused it</div>
                  <div>{t.resolution.root_cause}</div>
                </div>
              )}
              <Rating ticket={t} onRate={(rating) => act(() => api.post(`/v1/tickets/${t.id}/feedback`, { rating }), "Thanks for the feedback")} />
              {t.status === "resolved" && (
                <button className="btn btn-secondary" disabled={busy} onClick={() => setReopenOpen(true)}>
                  Problem came back? Reopen
                </button>
              )}
            </div>
          )}

          <div className="card stack">
            <h2 className="title-md">Timeline</h2>
            <div className="timeline">
              {t.events.map((e, i) => (
                <div key={i} className={`tl-item${["created", "resolved", "reopened", "escalated"].includes(e.kind) ? " key" : ""}`}>
                  <div>{describeEvent(e.kind, e.detail)}</div>
                  <div className="caption">{ago(e.created_at)}</div>
                </div>
              ))}
            </div>
          </div>
          <details className="card-sm">
            <summary className="title-sm" style={{ cursor: "pointer" }}>Your original message</summary>
            <p className="muted body-sm" style={{ whiteSpace: "pre-wrap", marginTop: 12 }}>{t.complaint}</p>
          </details>
        </aside>
      </div>

      {chatStep && <StepChatDrawer ticket={t} step={chatStep} onClose={() => setChatStep(null)} onUpdate={(next) => ticket.setData(next)} />}
      {reopenOpen && (
        <ReopenDialog
          busy={busy}
          onCancel={() => setReopenOpen(false)}
          onSubmit={async (note) => {
            setReopenOpen(false);
            await act(() => api.post(`/v1/tickets/${t.id}/confirm`, { solved: false, note }), "Sent back to a specialist");
          }}
        />
      )}
      <Toast message={toast} />
    </div>
  );
}

function StepRow({ step, disabled, chatCount, onStatus, onChat }: { step: Step; disabled: boolean; chatCount: number; onStatus: (s: Step["status"]) => void; onChat: () => void }) {
  return (
    <div className={`step ${step.status}`}>
      <span className="step-num">{step.status === "worked" ? <Icon name="check" size={16} stroke={2.5} /> : step.status === "did_not_work" ? <Icon name="x" size={14} stroke={2.5} /> : step.position}</span>
      <div className="stack-sm">
        <div style={{ fontWeight: 500 }}>{step.text}</div>
        {step.detail && <div className="muted body-sm">{step.detail}</div>}
        <div className="row" style={{ marginTop: 4 }}>
          <div className="seg" role="group" aria-label="Did this step work?">
            <button className={step.status === "worked" ? "on-worked" : ""} disabled={disabled} onClick={() => onStatus(step.status === "worked" ? "pending" : "worked")}>
              <Icon name="check" size={14} /> Tried — worked
            </button>
            <button className={step.status === "did_not_work" ? "on-did_not_work" : ""} disabled={disabled} onClick={() => onStatus(step.status === "did_not_work" ? "pending" : "did_not_work")}>
              <Icon name="x" size={13} /> Tried — didn't work
            </button>
          </div>
          <button className="btn btn-xs btn-outline" onClick={onChat}>
            <Icon name="chat" size={14} /> Ask about this step{chatCount ? ` (${chatCount})` : ""}
          </button>
        </div>
      </div>
    </div>
  );
}

function StepChatDrawer({ ticket, step, onClose, onUpdate }: { ticket: CustomerTicket; step: Step; onClose: () => void; onUpdate: (t: CustomerTicket) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const messages = ticket.messages.filter((m) => m.step_id === step.id);
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label={`Help with step ${step.position}`}>
        <div className="drawer-head row-between">
          <div className="grow">
            <div className="eyebrow">Step {step.position}</div>
            <div className="title-sm" style={{ marginTop: 4 }}>{step.text}</div>
          </div>
          <button className="btn btn-secondary btn-sm" onClick={onClose} aria-label="Close"><Icon name="x" size={16} /></button>
        </div>
        <div className="drawer-body">
          {messages.length ? (
            <Thread messages={messages} viewer="customer" />
          ) : (
            <div className="stack-sm">
              <p className="muted body-sm">Stuck on this step? Ask anything about it — where a button is, what a light means, or what to do next.</p>
              <div className="chips">
                {["Where do I find this?", "What should I see if it worked?", "I can't do this step"].map((q) => (
                  <button key={q} className="chip" disabled={busy} onClick={async () => {
                    setBusy(true);
                    try { onUpdate(await api.post<CustomerTicket>(`/v1/tickets/${ticket.id}/steps/${step.id}/chat`, { text: q })); } catch (err) { setError(err instanceof Error ? err.message : "Failed"); } finally { setBusy(false); }
                  }}>{q}</button>
                ))}
              </div>
            </div>
          )}
          {busy && <div style={{ marginTop: 12 }}><Spinner /></div>}
          <ErrorNote error={error} />
        </div>
        <div className="drawer-foot">
          <Composer
            busy={busy}
            placeholder="Ask about this step…"
            onSend={async (text) => {
              setBusy(true);
              setError(null);
              try {
                onUpdate(await api.post<CustomerTicket>(`/v1/tickets/${ticket.id}/steps/${step.id}/chat`, { text }));
              } catch (err) {
                setError(err instanceof Error ? err.message : "Failed to send");
              } finally {
                setBusy(false);
              }
            }}
          />
        </div>
      </aside>
    </>
  );
}

function ReopenDialog({ busy, onCancel, onSubmit }: { busy: boolean; onCancel: () => void; onSubmit: (note: string) => void }) {
  const [note, setNote] = useState("");
  return (
    <>
      <div className="drawer-backdrop" onClick={onCancel} />
      <aside className="drawer" role="dialog" aria-label="Still not working">
        <div className="drawer-head"><div className="title-md">Sorry it's still not working</div></div>
        <div className="drawer-body stack">
          <p className="muted body-sm">Tell us what happened when you tried the fix. Your ticket goes straight back to a specialist with everything you've already tried.</p>
          <textarea className="textarea" autoFocus value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. It worked for an hour, then dropped again at 8 pm" />
        </div>
        <div className="drawer-foot row">
          <button className="btn btn-primary" disabled={busy} onClick={() => onSubmit(note)}>Send back to support</button>
          <button className="btn btn-secondary" onClick={onCancel}>Cancel</button>
        </div>
      </aside>
    </>
  );
}

function Rating({ ticket, onRate }: { ticket: CustomerTicket; onRate: (rating: number) => void }) {
  const current = ticket.feedback?.rating ?? 0;
  return (
    <div>
      <div className="caption">How did we do?</div>
      <div className="row" style={{ marginTop: 6 }}>
        {[1, 2, 3, 4, 5].map((n) => (
          <button key={n} className={`chip${n <= current ? " selected" : ""}`} style={{ minWidth: 44, justifyContent: "center" }} onClick={() => onRate(n)} aria-label={`${n} stars`}>
            {n}
          </button>
        ))}
      </div>
    </div>
  );
}

function describeEvent(kind: string, detail: Record<string, unknown> | null): string {
  const d = detail ?? {};
  switch (kind) {
    case "created": return "Ticket received";
    case "analyzed": return `Diagnosed as “${d.intent ?? "your issue"}”`;
    case "status_changed": return `Status: ${String(d.to ?? "").replace(/_/g, " ")}${d.reason ? ` — ${d.reason}` : ""}`;
    case "step_feedback": return `Step ${d.step}: ${d.status === "worked" ? "worked" : d.status === "did_not_work" ? "didn't work" : "reset"}`;
    case "assigned": return "A specialist picked up your ticket";
    case "info_requested": return "Support asked for more information";
    case "solution_proposed": return "A solution was proposed";
    case "reopened": return "You reopened the ticket";
    case "resolved": return "Resolved";
    case "escalated": return "Sent to a specialist";
    case "incident_linked": return "Linked to a known area issue";
    default: return kind.replace(/_/g, " ");
  }
}
