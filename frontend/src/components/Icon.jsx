// Minimal geometric icon set (1.75px strokes, 24px grid) — matches the design system's restraint.
const PATHS = {
  wifi: "M2 9a15 15 0 0 1 20 0M5.5 12.5a10 10 0 0 1 13 0M9 16a5 5 0 0 1 6 0M12 19.5h.01",
  phone: "M7 2h10a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1zM11 18h2",
  sim: "M7 2h7l5 5v14a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1zM9 12h6v6H9z",
  card: "M3 6h18v12H3zM3 10h18M7 15h3",
  tv: "M3 6h18v11H3zM8 21h8M12 17v4",
  dot: "M12 12h.01",
  check: "M5 12.5l4.5 4.5L19 7.5",
  x: "M6 6l12 12M18 6L6 18",
  chat: "M4 5h16v11H9l-5 4z",
  arrow: "M5 12h14M13 6l6 6-6 6",
  back: "M19 12H5M11 18l-6-6 6-6",
  spark: "M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0",
  inbox: "M3 13l3-8h12l3 8v6H3zM3 13h5l1 2h6l1-2h5",
  book: "M4 4h7a2 2 0 0 1 2 2v14a2 2 0 0 0-2-2H4zM20 4h-7a2 2 0 0 0-2 2v14a2 2 0 0 1 2-2h7z",
  tree: "M12 3v6M6 15v-3h12v3M6 15v3M18 15v3M12 9v3",
  pulse: "M3 12h4l3-7 4 14 3-7h4",
  grid: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  alert: "M12 3l9 16H3zM12 10v4M12 17h.01",
  play: "M7 5l12 7-12 7z",
  mail: "M3 6h18v12H3zM3 7l9 6 9-6",
  radar: "M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0M12 12m-5 0a5 5 0 1 0 10 0a5 5 0 1 0-10 0M12 12l6-6",
  plus: "M12 5v14M5 12h14",
  refresh: "M20 11a8 8 0 0 0-14.8-4M4 4v4h4M4 13a8 8 0 0 0 14.8 4M20 20v-4h-4",
  logout: "M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10",
  shield: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z",
  bolt: "M13 2L4 14h7l-1 8 9-12h-7z",
};
export function Icon({ name, size = 20, stroke = 1.75, className }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d={PATHS[name] ?? PATHS.dot} />
    </svg>
  );
}
export function BrandMark({ size = 28 }) {
  return (
    <span className="brand-mark" style={{ width: size, height: size }}>
      <Icon name="check" size={size * 0.6} stroke={2.6} className="" />
    </span>
  );
}
