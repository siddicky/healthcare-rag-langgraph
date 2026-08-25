/**
 * The coach wire protocol constants — the member-perimeter contract
 * (`healthcare_rag/agent/perimeter.py`) mirrored client-side.
 *
 * Every request body is built from the constant objects here so tests can
 * assert their exact shapes; the perimeter rejects ANY extra key.
 */

/** The server-defined document-review sentinel (perimeter.DOCUMENT_REVIEW_QUESTION). */
export const SENTINEL_QUESTION = "Please review this document.";

/** The erase-confirmation marker message `name` (AI message; v19 contract). */
export const ERASE_MARKER_NAME = "erase_confirmation_v1";

/** The fixed scrubbed content of the erase-confirmation marker. */
export const ERASE_MARKER_CONTENT = "All saved data erased.";

/** Thread status values from the Agent Server Thread schema. */
export type ThreadStatus = "idle" | "busy" | "interrupted" | "error";

/**
 * Node updates the chat may render. Updates from any other node are dropped
 * (with telemetry) — unknown nodes never render.
 */
export const RENDERED_NODE_NAMES = [
  "coach_gate",
  "coach_agent",
  "erase_my_data",
  "reminder_delivery",
  "claim_document",
  "review_document",
  "finalize_coach",
] as const;

export type RenderedNodeName = (typeof RENDERED_NODE_NAMES)[number];

const RENDERED_NODES: readonly string[] = RENDERED_NODE_NAMES;

export function isRenderedNode(value: unknown): value is RenderedNodeName {
  return typeof value === "string" && RENDERED_NODES.includes(value);
}

/**
 * The projection the perimeter allows for threads/search `select` —
 * exactly the five fields the server validates.
 */
export const THREAD_SELECT_FIELDS = [
  "thread_id",
  "created_at",
  "updated_at",
  "metadata",
  "status",
] as const;

const UUID_PATH_SEGMENT =
  "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
export const HISTORY_PATH = new RegExp(`^/threads/${UUID_PATH_SEGMENT}/history$`);
export const JOIN_PATH = new RegExp(
  `^/threads/${UUID_PATH_SEGMENT}/runs/${UUID_PATH_SEGMENT}/join$`,
);
export const JOIN_STREAM_PATH = new RegExp(
  `^/threads/${UUID_PATH_SEGMENT}/runs/${UUID_PATH_SEGMENT}/join/stream$`,
);
export const CANCEL_PATH = new RegExp(
  `^/threads/${UUID_PATH_SEGMENT}/runs/${UUID_PATH_SEGMENT}/cancel$`,
);

/**
 * The ONLY channels the chat reads out of the projected latest-state
 * response `{values, interrupts}`. Everything else in `values` is ignored
 * (and the private channels never cross the perimeter at all).
 */
export const STATE_VALUE_KEYS = ["messages"] as const;

/** Run envelope fixed values (perimeter `_RUN_FIXED`), in SDK payload spelling. */
export const RUN_ASSISTANT_ID = "coach";
export interface RunStreamFixedParams {
  streamMode: ["updates"];
  streamSubgraphs: false;
  streamResumable: false;
  durability: "exit";
  ifNotExists: "reject";
  multitaskStrategy: "reject";
}
export const RUN_STREAM_PARAMS: RunStreamFixedParams = {
  streamMode: ["updates"],
  streamSubgraphs: false,
  streamResumable: false,
  durability: "exit",
  ifNotExists: "reject",
  multitaskStrategy: "reject",
};

export interface RunStreamFixedParamsV2 {
  streamMode: ["updates", "messages"];
  streamSubgraphs: false;
  streamResumable: true;
  durability: "exit";
  ifNotExists: "reject";
  multitaskStrategy: "enqueue";
}

export const RUN_STREAM_PARAMS_V2: RunStreamFixedParamsV2 = {
  streamMode: ["updates", "messages"],
  streamSubgraphs: false,
  streamResumable: true,
  durability: "exit",
  ifNotExists: "reject",
  multitaskStrategy: "enqueue",
};

export function getRunStreamParams(): RunStreamFixedParams | RunStreamFixedParamsV2 {
  return process.env.NEXT_PUBLIC_HC_RAG_MEMBER_STREAM_PERIMETER === "v2"
    ? RUN_STREAM_PARAMS_V2
    : RUN_STREAM_PARAMS;
}

/** A member run input: `{question}` or `{question: SENTINEL, attachment_id}`. */
export type RunInput = {
  question: string;
  attachment_id?: string;
};

/** The unified resume payload: `{accept, fields?}` (perimeter `_validate_resume`). */
export interface ResumePayload {
  accept: boolean;
  fields?: { key: string; value: string }[];
}

/** Tool families that mutate member state — never re-runnable via regenerate. */
export const MUTATING_TOOL_PREFIXES = ["log_", "change_schedule", "remember_fact", "create_reminder", "edit_reminder", "cancel_reminder", "set_reminder"] as const;

/** Document upload lifecycle stages (upload registry status values). */
export type UploadStage = "uploading" | "scanning" | "extracting" | "done";

export function isUploadStage(value: unknown): value is UploadStage {
  return (
    typeof value === "string" &&
    (value === "uploading" || value === "scanning" || value === "extracting" || value === "done")
  );
}

/** Conversation openers (ui_kits/coach-chat list, attachment opener included). */
export const OPENERS = [
  "Log today's weight",
  "Log my injection",
  "What's on my calendar this month?",
  "Move my Friday check-in",
  "Upload my intake form",
  "Set a weekly weigh-in reminder",
] as const;

/** The opener that opens the attach flow instead of sending text. */
export const UPLOAD_OPENER = "Upload my intake form";
