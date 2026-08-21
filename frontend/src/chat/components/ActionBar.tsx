"use client";

import { BranchIcon, RefreshIcon, ThumbsDownIcon, ThumbsUpIcon } from "./Icons";

/**
 * The LATEST-turn-only action bar. Regenerate appears only when the
 * eligibility gate passes; branch and feedback ride the same latest
 * completed assistant turn.
 */
export function ActionBar({
  showRegenerate,
  feedbackSent,
  feedbackFailed,
  disabled,
  onRegenerate,
  onBranch,
  onFeedback,
}: {
  showRegenerate: boolean;
  feedbackSent: 1 | -1 | null;
  feedbackFailed: boolean;
  disabled: boolean;
  onRegenerate: () => void;
  onBranch: () => void;
  onFeedback: (score: 1 | -1) => void;
}) {
  return (
    <div className="msg-actions" data-testid="action-bar">
      {showRegenerate && (
        <button
          className="msg-action-btn"
          title="Regenerate"
          aria-label="Regenerate"
          disabled={disabled}
          onClick={() => onRegenerate()}
        >
          <RefreshIcon />
        </button>
      )}
      <button
        className="msg-action-btn"
        title="Branch into a new thread"
        aria-label="Branch into a new thread"
        disabled={disabled}
        onClick={() => onBranch()}
      >
        <BranchIcon />
      </button>
      <button
        className={`msg-action-btn${feedbackSent === 1 ? " up-active" : ""}`}
        title="Good response"
        aria-label="Good response"
        disabled={disabled || feedbackFailed || feedbackSent !== null}
        onClick={() => onFeedback(1)}
      >
        <ThumbsUpIcon />
      </button>
      <button
        className={`msg-action-btn${feedbackSent === -1 ? " down-active" : ""}`}
        title="Bad response"
        aria-label="Bad response"
        disabled={disabled || feedbackFailed || feedbackSent !== null}
        onClick={() => onFeedback(-1)}
      >
        <ThumbsDownIcon />
      </button>
    </div>
  );
}
