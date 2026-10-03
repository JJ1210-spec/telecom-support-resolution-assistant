import { useEffect, useState, type ReactNode } from "react";

export function Spinner({ light = false }: { light?: boolean }) {
  return <span className={`spinner${light ? " light" : ""}`} role="status" aria-label="Loading" />;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p className="title-md" style={{ color: "var(--ink)" }}>
        {title}
      </p>
      {children && <div className="muted" style={{ marginTop: 8 }}>{children}</div>}
    </div>
  );
}

const STATUS_TONE: Record<string, string> = {
  analyzing: "badge",
  self_service: "badge badge-blue",
  escalated: "badge badge-amber",
  in_progress: "badge badge-blue",
  awaiting_customer: "badge badge-amber",
  solution_proposed: "badge badge-blue",
  resolved: "badge badge-green",
  closed: "badge",
};

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  return (
    <span className={STATUS_TONE[status] ?? "badge"}>
      <span className="dot" />
      {label ?? status.replace(/_/g, " ")}
    </span>
  );
}

export function SeverityBadge({ level }: { level?: string | null }) {
  if (!level) return null;
  const tone = level === "P1" ? "badge-red" : level === "P2" ? "badge-amber" : "";
  return <span className={`badge sev ${tone}`}>{level}</span>;
}

const ROUTE_LABEL: Record<string, string> = {
  self_service: "Self-service",
  assisted: "Assisted",
  human: "Human",
};

export function RouteBadge({ route }: { route?: string | null }) {
  if (!route) return <span className="badge">Pending</span>;
  const tone = route === "self_service" ? "badge-green" : route === "assisted" ? "badge-blue" : "badge-dark";
  return <span className={`badge ${tone}`}>{ROUTE_LABEL[route] ?? route}</span>;
}

export function Cite({ id }: { id: string }) {
  const customer = /#h\d+$/.test(id);
  return (
    <span className={`cite${customer ? " customer" : ""}`} title={customer ? "Customer self-help section" : "Source"}>
      {id}
    </span>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "up" | "down" }) {
  return (
    <div className="stat">
      <div className="caption">{label}</div>
      <div className={`value ${tone ?? ""}`}>{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

export function Meter({ value, max = 1, tone }: { value: number; max?: number; tone?: "warn" | "bad" }) {
  const pct = Math.max(0, Math.min(100, (value / (max || 1)) * 100));
  return (
    <div className={`meter ${tone ?? ""}`} role="meter" aria-valuenow={value} aria-valuemin={0} aria-valuemax={max}>
      <span style={{ width: `${pct}%` }} />
    </div>
  );
}

export function pct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function ago(iso?: string | null): string {
  if (!iso) return "";
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function useToast(): [string | null, (message: string) => void] {
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    if (!message) return;
    const timer = window.setTimeout(() => setMessage(null), 3200);
    return () => window.clearTimeout(timer);
  }, [message]);
  return [message, setMessage];
}

export function Toast({ message }: { message: string | null }) {
  return message ? (
    <div className="toast" role="status">
      {message}
    </div>
  ) : null;
}

export function ErrorNote({ error }: { error: string | null }) {
  return error ? <p className="error-text" role="alert">{error}</p> : null;
}

/** Small horizontal bar chart for distributions (no chart library needed). */
export function Bars({ rows, format = (v: number) => String(v), stacked = false }: { rows: { label: string; value: number; tone?: string }[]; format?: (v: number) => string; stacked?: boolean }) {
  const max = Math.max(1e-9, ...rows.map((r) => r.value));
  if (stacked) {
    return (
      <div className="bars-stacked">
        {rows.map((r) => (
          <div key={r.label} className="bar-row">
            <div className="bar-label"><span>{r.label}</span><span className="num">{format(r.value)}</span></div>
            <Meter value={r.value} max={max} tone={r.tone as "warn" | "bad" | undefined} />
          </div>
        ))}
      </div>
    );
  }
  return (
    <div className="stack-sm">
      {rows.map((r) => (
        <div key={r.label} className="prob-bar">
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={r.label}>
            {r.label}
          </span>
          <Meter value={r.value} max={max} tone={r.tone as "warn" | "bad" | undefined} />
          <span className="num body-sm" style={{ textAlign: "right" }}>
            {format(r.value)}
          </span>
        </div>
      ))}
    </div>
  );
}

/** Sparkline / mini line chart for a numeric series. */
export function Sparkline({ values, width = 220, height = 48, threshold }: { values: number[]; width?: number; height?: number; threshold?: number }) {
  if (values.length < 2) return <span className="caption">Not enough history yet</span>;
  const max = Math.max(...values, threshold ?? 0) || 1;
  const min = Math.min(0, ...values);
  const x = (i: number) => (i / (values.length - 1)) * (width - 4) + 2;
  const y = (v: number) => height - 2 - ((v - min) / (max - min || 1)) * (height - 4);
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  return (
    <svg width={width} height={height} role="img" aria-label="trend">
      {threshold !== undefined && (
        <line x1={0} x2={width} y1={y(threshold)} y2={y(threshold)} stroke="var(--down)" strokeDasharray="3 3" strokeWidth={1} />
      )}
      <path d={d} fill="none" stroke="var(--primary)" strokeWidth={2} strokeLinejoin="round" />
      <circle cx={x(values.length - 1)} cy={y(values[values.length - 1])} r={3} fill="var(--primary)" />
    </svg>
  );
}
