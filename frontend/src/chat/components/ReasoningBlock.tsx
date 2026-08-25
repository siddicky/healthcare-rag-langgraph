"use client";

import { useState } from "react";

export function ReasoningBlock({ reasoning }: { reasoning: string }) {
  const [open, setOpen] = useState(false);
  const trimmed = reasoning.trim();
  if (trimmed === "") return null;
  return (
    <div
      data-testid="reasoning-block"
      style={{
        marginBottom: "var(--space-xs)",
        border: "1px solid var(--rust-10)",
        borderRadius: "var(--border-radius-sm)",
        background: "var(--birch)",
        overflow: "hidden",
      }}
    >
      <button
        type="button"
        data-testid="reasoning-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "8px 10px",
          border: "none",
          background: "transparent",
          cursor: "pointer",
          fontFamily: "var(--font-body)",
          fontSize: 12,
          fontWeight: 600,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
          color: "var(--camel)",
          textAlign: "left",
        }}
      >
        <span
          aria-hidden
          style={{
            width: 20,
            height: 20,
            borderRadius: 6,
            background: "var(--gold-20)",
            color: "var(--camel)",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
            transition: "transform var(--transition-fast)",
            transform: open ? "rotate(90deg)" : "rotate(0deg)",
          }}
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M9 18l6-6-6-6" />
          </svg>
        </span>
        {open ? "Hide reasoning" : "Show reasoning"}
        <span style={{ marginLeft: "auto", fontSize: 11, fontWeight: 400, letterSpacing: "0.02em", textTransform: "none", opacity: 0.8 }}>{trimmed.length} chars</span>
      </button>
      {open && (
        <div
          data-testid="reasoning-content"
          style={{
            padding: "10px 12px",
            background: "var(--white)",
            borderTop: "1px solid var(--rust-10)",
            fontFamily: "var(--font-body)",
            fontSize: 13,
            lineHeight: "1.6",
            color: "var(--rust)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            maxHeight: 260,
            overflow: "auto",
          }}
        >
          {trimmed}
        </div>
      )}
    </div>
  );
}
