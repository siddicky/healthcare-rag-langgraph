export interface DividerProps {
  /** 1px hairline; gold uses the 30% gold tint. */
  gold?: boolean;
}

export function Divider({ gold = false }: DividerProps) {
  return <div className={gold ? "divider divider-gold" : "divider"} />;
}
