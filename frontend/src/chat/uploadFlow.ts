import { isUploadStage, type UploadStage } from "./coachProtocol";

/**
 * Document upload state machine — stage progression comes ONLY from server
 * responses (POST /coach/uploads body or GET status polls); there are no
 * client-side stage timers, and polling NEVER continues after done/error.
 */

export interface UploadInfo {
  uploadId: string;
  threadId: string;
  fileName: string;
  fileSizeLabel: string;
}

export type UploadUi =
  | { phase: "idle" }
  | { phase: "inflight"; info: UploadInfo }
  | { phase: "staged"; info: UploadInfo; stage: UploadStage }
  | { phase: "failed"; info: UploadInfo; detail: string };

export type UploadEvent =
  | { kind: "started"; info: UploadInfo }
  | { kind: "stage"; stage: unknown }
  | { kind: "error"; detail: string }
  | { kind: "consumed" };

export function applyUploadEvent(state: UploadUi, event: UploadEvent): UploadUi {
  switch (event.kind) {
    case "started":
      return { phase: "inflight", info: event.info };
    case "stage":
      if (state.phase === "idle" || state.phase === "failed") return state;
      if (!isUploadStage(event.stage)) return state;
      return { phase: "staged", info: state.info, stage: event.stage };
    case "error":
      if (state.phase === "idle" || state.phase === "failed") return state;
      return { phase: "failed", info: state.info, detail: event.detail };
    case "consumed":
      return { phase: "idle" };
  }
}

/** Whether another status poll should be scheduled (never after done/error). */
export function shouldPollStatus(state: UploadUi): boolean {
  if (state.phase !== "staged") return false;
  return state.stage !== "done";
}

/** The DocumentIngestCard stage while an upload is visible. */
export function documentStage(state: UploadUi): UploadStage | null {
  if (state.phase === "inflight") return "uploading";
  if (state.phase === "staged") return state.stage;
  return null;
}

export function formatFileSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}
