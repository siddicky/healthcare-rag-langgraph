export interface AccentLineProps {
  /** 60x3px gold accent bar used under section headers. */
  center?: boolean;
}

export function AccentLine({ center = false }: AccentLineProps) {
  return <div className={center ? "accent-line accent-line-center" : "accent-line"} />;
}
