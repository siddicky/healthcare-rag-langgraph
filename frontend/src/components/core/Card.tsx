import type { CSSProperties, ReactNode } from "react";

export interface CardProps {
  /** 'elevated' raises shadow-md and lifts 4px on hover; 'birch' is a flat tinted surface; leave unset for the base white card. */
  variant?: "elevated" | "birch";
  /** Adds a 2px solid carrot border. */
  bordered?: boolean;
  /** Uses 48px padding and a 20px radius instead of the default 32px/16px. */
  large?: boolean;
  hoverLift?: boolean;
  children: ReactNode;
  style?: CSSProperties;
}

export function Card({
  variant,
  bordered = false,
  large = false,
  hoverLift = true,
  children,
  style,
}: CardProps) {
  const cls = [
    "card",
    variant ? `card-${variant}` : "",
    bordered ? "card-bordered" : "",
    large ? "card-lg" : "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <div className={cls} style={{ ...(hoverLift ? {} : { transition: "none" }), ...style }}>
      {children}
    </div>
  );
}
