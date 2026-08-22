import type { ReactNode } from "react";

export interface LabelProps {
  /** Uppercase eyebrow text. gold uses the gold color instead of camel. */
  gold?: boolean;
  children: ReactNode;
}

export function Label({ gold = false, children }: LabelProps) {
  return <span className={gold ? "label label-gold" : "label"}>{children}</span>;
}
