export interface ScoreRingProps {
  /** 0-100, drawn as a conic-gradient ring. */
  score?: number;
  label?: string;
}

export function ScoreRing({ score = 78, label = "Health score" }: ScoreRingProps) {
  return (
    <div
      className="score-visual"
      style={{
        background: `conic-gradient(var(--carrot) 0deg, var(--gold) ${score * 3.6}deg, var(--gold-20) ${score * 3.6}deg, var(--gold-20) 360deg)`,
      }}
    >
      <div className="score-inner">
        <div className="score-number">{score}</div>
        <div className="score-label">{label}</div>
      </div>
    </div>
  );
}
