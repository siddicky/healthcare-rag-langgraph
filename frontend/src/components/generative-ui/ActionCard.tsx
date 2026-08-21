"use client";

export interface CardAction {
  label: string;
  onClick?: () => void;
}

export interface ActionCardProps {
  title: string;
  body?: string;
  primaryAction?: CardAction;
  secondaryAction?: CardAction;
}

export function ActionCard({ title, body, primaryAction, secondaryAction }: ActionCardProps) {
  return (
    <div className="card card-elevated" style={{ maxWidth: 340 }}>
      <h4 style={{ margin: "0 0 6px" }}>{title}</h4>
      {body && <p style={{ margin: "0 0 14px", fontSize: 15, opacity: 0.85 }}>{body}</p>}
      <div className="btn-group">
        {primaryAction && (
          <button className="btn btn-primary btn-sm" onClick={primaryAction.onClick}>
            {primaryAction.label}
          </button>
        )}
        {secondaryAction && (
          <button className="btn btn-secondary btn-sm" onClick={secondaryAction.onClick}>
            {secondaryAction.label}
          </button>
        )}
      </div>
    </div>
  );
}
