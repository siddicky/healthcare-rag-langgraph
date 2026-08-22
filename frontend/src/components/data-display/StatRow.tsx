import { Fragment } from "react";

export interface StatItem {
  value: string;
  label: string;
}

export interface StatRowProps {
  stats: StatItem[];
}

export function StatRow({ stats = [] }: StatRowProps) {
  return (
    <div className="stats-row">
      {stats.map((s, i) => (
        <Fragment key={s.label}>
          <div className="stat-item">
            <div className="stat-number">{s.value}</div>
            <div className="stat-label">{s.label}</div>
          </div>
          {i < stats.length - 1 && <div className="stat-divider" />}
        </Fragment>
      ))}
    </div>
  );
}
