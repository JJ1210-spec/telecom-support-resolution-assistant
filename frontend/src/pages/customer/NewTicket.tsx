import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import type { Catalog, CustomerTicket, IntakeState, Question } from "../../api/types";
import { Icon } from "../../components/Icon";
import { Bars, ErrorNote, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { useResource } from "../../hooks/useLive";

type Stage = "area" | "issue" | "describe" | "questions" | "review";

export default function NewTicket() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const catalog = useResource(() => api.get<Catalog>("/v1/catalog"), []);
  const [stage, setStage] = useState<Stage>("area");
  const [area, setArea] = useState<string | null>(null);
  const [intent, setIntent] = useState<string | null>(null);
  const [complaint, setComplaint] = useState("");
  const [intake, setIntake] = useState<IntakeState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const issues = useMemo(() => (catalog.data?.issues ?? []).filter((i) => i.area === area), [catalog.data, area]);
  const areaLabel = catalog.data?.areas.find((a) => a.id === area)?.label;
  const issueLabel = issues.find((i) => i.intent === intent)?.label;

  const run = async <T,>(fn: () => Promise<T>): Promise<T | null> => {
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
      return null;
    } finally {
      setBusy(false);
    }
  };

  const startIntake = async () => {
    const text = complaint.trim() || [areaLabel, issueLabel].filter(Boolean).join(": ");
    const state = await run(() => api.post<IntakeState>("/v1/intake/start", { complaint: text, area, intent }));
    if (state) {
      setIntake(state);
      setStage(state.done ? "review" : "questions");
    }
  };

  const answer = async (question: Question, optionIds: string[], text?: string) => {
    if (!intake) return;
    const state = await run(() =>
      api.post<IntakeState>(`/v1/intake/${intake.session_id}/answer`, { question_id: question.id, option_ids: optionIds, text: text ?? null }),
    );
    if (state) {
      setIntake(state);
      if (state.done) setStage("review");
    }
  };

  const submit = async () => {
    const text = complaint.trim() || [areaLabel, issueLabel].filter(Boolean).join(": ") || "Support request";
    const ticket = await run(() =>
      api.post<CustomerTicket>("/v1/tickets", { complaint: text.length >= 5 ? text : `${text} issue`, session_id: intake?.session_id ?? null, region: user?.region ?? null }),
    );
    if (ticket) navigate(`/tickets/${ticket.id}`, { replace: true });
  };

  const stepIndex = { area: 0, issue: 1, describe: 2, questions: 3, review: 4 }[stage];

  return (
    <div className="container page">
      <div className="grid-2" style={{ gridTemplateColumns: "minmax(0, 1.5fr) minmax(0, 1fr)", alignItems: "start" }}>
        <div className="stack-lg">
          <div>
            <div className="eyebrow">New ticket · step {stepIndex + 1} of 5</div>
            <div className="progress-track" style={{ marginTop: 12 }}>
              {[0, 1, 2, 3, 4].map((i) => (
                <span key={i} style={{ background: i <= stepIndex ? "var(--primary)" : "var(--surface-strong)" }} />
              ))}
            </div>
          </div>

          {catalog.data?.known_incidents.length ? (
            <div className="alert alert-medium">
              <Icon name="radar" />
              <div>
                <strong>Known issue near you:</strong> {catalog.data.known_incidents[0].title}. {catalog.data.known_incidents[0].public_note}
              </div>
            </div>
          ) : null}

          {stage === "area" && (
            <section className="stack fade-in">
              <h1 className="display-sm">What do you need help with?</h1>
              {catalog.loading && <Spinner />}
              <div className="grid-2" style={{ gap: 12 }}>
                {catalog.data?.areas.map((a) => (
                  <button
                    key={a.id}
                    className={`option-card${area === a.id ? " selected" : ""}`}
                    onClick={() => {
                      setArea(a.id);
                      setIntent(null);
                      setStage("issue");
                    }}
                  >
                    <span className="icon-plate">
                      <Icon name={a.icon} />
                    </span>
                    <span>
                      <span className="title-sm" style={{ display: "block" }}>{a.label}</span>
                      <span className="caption">{a.issues} common issues</span>
                    </span>
                  </button>
                ))}
              </div>
              <div className="divider" />
              <div className="row-between">
                <span className="muted body-sm">Not sure which one? Just describe it.</span>
                <button className="btn btn-text" onClick={() => { setArea(null); setIntent(null); setStage("describe"); }}>
                  Describe it in my own words <Icon name="arrow" size={16} />
                </button>
              </div>
            </section>
          )}

          {stage === "issue" && (
            <section className="stack fade-in">
              <BackLink onClick={() => setStage("area")} />
              <h1 className="display-sm">{areaLabel}: what's happening?</h1>
              <div className="chips">
                {issues.map((i) => (
                  <button
                    key={i.intent}
                    className={`chip chip-lg${intent === i.intent ? " selected" : ""}`}
                    onClick={() => {
                      setIntent(i.intent);
                      setStage("describe");
                    }}
                  >
                    {i.label}
                  </button>
                ))}
                <button className="chip chip-lg" onClick={() => { setIntent(null); setStage("describe"); }}>
                  Something else
                </button>
              </div>
            </section>
          )}

          {stage === "describe" && (
            <section className="stack fade-in">
              <BackLink onClick={() => setStage(area ? "issue" : "area")} />
              <h1 className="display-sm">{issueLabel ? `"${issueLabel}" — tell us more` : "Describe the problem"}</h1>
              <label className="field">
                <span className="label">In your own words (any language)</span>
                <textarea
                  className="textarea"
                  autoFocus
                  maxLength={5000}
                  placeholder="e.g. My internet drops every evening around 8 and I work from home…"
                  value={complaint}
                  onChange={(e) => setComplaint(e.target.value)}
                />
                <span className="hint">Please don't include passwords, OTPs or card numbers — we remove phone numbers and emails automatically.</span>
              </label>
              <ErrorNote error={error} />
              <div className="row">
                <button className="btn btn-primary" disabled={busy || (!complaint.trim() && !intent && !area)} onClick={startIntake}>
                  {busy ? <Spinner light /> : "Continue"}
                </button>
                {intent && !complaint.trim() && <span className="caption">You can skip this — we'll ask a couple of quick questions.</span>}
              </div>
            </section>
          )}

          {stage === "questions" && intake?.next_question && (
            <QuestionCard key={intake.next_question.id + intake.answers.length} question={intake.next_question} busy={busy} onAnswer={answer} />
          )}
          {stage === "questions" && <ErrorNote error={error} />}

          {stage === "review" && intake && (
            <section className="stack fade-in">
              <h1 className="display-sm">Ready to raise your ticket</h1>
              <div className="card-sm stack-sm">
                <div className="caption">Your description</div>
                <div>{complaint || [areaLabel, issueLabel].filter(Boolean).join(": ")}</div>
                {intake.answers.map((a) => (
                  <div key={a.question_id} className="row-between body-sm" style={{ borderTop: "1px solid var(--hairline-soft)", paddingTop: 8 }}>
                    <span className="muted">{a.question}</span>
                    <strong>{a.answer?.join(", ") || a.text || "—"}</strong>
                  </div>
                ))}
              </div>
              <ErrorNote error={error} />
              <div className="row">
                <button className="btn btn-primary btn-lg" disabled={busy} onClick={submit}>
                  {busy ? <Spinner light /> : "Submit ticket"}
                </button>
                <span className="caption">We'll email you a confirmation right away.</span>
              </div>
            </section>
          )}
        </div>

        <aside className="card-flat stack" style={{ position: "sticky", top: 88 }}>
          <div className="eyebrow">What we think it is</div>
          {!intake ? (
            <p className="muted body-sm">
              As you answer, we narrow down the likely issue. Each question is picked to rule out as many possibilities as
              possible — so you answer fewer of them.
            </p>
          ) : (
            <>
              <Bars rows={intake.candidates.map((c) => ({ label: c.label, value: c.probability }))} format={(v) => `${Math.round(v * 100)}%`} />
              <div className="row-between caption">
                <span>Remaining uncertainty</span>
                <span className="num">{intake.entropy_bits.toFixed(2)} bits</span>
              </div>
              {intake.answers.length > 0 && (
                <div className="caption">
                  Your answers removed{" "}
                  <span className="num">{intake.answers.reduce((s, a) => s + (a.information_gain_bits || 0), 0).toFixed(2)}</span> bits of uncertainty.
                </div>
              )}
            </>
          )}
        </aside>
      </div>
    </div>
  );
}

