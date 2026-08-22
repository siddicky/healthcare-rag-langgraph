export interface TrendCardProps {
  label: string;
  value: string;
  unit?: string;
  /** e.g. "-0.6 lb this week" — colored green/red via deltaGood. */
  delta?: string;
  deltaGood?: boolean;
  /** Sparkline series, oldest first. */
  points: number[];
}

export function TrendCard({ label, value, unit, delta, deltaGood = true, points = [] }: TrendCardProps) {
  const w = 220;
  const h = 56;
  const pad = 4;
  const max = Math.max(...points);
  const min = Math.min(...points);
  const norm = (v: number): number =>
    max === min ? h / 2 : h - pad - ((v - min) / (max - min)) * (h - pad * 2);
  const path = points.map((p, i) => `${(i / (points.length - 1)) * w},${norm(p)}`).join(" ");
  const deltaColor = deltaGood ? "var(--success)" : "var(--error)";
  return (
    <div className="card" style={{ maxWidth: 280 }}>
      <div className="label" style={{ marginBottom: 4 }}>
        {label}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 8 }}>
        <span style={{ fontFamily: "var(--font-headline)", fontSize: 32, color: "var(--carrot)" }}>
          {value}
          <span style={{ fontSize: 16 }}>{unit}</span>
        </span>
        {delta && (
          <span style={{ fontSize: 13, fontWeight: 600, color: deltaColor }}>{delta}</span>
        )}
      </div>
      {points.length > 1 && (
        <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
          <polyline
            points={path}
            fill="none"
            stroke="var(--carrot)"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      )}
    </div>
  );
}
