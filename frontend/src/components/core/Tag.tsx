import type { ReactNode } from "react";

export interface TagProps {
  /** Tag is always gold-tinted in the source CSS; kept as a prop for future variants. */
  gold?: boolean;
  children: ReactNode;
}

export function Tag({ gold = true, children }: TagProps) {
  return <span className={gold ? "tag tag-gold" : "tag"}>{children}</span>;
}
