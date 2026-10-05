import { useState } from "react";
import { Link, NavLink, Outlet, useParams } from "react-router-dom";
import { api } from "../../api/client";
import { Composer, Thread } from "../../components/Chat";
import { Icon } from "../../components/Icon";
import { ago, Empty, ErrorNote, SeverityBadge, Spinner, StatusBadge, Toast, useToast } from "../../components/ui";
import { useLiveEvents, useResource } from "../../hooks/useLive";
/* ------------------------------------------------------------------ inbox shell (list rail + workspace) */
export function CustomerInbox() {
  const { id } = useParams();
  const list = useResource(() => api.get("/v1/tickets"), []);
  const [filter, setFilter] = useState("open");
  useLiveEvents(() => void list.reload());
  const tickets = (list.data?.tickets ?? []).filter(
    (t) => filter === "all" || !["resolved", "closed"].includes(t.status),
  );
  return (
    <div className={`split${id ? " has-selection" : ""}`}>
      <aside className="rail" aria-label="Your tickets">
        <div className="rail-head">
          <div className="row-between">
            <h1 className="title-md">Your tickets</h1>
            <Link to="/tickets/new" className="btn btn-primary btn-sm">
              <Icon name="plus" size={16} /> New
            </Link>
          </div>
          <div className="seg" role="tablist" aria-label="Filter tickets">
            <button className={filter === "open" ? "on-worked" : ""} onClick={() => setFilter("open")}>
              Open
            </button>
            <button className={filter === "all" ? "on-worked" : ""} onClick={() => setFilter("all")}>
              All
            </button>
          </div>
        </div>
        <div className="rail-list">
          {list.loading && !list.data && (
            <div style={{ padding: 16 }}>
              <Spinner />
            </div>
          )}
          <ErrorNote error={list.error} />
          {!list.loading && tickets.length === 0 && (
            <p className="caption" style={{ padding: "12px 14px" }}>
              {filter === "open" ? "No open tickets." : "No tickets yet."}
            </p>
          )}
          {tickets.map((t) => (
            <NavLink
              key={t.id}
              to={`/tickets/${t.id}`}
              className={({ isActive }) => `rail-item${isActive ? " active" : ""}`}
            >
              <div className="row-between" style={{ gap: 8 }}>
                <span className="mono caption">{t.id}</span>
                <span className="caption">{ago(t.updated_at)}</span>
              </div>
              <div className="title-sm" style={{ marginTop: 6 }}>
                {t.intent_label || t.subject}
              </div>
              <div className="row" style={{ marginTop: 8, gap: 6 }}>
                <StatusBadge status={t.status} label={t.status_label} />
                <SeverityBadge level={t.severity} />
              </div>
            </NavLink>
          ))}
        </div>
      </aside>
      <Outlet />
    </div>
  );
}
export function InboxHome() {
  return (
    <section className="workspace">
      <div className="ws-scroll" style={{ display: "grid", placeItems: "center" }}>
        <div style={{ textAlign: "center", maxWidth: 420, padding: 32 }}>
          <span className="icon-plate" style={{ margin: "0 auto", width: 56, height: 56 }}>
            <Icon name="inbox" size={26} />
          </span>
          <h2 className="title-lg" style={{ marginTop: 20 }}>
            Select a ticket
          </h2>
          <p className="muted" style={{ margin: "10px 0 24px" }}>
            Pick a ticket on the left to see its steps and conversation, or tell us about a new problem.
          </p>
          <Link to="/tickets/new" className="btn btn-primary">
            Get help with something new
          </Link>
        </div>
      </div>
    </section>
  );
}
/* ------------------------------------------------------------------ ticket workspace */
const STAGES = ["Received", "Diagnosed", "Being solved", "Confirm fix", "Resolved"];
function stageOf(status) {
  return (
    {
      analyzing: 0,
      self_service: 2,
      escalated: 1,
      in_progress: 2,
      awaiting_customer: 2,
      solution_proposed: 3,
      resolved: 4,
      closed: 4,
    }[status] ?? 0
  );
}
const PROGRESS_TEXT = {
  retrieving: "Searching past cases like yours…",
  triaging: "Understanding your issue…",
  drafting: "Preparing step-by-step help…",
};
export function TicketDetail() {
  const { id = "" } = useParams();
  const ticket = useResource(() => api.get(`/v1/tickets/${id}`), [id]);
  const [stage, setStage] = useState(null);
  const [chatStep, setChatStep] = useState(null);
  const [busy, setBusy] = useState(false);
  const [toast, showToast] = useToast();
  const [reopenOpen, setReopenOpen] = useState(false);
  const [tab, setTab] = useState("steps");
  useLiveEvents((event) => {
    if (event.ticket_id !== id) return;
    if (event.kind === "analysis_progress") setStage(String(event.stage));
    else void ticket.reload();
  });
  const t = ticket.data;
  const act = async (fn, message) => {
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
  if (!t) {
    return (
      <section className="workspace" style={{ display: "grid", placeItems: "center" }}>
        {ticket.loading ? <Spinner /> : <ErrorNote error={ticket.error ?? "Ticket not found"} />}
      </section>
    );
  }
  const analyzing = t.analysis_state === "running";
  const open = !["resolved", "closed"].includes(t.status);
  const anyWorked = t.steps.some((s) => s.status === "worked");
  const generalMessages = t.messages.filter((m) => !m.step_id);
  const current = stageOf(t.status);
  const canReopen = ["self_service", "solution_proposed", "awaiting_customer", "resolved"].includes(t.status);
  return (
    <section className="workspace" aria-label={`Ticket ${t.id}`}>
      <header className="ws-head">
        <Link to="/tickets" className="btn btn-text only-mobile" style={{ color: "var(--body)", marginBottom: 8 }}>
          <Icon name="back" size={16} /> All tickets
        </Link>
        <div className="row-between" style={{ alignItems: "flex-start" }}>
          <div className="grow">
            <div className="row" style={{ gap: 8 }}>
              <span className="mono caption">{t.id}</span>
              <StatusBadge status={t.status} label={t.status_label} />
              {t.issue && <SeverityBadge level={t.issue.severity} />}
            </div>
            <h1 className="title-lg" style={{ marginTop: 8 }}>
              {t.issue?.label || t.subject}
            </h1>
            <div className="row caption" style={{ marginTop: 8, gap: 12 }}>
              <span className="mini-progress" aria-label={`Stage: ${STAGES[current]}`}>
                {STAGES.map((s, i) => (
                  <span key={s} className={i < current ? "done" : i === current ? "now" : ""} />
                ))}
              </span>
              <span>{STAGES[current]}</span>
              <span>· Opened {ago(t.created_at)}</span>
              {t.assignee && <span>· Handled by {t.assignee}</span>}
              {t.reopen_count > 0 && <span>· Reopened {t.reopen_count}×</span>}
            </div>
          </div>
          {open && !analyzing && (
            <div className="row">
              {canReopen && (
                <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => setReopenOpen(true)}>
                  Still not working
                </button>
              )}
              <button
                className="btn btn-primary btn-sm"
                disabled={busy}
                onClick={() =>
                  act(() => api.post(`/v1/tickets/${t.id}/confirm`, { solved: true }), "Ticket resolved — thank you!")
                }
              >
                <Icon name="check" size={16} /> It's solved
              </button>
            </div>
          )}
          {!open && t.status === "resolved" && (
            <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => setReopenOpen(true)}>
              Problem came back? Reopen
            </button>
          )}
        </div>
      </header>

      {analyzing && (
        <div className="ws-strip">
          <Spinner /> <span className="pulse">{PROGRESS_TEXT[stage ?? ""] ?? "Analysing your request…"}</span>
          <span className="caption">Your ticket is saved and you'll get an email.</span>
        </div>
      )}
      {t.incident && (
        <div className="ws-strip warn">
          <Icon name="radar" size={18} />
          <span>
            <strong>{t.incident.status === "resolved" ? "Area issue resolved: " : "Known issue in your area: "}</strong>
            {t.incident.public_note}
          </span>
        </div>
      )}
      {!analyzing && t.why && !t.incident && (
        <div className="ws-strip">
          <Icon name={t.route === "self_service" ? "bolt" : "user"} size={18} />
          <span>{t.why}</span>
        </div>
      )}

      <div className="ws-body">
        <div className="panel">
          <div className="panel-head">
            <div className="tabs customer-tabs" role="tablist">
              <button className={`tab${tab === "steps" ? " active" : ""}`} onClick={() => setTab("steps")}>
                {t.steps[0]?.origin !== "ai" ? "Solution" : "Steps"}
                <span className="count">{t.steps.length}</span>
              </button>
              <button className={`tab${tab === "timeline" ? " active" : ""}`} onClick={() => setTab("timeline")}>
                Timeline
              </button>
              <button className={`tab${tab === "details" ? " active" : ""}`} onClick={() => setTab("details")}>
                Details
              </button>
            </div>
          </div>
          <div className="panel-body">
            {tab === "steps" && (
              <StepsTab
                ticket={t}
                busy={busy}
                open={open}
                anyWorked={anyWorked}
                onChat={setChatStep}
                onStatus={(s, status) =>
                  act(
                    () => api.post(`/v1/tickets/${t.id}/steps/${s.id}/feedback`, { status }),
                    status === "worked"
                      ? "Great — glad that helped"
                      : status === "did_not_work"
                        ? "Thanks — noted for your admin"
                        : undefined,
                  )
                }
                onSolved={() =>
                  act(() => api.post(`/v1/tickets/${t.id}/confirm`, { solved: true }), "Ticket resolved — thank you!")
                }
                onAdmin={() =>
                  act(
                    () => api.post(`/v1/tickets/${t.id}/escalate`, { text: "Customer asked for an admin" }),
                    "An admin will take it from here",
                  )
                }
                onRate={(rating) =>
                  act(() => api.post(`/v1/tickets/${t.id}/feedback`, { rating }), "Thanks for the feedback")
                }
              />
            )}
            {tab === "timeline" && (
              <div className="timeline">
                {t.events.map((e, i) => (
                  <div
                    key={i}
                    className={`tl-item${["created", "resolved", "reopened", "escalated"].includes(e.kind) ? " key" : ""}`}
                  >
                    <div>{describeEvent(e.kind, e.detail)}</div>
                    <div className="caption">{ago(e.created_at)}</div>
                  </div>
                ))}
              </div>
            )}
            {tab === "details" && (
              <dl className="kv">
                <dt>Your message</dt>
                <dd style={{ whiteSpace: "pre-wrap" }}>{t.complaint}</dd>
                <dt>Issue</dt>
                <dd>{t.issue?.label ?? "Being diagnosed"}</dd>
                <dt>Product</dt>
                <dd>{t.issue?.product ?? "—"}</dd>
                <dt>Priority</dt>
                <dd>{t.issue?.severity ?? "—"}</dd>
                <dt>Area</dt>
                <dd>{t.region ?? "—"}</dd>
                <dt>Target response</dt>
                <dd>{t.sla_due_at ? new Date(t.sla_due_at).toLocaleString() : "—"}</dd>
              </dl>
            )}
          </div>
        </div>

        <div className="panel soft">
          <div className="panel-head plain">
            <h2 className="title-sm">Conversation</h2>
            <span className="caption">{t.assignee ? `with ${t.assignee}` : "Support team"}</span>
          </div>
          <div className="panel-body">
            {generalMessages.length ? (
              <Thread
                messages={generalMessages}
                viewer="customer"
                disabled={busy}
                onQuickReply={(option) =>
                  act(() => api.post(`/v1/tickets/${t.id}/messages`, { body: option, answered_option: option }))
                }
              />
            ) : (
              <p className="muted body-sm">No messages yet.</p>
            )}
          </div>
          {t.status !== "closed" && (
            <div className="panel-foot">
              <Composer
                busy={busy}
                placeholder="Message support…"
                onSend={(body) => act(() => api.post(`/v1/tickets/${t.id}/messages`, { body }), "Message sent")}
              />
            </div>
          )}
        </div>
      </div>

      {chatStep && (
        <StepChatDrawer
          ticket={t}
          step={chatStep}
          onClose={() => setChatStep(null)}
          onUpdate={(next) => ticket.setData(next)}
        />
      )}
      {reopenOpen && (
        <ReopenDialog
          busy={busy}
          onCancel={() => setReopenOpen(false)}
          onSubmit={async (note) => {
            setReopenOpen(false);
            await act(
              () => api.post(`/v1/tickets/${t.id}/confirm`, { solved: false, note }),
              "Sent back to an admin",
            );
          }}
        />
      )}
      <Toast message={toast} />
    </section>
  );
}
function StepsTab({ ticket: t, busy, open, anyWorked, onStatus, onChat, onSolved, onAdmin, onRate }) {
  const precautionPlan = t.route === "human" && t.steps[0]?.origin === "ai";
  return (
    <div className="stack">
      {!open && (
        <div className="card-flat stack-sm" style={{ padding: 24 }}>
          <span className="badge badge-green" style={{ alignSelf: "flex-start" }}>
            Resolved {ago(t.resolved_at)}
          </span>
          {t.resolution?.root_cause && (
            <div>
              <div className="caption">What caused it</div>
              <div>{t.resolution.root_cause}</div>
            </div>
          )}
          <Rating ticket={t} onRate={onRate} />
        </div>
      )}
      {t.analysis_state === "running" && <p className="muted body-sm">Steps will appear here in a few seconds.</p>}
      {t.analysis_state !== "running" && t.steps.length === 0 && (
        <Empty title="An admin is on it">
          This issue needs a person to look at it. You'll get a reply in the conversation and by email.
        </Empty>
      )}
      {t.steps.length > 0 && (
        <>
          <div className="step-intro">
            <strong>{precautionPlan ? "Safe checks while an admin reviews" :
              t.steps[0]?.origin !== "ai" ? "Steps from your admin" : "Suggested steps for your issue"}</strong>
            <p className="caption">
              {precautionPlan ? "An admin will handle the fix. These checks only help you record useful information; they will not change your service." :
                <>Try each step, then tell us whether it worked. {t.steps[0]?.origin === "ai" &&
                  "The information under each suggestion explains what supports it. "}
                  Stuck? Use “Ask about this step”.</>}
            </p>
          </div>
          <div>
            {t.steps.map((s) => (
              <StepRow
                key={s.id}
                step={s}
                precaution={precautionPlan && s.origin === "ai"}
                disabled={busy || !open}
                chatCount={t.messages.filter((m) => m.step_id === s.id).length}
                onStatus={(status) => onStatus(s, status)}
                onChat={() => onChat(s)}
              />
            ))}
          </div>
          {open && anyWorked && !precautionPlan && (
            <div className="alert alert-info" style={{ alignItems: "center" }}>
              <Icon name="check" />
              <span className="grow">One of the steps worked. Is your issue completely solved?</span>
              <button className="btn btn-sm btn-primary" disabled={busy} onClick={onSolved}>
                Yes, it's solved
              </button>
            </div>
          )}
          {open && t.status === "self_service" && (
            <button className="btn btn-text" style={{ alignSelf: "flex-start" }} disabled={busy} onClick={onAdmin}>
              Rather talk to an admin?
            </button>
          )}
        </>
      )}
    </div>
  );
}
function StepRow({ step, precaution, disabled, chatCount, onStatus, onChat }) {
  const evidence = step.evidence ?? [];
  return (
    <div className={`step ${step.status}`}>
      <span className="step-num">
        {step.status === "worked" ? (
          <Icon name="check" size={16} stroke={2.5} />
        ) : step.status === "did_not_work" ? (
          <Icon name="x" size={14} stroke={2.5} />
        ) : (
          step.position
        )}
      </span>
      <div className="stack-sm">
        <div style={{ fontWeight: 500 }}>{step.text}</div>
        {step.detail && <div className="muted body-sm">{step.detail}</div>}
        {step.origin === "ai" && evidence.length > 0 && (
          <div className="step-evidence">
            <div className="step-evidence-title">Why we're suggesting this</div>
            <ul>
              {evidence.map((source, index) => (
                <li key={`${source.kind}-${index}`}>
                  <span className="step-source-type">{source.kind === "guide" ? "Support guide" : "Past resolved case"}</span>
                  <strong>{source.title}</strong>
                  {source.excerpt && <p>{source.excerpt}</p>}
                </li>
              ))}
            </ul>
          </div>
        )}
        {step.origin === "ai" && evidence.length === 0 && (
          <p className="caption">An admin can review the supporting information for this suggestion.</p>
        )}
        {!precaution && <div className="row" style={{ marginTop: 4 }}>
          <div className="seg" role="group" aria-label="Did this step work?">
            <button
              className={step.status === "worked" ? "on-worked" : ""}
              disabled={disabled}
              onClick={() => onStatus(step.status === "worked" ? "pending" : "worked")}
            >
              <Icon name="check" size={14} /> Tried — worked
            </button>
            <button
              className={step.status === "did_not_work" ? "on-did_not_work" : ""}
              disabled={disabled}
              onClick={() => onStatus(step.status === "did_not_work" ? "pending" : "did_not_work")}
            >
              <Icon name="x" size={13} /> Tried — didn't work
            </button>
          </div>
          <button className="btn btn-xs btn-outline" onClick={onChat}>
            <Icon name="chat" size={14} /> Ask about this step{chatCount ? ` (${chatCount})` : ""}
          </button>
        </div>}
      </div>
    </div>
  );
}
function StepChatDrawer({ ticket, step, onClose, onUpdate }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const messages = ticket.messages.filter((m) => m.step_id === step.id);
  const send = async (text) => {
    setBusy(true);
    setError(null);
    try {
      onUpdate(await api.post(`/v1/tickets/${ticket.id}/steps/${step.id}/chat`, { text }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send");
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label={`Help with step ${step.position}`}>
        <div className="drawer-head row-between">
          <div className="grow">
            <div className="eyebrow">Step {step.position}</div>
            <div className="title-sm" style={{ marginTop: 4 }}>
              {step.text}
            </div>
          </div>
          <button className="btn btn-secondary btn-sm" onClick={onClose} aria-label="Close">
            <Icon name="x" size={16} />
          </button>
        </div>
        <div className="drawer-body">
          {messages.length ? (
            <Thread messages={messages} viewer="customer" />
          ) : (
            <div className="stack-sm">
              <p className="muted body-sm">
                Stuck on this step? Ask anything about it — where a button is, what a light means, or what to do next.
              </p>
              <div className="chips">
                {["Where do I find this?", "What should I see if it worked?", "I can't do this step"].map((q) => (
                  <button key={q} className="chip" disabled={busy} onClick={() => void send(q)}>
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}
          {busy && (
            <div style={{ marginTop: 12 }}>
              <Spinner />
            </div>
          )}
          <ErrorNote error={error} />
        </div>
        <div className="drawer-foot">
          <Composer busy={busy} placeholder="Ask about this step…" onSend={send} />
        </div>
      </aside>
    </>
  );
}
function ReopenDialog({ busy, onCancel, onSubmit }) {
  const [note, setNote] = useState("");
  return (
    <>
      <div className="drawer-backdrop" onClick={onCancel} />
      <aside className="drawer" role="dialog" aria-label="Still not working">
        <div className="drawer-head">
          <div className="title-md">Sorry it's still not working</div>
        </div>
        <div className="drawer-body stack">
          <p className="muted body-sm">
            Tell us what happened when you tried the fix. Your ticket goes straight back to an admin with everything
            you've already tried.
          </p>
          <textarea
            className="textarea"
            autoFocus
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="e.g. It worked for an hour, then dropped again at 8 pm"
          />
        </div>
        <div className="drawer-foot row">
          <button className="btn btn-primary" disabled={busy} onClick={() => onSubmit(note)}>
            Send back to support
          </button>
          <button className="btn btn-secondary" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </aside>
    </>
  );
}
function Rating({ ticket, onRate }) {
  const current = ticket.feedback?.rating ?? 0;
  return (
    <div>
      <div className="caption">How did we do?</div>
      <div className="row" style={{ marginTop: 6 }}>
        {[1, 2, 3, 4, 5].map((n) => (
          <button
            key={n}
            className={`chip${n <= current ? " selected" : ""}`}
            style={{ minWidth: 44, justifyContent: "center" }}
            onClick={() => onRate(n)}
            aria-label={`${n} stars`}
          >
            {n}
          </button>
        ))}
      </div>
    </div>
  );
}
function describeEvent(kind, detail) {
  const d = detail ?? {};
  switch (kind) {
    case "created":
      return "Ticket received";
    case "analyzed":
      return `Diagnosed as “${d.intent ?? "your issue"}”`;
    case "status_changed":
      return `Status: ${String(d.to ?? "").replace(/_/g, " ")}${d.reason ? ` — ${d.reason}` : ""}`;
    case "step_feedback":
      return `Step ${d.step}: ${d.status === "worked" ? "worked" : d.status === "did_not_work" ? "didn't work" : "reset"}`;
    case "assigned":
      return "An admin picked up your ticket";
    case "info_requested":
      return "Support asked for more information";
    case "solution_proposed":
      return "A solution was proposed";
    case "reopened":
      return "You reopened the ticket";
    case "resolved":
      return "Resolved";
    case "escalated":
      return "Sent to an admin";
    case "incident_linked":
      return "Linked to a known area issue";
    default:
      return kind.replace(/_/g, " ");
  }
}