function BackLink({ onClick }: { onClick: () => void }) {
  return (
    <button className="btn btn-text" style={{ alignSelf: "flex-start", color: "var(--body)" }} onClick={onClick}>
      <Icon name="back" size={16} /> Back
    </button>
  );
}

function QuestionCard({ question, busy, onAnswer }: { question: Question; busy: boolean; onAnswer: (q: Question, ids: string[], text?: string) => void }) {
  const [selected, setSelected] = useState<string[]>([]);
  const [text, setText] = useState("");
  return (
    <section className="stack fade-in">
      <div className="row">
        <span className="badge badge-blue">{question.kind === "diagnostic" ? "Quick question" : question.kind === "severity" ? "Impact" : "Context"}</span>
        {question.generated && <span className="badge">Tailored to your answers</span>}
      </div>
      <h1 className="display-sm">{question.text}</h1>
      {question.type === "single" && (
        <div className="stack-sm">
          {question.options.map((o) => (
            <button key={o.id} className="option-card" disabled={busy} onClick={() => onAnswer(question, [o.id])}>
              <span style={{ flex: 1 }}>{o.label}</span>
              <Icon name="arrow" size={18} />
            </button>
          ))}
        </div>
      )}
      {question.type === "multi" && (
        <>
          <div className="chips">
            {question.options.map((o) => (
              <button
                key={o.id}
                className={`chip chip-lg${selected.includes(o.id) ? " selected" : ""}`}
                onClick={() => setSelected((s) => (s.includes(o.id) ? s.filter((x) => x !== o.id) : [...s, o.id]))}
              >
                {selected.includes(o.id) && <Icon name="check" size={16} />} {o.label}
              </button>
            ))}
          </div>
          <div className="row">
            <button className="btn btn-primary" disabled={busy || !selected.length} onClick={() => onAnswer(question, selected)}>
              Continue
            </button>
            <button className="btn btn-text" disabled={busy} onClick={() => onAnswer(question, [])}>Skip</button>
          </div>
        </>
      )}
      {question.type === "text" && (
        <>
          <textarea className="textarea" value={text} onChange={(e) => setText(e.target.value)} placeholder="Optional" />
          <div className="row">
            <button className="btn btn-primary" disabled={busy} onClick={() => onAnswer(question, [], text)}>
              {busy ? <Spinner light /> : text.trim() ? "Continue" : "Skip"}
            </button>
          </div>
        </>
      )}
      {busy && <Spinner />}
    </section>
  );
}
