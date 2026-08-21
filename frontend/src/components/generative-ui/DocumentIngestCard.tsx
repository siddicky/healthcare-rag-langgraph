export interface DocumentIngestCardProps {
  fileName: string;
  fileSizeLabel?: string;
  /** Progression: 'uploading' (shows the bar fill via progress) -> 'scanning' -> 'extracting' -> 'done'. */
  stage: "uploading" | "scanning" | "extracting" | "done";
  /** 0-100, only meaningful during the 'uploading' stage; later stages render the bar full. */
  progress?: number;
}

const STAGE_COPY: Record<DocumentIngestCardProps["stage"], string> = {
  uploading: "Uploading…",
  scanning: "Scanning document…",
  extracting: "Extracting key details…",
  done: "Ready to review",
};

export function DocumentIngestCard({ fileName, fileSizeLabel, stage = "uploading", progress = 0 }: DocumentIngestCardProps) {
  const pct = stage === "uploading" ? Math.max(0, Math.min(100, progress)) : 100;
  return (
    <div className="card" style={{ maxWidth: 340 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
        <div
          style={{
            width: 40,
            height: 40,
            borderRadius: 10,
            background: "var(--gold-20)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="var(--carrot-accessible)"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
            <path d="M14 2v6h6" />
          </svg>
        </div>
        <div style={{ minWidth: 0 }}>
          <div
            style={{
              fontSize: 14,
              fontWeight: 600,
              color: "var(--rust)",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {fileName}
          </div>
          {fileSizeLabel && <div style={{ fontSize: 12, color: "var(--camel)" }}>{fileSizeLabel}</div>}
        </div>
      </div>
      <div style={{ height: 6, borderRadius: 3, background: "var(--rust-10)", overflow: "hidden", marginBottom: 10 }}>
        <div
          style={{
            height: "100%",
            width: `${pct}%`,
            background: stage === "done" ? "var(--success)" : "var(--carrot)",
            borderRadius: 3,
            transition: "width 0.3s ease",
          }}
        />
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontSize: 13,
          color: stage === "done" ? "var(--success)" : "var(--camel)",
          fontWeight: 500,
        }}
      >
        {(stage === "scanning" || stage === "extracting") && (
          <span
            style={{
              width: 14,
              height: 14,
              border: "2px solid var(--rust-10)",
              borderTopColor: "var(--carrot)",
              borderRadius: "50%",
              display: "inline-block",
              animation: "ds-spin 0.8s linear infinite",
            }}
          />
        )}
        {stage === "done" && <span>✓</span>}
        {STAGE_COPY[stage]}
      </div>
    </div>
  );
}
