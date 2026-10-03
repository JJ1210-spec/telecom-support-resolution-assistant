import { useEffect, useRef, useState } from "react";
import { ago } from "./ui";
import { Icon } from "./Icon";
const AUTHOR = { customer: "You", agent: "Support specialist", ai: "Resolve assistant", system: "Update" };
/** Conversation thread. `viewer` decides which side is "me". Quick-reply options render as chips. */
export function Thread({ messages, viewer, onQuickReply, disabled }) {
  const end = useRef(null);
  // Braces matter: newer browsers return a Promise from scrollIntoView, and React would call any value an effect
  // returns as its cleanup function -> "c is not a function" -> blank page.
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [messages.length]);
  const lastOptionsIndex = [...messages].reverse().findIndex((m) => m.options && m.options.length);
  const pendingIndex = lastOptionsIndex === -1 ? -1 : messages.length - 1 - lastOptionsIndex;
  const answered = pendingIndex >= 0 && messages.slice(pendingIndex + 1).some((m) => m.author_role === "customer");
  return (
    <div className="thread">
      {messages.map((m, index) => {
        const mine = m.author_role === viewer;
        const cls = mine
          ? "me"
          : m.author_role === "ai"
            ? "them ai"
            : m.author_role === "system"
              ? "them system"
              : "them";
        const internal = m.visibility === "internal";
        const who =
          viewer === "agent" && m.author_role === "customer"
            ? "Customer"
            : viewer === "agent" && m.author_role === "agent"
              ? "Agent"
              : AUTHOR[m.author_role];
        return (
          <div key={m.id} className={`msg ${cls}${internal ? " internal" : ""}`}>
            <div style={{ minWidth: 0 }}>
              <div className="bubble">
                {internal && <strong style={{ display: "block", fontSize: 12 }}>Internal note</strong>}
                {m.body}
                {m.answered_option && !mine && (
                  <div className="caption" style={{ marginTop: 6 }}>
                    Chose: {m.answered_option}
                  </div>
                )}
              </div>
              <div className="msg-meta" style={{ textAlign: mine ? "right" : "left" }}>
                {who} · {ago(m.created_at)}
              </div>
              {m.options &&
                m.options.length > 0 &&
                viewer === "customer" &&
                index === pendingIndex &&
                !answered &&
                onQuickReply && (
                  <div className="chips" style={{ marginTop: 10 }}>
                    {m.options.map((option) => (
                      <button key={option} className="chip" disabled={disabled} onClick={() => onQuickReply(option)}>
                        {option}
                      </button>
                    ))}
                    <span className="caption" style={{ alignSelf: "center" }}>
                      or type a reply
                    </span>
                  </div>
                )}
              {m.options && m.options.length > 0 && viewer === "agent" && (
                <div className="caption" style={{ marginTop: 6 }}>
                  Quick replies offered: {m.options.join(" · ")}
                </div>
              )}
            </div>
          </div>
        );
      })}
      <div ref={end} />
    </div>
  );
}
export function Composer({ onSend, placeholder = "Write a message…", busy }) {
  const [text, setText] = useState("");
  const send = async () => {
    const value = text.trim();
    if (!value) return;
    await onSend(value);
    setText("");
  };
  return (
    <div className="composer">
      <textarea
        className="textarea"
        value={text}
        placeholder={placeholder}
        aria-label={placeholder}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            void send();
          }
        }}
      />
      <button
        className="btn btn-primary"
        style={{ width: 48, padding: 0 }}
        disabled={busy || !text.trim()}
        onClick={() => void send()}
        aria-label="Send"
      >
        <Icon name="arrow" />
      </button>
    </div>
  );
}
