import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../../api/client";
import { Icon } from "../../components/Icon";
import { ConsoleHead } from "../../components/Layout";
import { ErrorNote, Spinner } from "../../components/ui";
import { useResource } from "../../hooks/useLive";

function Field({ label, children }) {
  if (children === null || children === undefined || children === "") return null;
  return (
    <div>
      <div className="eyebrow">{label}</div>
      <div className="body-sm" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{children}</div>
    </div>
  );
}

function Numbered({ items }) {
  return items?.length ? (
    <ol className="body-sm" style={{ margin: 0, paddingLeft: 20 }}>
      {items.map((item, index) => <li key={index}>{item}</li>)}
    </ol>
  ) : <span className="caption">None recorded</span>;
}

function ExtraFields({ record, skip }) {
  const entries = Object.entries(record).filter(([key, value]) => !skip.includes(key) && value != null && value !== "" &&
    (!Array.isArray(value) || value.length));
  if (!entries.length) return null;
  return (
    <div className="card stack">
      <h2 className="title-md">Other recorded details</h2>
      {entries.map(([key, value]) => (
        <Field key={key} label={key.replaceAll("_", " ")}>
          {Array.isArray(value) ? value.join("; ") : typeof value === "object" ? JSON.stringify(value, null, 2) : String(value)}
        </Field>
      ))}
    </div>
  );
}

export default function SourceDetail() {
  const { sourceId = "" } = useParams();
  const navigate = useNavigate();
  const source = useResource(() => api.get(`/v1/admin/sources?source_id=${encodeURIComponent(sourceId)}`), [sourceId]);
  const detail = source.data?.id === sourceId ? source.data : null;
  const article = detail?.article;
  const ticket = detail?.case;
  return (
    <div className="stack-lg">
      <ConsoleHead eyebrow="Retrieved evidence" title={article?.title || ticket?.subject || "Source details"}>
        <button className="btn btn-secondary btn-sm" onClick={() => navigate(-1)}>
          <Icon name="back" size={14} /> Back
        </button>
      </ConsoleHead>
      {source.loading && !detail ? <Spinner /> : <ErrorNote error={source.error} />}
      {detail && (
        <>
          <div className="card stack-sm">
            <div className="row">
              <span className="mono caption">{detail.id}</span>
              <span className="badge">{detail.kind === "kb" ? "Knowledge base" : "Resolved case"}</span>
              <span className="badge">{article?.status || detail.status}</span>
            </div>
            <div className="caption">The full record behind this citation. Retrieved similarity is shown in the analysis, not stored on the source.</div>
          </div>
          {article && (
            <>
              <div className="card stack">
                <h2 className="title-md">Cited section: {detail.section}</h2>
                <div className="body-sm" style={{ whiteSpace: "pre-wrap" }}>{detail.section_text}</div>
              </div>
              <div className="card stack">
                <h2 className="title-md">Complete knowledge-base article</h2>
                <Field label="Summary">{article.summary}</Field>
                <Field label="Customer self-help"><Numbered items={article.self_help} /></Field>
                <Field label="Admin checks"><Numbered items={article.checks} /></Field>
                <Field label="Escalation criteria">{article.escalation}</Field>
                <Field label="Product">{article.product}</Field>
                <Field label="Intent">{article.intent}</Field>
                <Field label="Version">{article.version}</Field>
                <Field label="Origin">{article.origin}</Field>
                <Field label="Review reason">{article.review_reason}</Field>
                <Field label="Created by">{article.created_by}</Field>
                {article.source_ticket_id && (
                  <Field label="Learned from ticket">
                    <Link to={`/console/tickets/${encodeURIComponent(article.source_ticket_id)}`}>
                      {article.source_ticket_id}
                    </Link>
                  </Field>
                )}
                <Field label="Updated">{article.updated_at && new Date(article.updated_at).toLocaleString()}</Field>
              </div>
            </>
          )}
          {ticket && (
            <>
              <div className="card stack">
                <h2 className="title-md">What happened</h2>
                <Field label="Customer complaint">{ticket.body}</Field>
                <Field label="Root cause or resolution summary">{ticket.resolution_summary}</Field>
                <Field label="Resolution steps"><Numbered items={ticket.resolution_steps} /></Field>
                <Field label="Closure evidence">{ticket.closure_evidence}</Field>
                {detail.related_ticket_id && (
                  <Link to={`/console/tickets/${encodeURIComponent(detail.related_ticket_id)}`} className="btn btn-primary btn-sm" style={{ alignSelf: "flex-start" }}>
                    Open original ticket and timeline <Icon name="arrow" size={14} />
                  </Link>
                )}
              </div>
              <div className="card stack">
                <h2 className="title-md">Case details</h2>
                <Field label="Created">{ticket.created_at}</Field>
                <Field label="Resolved">{ticket.resolved_at}</Field>
                <Field label="Product">{ticket.product}</Field>
                <Field label="Category">{ticket.category}</Field>
                <Field label="Intent">{ticket.intent}</Field>
                <Field label="Severity">{ticket.severity}</Field>
                <Field label="Sentiment">{ticket.sentiment}</Field>
                <Field label="Origin">{detail.origin}</Field>
                <Field label="Version">{detail.version}</Field>
                <Field label="Outcome score">{detail.outcome_score}</Field>
              </div>
              <ExtraFields record={ticket} skip={["body", "resolution_summary", "resolution_steps", "closure_evidence",
                "created_at", "resolved_at", "product", "category", "intent", "severity", "sentiment", "subject"]} />
            </>
          )}
        </>
      )}
    </div>
  );
}
