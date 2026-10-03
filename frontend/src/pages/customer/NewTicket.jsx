import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import { Icon } from "../../components/Icon";
import { Bars, ErrorNote, Spinner } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { useResource } from "../../hooks/useLive";
const ORDER = ["area", "issue", "describe", "questions", "review"];
const STAGE_LABEL = {
  area: "Choose a topic",
  issue: "Pick the issue",
  describe: "Describe it",
  questions: "Quick questions",
  review: "Review",
};
export default function NewTicket() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const catalog = useResource(() => api.get("/v1/catalog"), []);
  const [stage, setStage] = useState("area");
  const [area, setArea] = useState(null);
  const [intent, setIntent] = useState(null);
  const [complaint, setComplaint] = useState("");
  const [intake, setIntake] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const issues = useMemo(() => (catalog.data?.issues ?? []).filter((i) => i.area === area), [catalog.data, area]);
  const areaLabel = catalog.data?.areas.find((a) => a.id === area)?.label;
  const issueLabel = issues.find((i) => i.intent === intent)?.label;
  const summaryText = complaint.trim() || [areaLabel, issueLabel].filter(Boolean).join(": ");
  const run = async (fn) => {
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
    const state = await run(() => api.post("/v1/intake/start", { complaint: summaryText, area, intent }));
    if (state) {
      setIntake(state);
      setStage(state.done ? "review" : "questions");
    }
  };
  const answer = async (question, optionIds, text) => {
    if (!intake) return;
    const state = await run(() =>
      api.post(`/v1/intake/${intake.session_id}/answer`, {
        question_id: question.id,
        option_ids: optionIds,
        text: text ?? null,
      }),
    );
    if (state) {
      setIntake(state);
      if (state.done) setStage("review");
    }
  };
  const submit = async () => {
    const text = summaryText || "Support request";
    const ticket = await run(() =>
      api.post("/v1/tickets", {
        complaint: text.length >= 5 ? text : `${text} issue`,
        session_id: intake?.session_id ?? null,
        region: user?.region ?? null,
      }),
    );
    if (ticket) navigate(`/tickets/${ticket.id}`, { replace: true });
  };
  const back = () => {
    setError(null);
    if (stage === "issue") setStage("area");
    else if (stage === "describe") setStage(area ? "issue" : "area");
    else if (stage === "questions" || stage === "review") {
      setIntake(null);
      setStage("describe");
    }
  };
  const index = ORDER.indexOf(stage);
  return (
    <div className="wizard">
      <div className="wizard-main">
        <div className="wizard-content">
          <div className="wizard-inner stack-lg">
            {catalog.data?.known_incidents.length ? (
              <div className="alert alert-medium">
                <Icon name="radar" />
                <div>
                  <strong>Known issue near you:</strong> {catalog.data.known_incidents[0].title}.{" "}
                  {catalog.data.known_incidents[0].public_note}
                </div>
              </div>
            ) : null}

            {stage === "area" && (
              <section className="stack fade-in">
                <div className="eyebrow">New request</div>
                <h1 className="display-sm">What do you need help with?</h1>
                {catalog.loading && <Spinner />}
                <ErrorNote error={catalog.error} />
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
                        <span className="title-sm" style={{ display: "block" }}>
                          {a.label}
                        </span>
                        <span className="caption">{a.issues} common issues</span>
                      </span>
                    </button>
                  ))}
                </div>
                <button
                  className="option-card"
                  onClick={() => {
                    setArea(null);
                    setIntent(null);
                    setStage("describe");
                  }}
                >
                  <span className="icon-plate">
                    <Icon name="chat" />
                  </span>
                  <span className="grow">
                    <span className="title-sm" style={{ display: "block" }}>
                      Not sure — I'll describe it
                    </span>
                    <span className="caption">We'll work out what's wrong with a couple of quick questions</span>
                  </span>
                  <Icon name="arrow" size={18} />
                </button>
              </section>
            )}

            {stage === "issue" && (
              <section className="stack fade-in">
                <div className="eyebrow">{areaLabel}</div>
                <h1 className="display-sm">What's happening?</h1>
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
                  <button
                    className="chip chip-lg"
                    onClick={() => {
                      setIntent(null);
                      setStage("describe");
                    }}
                  >
                    Something else
                  </button>
                </div>
              </section>
            )}

            {stage === "describe" && (
              <section className="stack fade-in">
                <div className="eyebrow">{issueLabel ?? areaLabel ?? "Describe the problem"}</div>
                <h1 className="display-sm">{issueLabel ? "Tell us a bit more" : "What's going wrong?"}</h1>
                <label className="field">
                  <span className="label">In your own words — any language is fine</span>
                  <textarea
                    className="textarea"
                    autoFocus
                    maxLength={5000}
                    style={{ minHeight: 160 }}
                    placeholder="e.g. My internet drops every evening around 8 and I work from home…"
                    value={complaint}
                    onChange={(e) => setComplaint(e.target.value)}
                  />
                  <span className="hint">
                    Don't include passwords, OTPs or card numbers — phone numbers and emails are removed automatically.
                  </span>
                </label>
                {intent && !complaint.trim() && (
                  <p className="caption">Optional — you can continue and just answer a couple of quick questions.</p>
                )}
              </section>
            )}

            {stage === "questions" && intake?.next_question && (
              <QuestionCard
                key={intake.next_question.id + intake.answers.length}
                question={intake.next_question}
                busy={busy}
                onAnswer={answer}
              />
            )}

            {stage === "review" && intake && (
              <section className="stack fade-in">
                <div className="eyebrow">Almost done</div>
                <h1 className="display-sm">Ready to raise your ticket</h1>
                <div className="card-sm stack-sm">
                  <div className="caption">Your description</div>
                  <div>{summaryText}</div>
                  {intake.answers.map((a) => (
                    <div
                      key={a.question_id}
                      className="row-between body-sm"
                      style={{ borderTop: "1px solid var(--hairline-soft)", paddingTop: 8 }}
                    >
                      <span className="muted">{a.question}</span>
                      <strong>{a.answer?.join(", ") || a.text || "—"}</strong>
                    </div>
                  ))}
                </div>
                <p className="caption">
                  We'll email you a confirmation right away, and your ticket stays open until you confirm it's solved.
                </p>
              </section>
            )}
            <ErrorNote error={error} />
          </div>
        </div>

        <footer className="wizard-foot">
          {stage === "area" ? (
            <Link to="/tickets" className="btn btn-text" style={{ color: "var(--body)" }}>
              Cancel
            </Link>
          ) : (
            <button className="btn btn-text" style={{ color: "var(--body)" }} disabled={busy} onClick={back}>
              <Icon name="back" size={16} /> Back
            </button>
          )}
          <div className="row hide-mobile" style={{ gap: 10 }}>
            <span className="mini-progress">
              {ORDER.map((s, i) => (
                <span key={s} className={i < index ? "done" : i === index ? "now" : ""} />
              ))}
            </span>
            <span className="caption">
              Step {index + 1} of 5 · {STAGE_LABEL[stage]}
            </span>
          </div>
          {stage === "describe" && (
            <button
              className="btn btn-primary"
              disabled={busy || (!complaint.trim() && !intent && !area)}
              onClick={startIntake}
            >
              {busy ? (
                <Spinner light />
              ) : (
                <>
                  Continue <Icon name="arrow" size={16} />
                </>
              )}
            </button>
          )}
          {stage === "review" && (
            <button className="btn btn-primary" disabled={busy} onClick={submit}>
              {busy ? <Spinner light /> : "Submit ticket"}
            </button>
          )}
          {!["describe", "review"].includes(stage) && (
            <span style={{ width: 120 }} className="hide-mobile">
              {busy && <Spinner />}
            </span>
          )}
        </footer>
      </div>

      <aside className="wizard-side stack">
        <div className="eyebrow">What we think it is</div>
        {!intake ? (
          <p className="muted body-sm">
            As you answer, we narrow down the likely issue. Each question is picked to rule out as many possibilities as
            possible, so you answer fewer of them.
          </p>
        ) : (
          <>
            <Bars
              stacked
              rows={intake.candidates.map((c) => ({ label: c.label, value: c.probability }))}
              format={(v) => `${Math.round(v * 100)}%`}
            />
            <div className="row-between caption">
              <span>Remaining uncertainty</span>
              <span className="num">{intake.entropy_bits.toFixed(2)} bits</span>
            </div>
            {intake.answers.length > 0 && (
              <div className="caption">
                Your answers removed{" "}
                <span className="num">
                  {intake.answers.reduce((s, a) => s + (a.information_gain_bits || 0), 0).toFixed(2)}
                </span>{" "}
                bits of uncertainty.
              </div>
            )}
          </>
        )}
        <div className="divider" />
        <div className="stack-sm caption">
          <div className="row nowrap">
            <Icon name="bolt" size={16} /> Familiar issues get instant, step-by-step fixes.
          </div>
          <div className="row nowrap">
            <Icon name="user" size={16} /> Anything complex goes to a specialist.
          </div>
          <div className="row nowrap">
            <Icon name="mail" size={16} /> Email confirmation in seconds.
          </div>
        </div>
      </aside>
    </div>
  );
}
function QuestionCard({ question, busy, onAnswer }) {
  const [selected, setSelected] = useState([]);
  const [text, setText] = useState("");
  return (
    <section className="stack fade-in">
      <div className="row">
        <span className="badge badge-blue">
          {question.kind === "diagnostic" ? "Quick question" : question.kind === "severity" ? "Impact" : "Context"}
        </span>
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
            <button
              className="btn btn-primary"
              disabled={busy || !selected.length}
              onClick={() => onAnswer(question, selected)}
            >
              Continue
            </button>
            <button className="btn btn-text" disabled={busy} onClick={() => onAnswer(question, [])}>
              Skip
            </button>
          </div>
        </>
      )}
      {question.type === "text" && (
        <>
          <textarea
            className="textarea"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Optional"
          />
          <div className="row">
            <button className="btn btn-primary" disabled={busy} onClick={() => onAnswer(question, [], text)}>
              {busy ? <Spinner light /> : text.trim() ? "Continue" : "Skip"}
            </button>
          </div>
        </>
      )}
    </section>
  );
}
