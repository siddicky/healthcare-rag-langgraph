export interface TimelineItem {
  week: string;
  title: string;
  desc: string;
}

export interface TimelineProps {
  items: TimelineItem[];
}

export function Timeline({ items = [] }: TimelineProps) {
  return (
    <div className="timeline">
      {items.map((it) => (
        <div className="timeline-item" key={it.week}>
          <div className="timeline-dot" />
          <div className="timeline-week">{it.week}</div>
          <div className="timeline-title">{it.title}</div>
          <div className="timeline-desc">{it.desc}</div>
        </div>
      ))}
    </div>
  );
}
