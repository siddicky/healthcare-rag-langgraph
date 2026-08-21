"use client";

import { useState, type KeyboardEvent } from "react";

export interface ExtractedField {
  key: string;
  label: string;
  value: string;
  /** Flags a low-confidence extraction for the member to double check before saving. */
  needsReview?: boolean;
}

export interface ResolvedMemoryField extends ExtractedField {
  status: "saved" | "discarded";
  notice?: string;
}

export interface MemoryExtractionCardProps {
  /** e.g. "intake-form.pdf" */
  sourceLabel: string;
  fields?: ExtractedField[];
  /** Post-decision render: per-field saved/discarded state, no editing, no buttons. */
  resolvedFields?: ResolvedMemoryField[];
  /** Called with the (possibly member-edited) fields when they accept. */
  onSave?: (fields: ExtractedField[]) => void;
  onDiscard?: () => void;
}

function FieldRow({
  label,
  valueNode,
  needsReview,
  trailing,
  notice,
}: {
  label: string;
  valueNode: React.ReactNode;
  needsReview?: boolean;
  trailing?: React.ReactNode;
  notice?: string;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        paddingBottom: 8,
        borderBottom: "1px solid var(--rust-10)",
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: 12,
            color: "var(--camel)",
            marginBottom: 2,
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          {label}
          {needsReview && (
            <span className="tag" style={{ fontSize: 10, padding: "2px 8px" }}>
              Check this
            </span>
          )}
        </div>
        <div style={{ fontSize: 15, color: "var(--rust)", fontWeight: 500 }}>{valueNode}</div>
        {notice && <div style={{ fontSize: 12, color: "var(--camel)", marginTop: 2 }}>{notice}</div>}
      </div>
      {trailing}
    </div>
  );
}

export function MemoryExtractionCard({
  sourceLabel,
  fields = [],
  resolvedFields,
  onSave,
  onDiscard,
}: MemoryExtractionCardProps) {
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(fields.map((f) => [f.key, f.value])),
  );
  const [editingKey, setEditingKey] = useState<string | null>(null);

  const resolved = resolvedFields !== undefined;

  return (
    <div className="card card-elevated" style={{ maxWidth: 380 }}>
      <span className="label" style={{ marginBottom: 4 }}>
        Found in {sourceLabel}
      </span>
      <p style={{ margin: "0 0 14px", fontSize: 14, color: "var(--rust)", opacity: 0.85 }}>
        {resolved ? "Here's what I did with each detail." : "Review before I save these to your profile."}
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 16 }}>
        {resolved
          ? resolvedFields.map((f) => (
              <FieldRow
                key={f.key}
                label={f.label}
                needsReview={f.needsReview}
                notice={f.notice}
                valueNode={f.value}
                trailing={
                  <span
                    style={{
                      fontSize: 12,
                      fontWeight: 600,
                      color: f.status === "saved" ? "var(--success)" : "var(--camel)",
                      flexShrink: 0,
                    }}
                  >
                    {f.status === "saved" ? "✓ Saved" : "Discarded"}
                  </span>
                }
              />
            ))
          : fields.map((f) =>
              editingKey === f.key ? (
                <FieldRow
                  key={f.key}
                  label={f.label}
                  needsReview={f.needsReview}
                  valueNode={
                    <input
                      className="form-input"
                      style={{ padding: "6px 10px", fontSize: 14 }}
                      value={values[f.key] ?? ""}
                      autoFocus
                      onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
                      onBlur={() => setEditingKey(null)}
                      onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
                        if (e.key === "Enter") setEditingKey(null);
                      }}
                    />
                  }
                />
              ) : (
                <FieldRow
                  key={f.key}
                  label={f.label}
                  needsReview={f.needsReview}
                  valueNode={values[f.key] ?? ""}
                  trailing={
                    <button
                      aria-label={`Edit ${f.label}`}
                      onClick={() => setEditingKey(f.key)}
                      style={{
                        border: "none",
                        background: "transparent",
                        cursor: "pointer",
                        color: "var(--camel)",
                        padding: 4,
                        flexShrink: 0,
                      }}
                    >
                      <svg
                        width="15"
                        height="15"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.8"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <path d="M17 3a2.85 2.85 0 114 4L7.5 20.5 2 22l1.5-5.5z" />
                      </svg>
                    </button>
                  }
                />
              ),
            )}
      </div>
      {!resolved && (
        <div className="btn-group">
          <button
            className="btn btn-primary btn-sm"
            onClick={() => {
              if (onSave) onSave(fields.map((f) => ({ ...f, value: values[f.key] ?? f.value })));
            }}
          >
            Save to profile
          </button>
          <button
            className="btn btn-secondary btn-sm"
            onClick={() => {
              if (onDiscard) onDiscard();
            }}
          >
            Discard
          </button>
        </div>
      )}
    </div>
  );
}
