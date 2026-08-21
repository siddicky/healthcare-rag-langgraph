import { describe, expect, it } from "vitest";
import {
  applyUploadEvent,
  documentStage,
  formatFileSize,
  shouldPollStatus,
  type UploadUi,
} from "@/chat/uploadFlow";

const info = {
  uploadId: "00000000-0000-4000-8000-000000000001",
  threadId: "11111111-1111-4111-8111-111111111111",
  fileName: "intake-form.pdf",
  fileSizeLabel: "182 KB",
};

function drive(events: Parameters<typeof applyUploadEvent>[1][]): UploadUi {
  let state: UploadUi = { phase: "idle" };
  for (const event of events) state = applyUploadEvent(state, event);
  return state;
}

describe("document ingest stage driver (fixture event sequences)", () => {
  it("progresses uploading → scanning → extracting → done from server stages only", () => {
    const state = drive([
      { kind: "started", info },
      { kind: "stage", stage: "uploading" },
      { kind: "stage", stage: "scanning" },
      { kind: "stage", stage: "extracting" },
      { kind: "stage", stage: "done" },
    ]);
    expect(state.phase).toBe("staged");
    expect(state.phase === "staged" && state.stage).toBe("done");
    expect(documentStage(state)).toBe("done");
  });

  it("stops polling once done — never polls after done/error", () => {
    const done = drive([
      { kind: "started", info },
      { kind: "stage", stage: "done" },
    ]);
    expect(shouldPollStatus(done)).toBe(false);
    expect(shouldPollStatus(drive([{ kind: "started", info }, { kind: "error", detail: "bad file" }]))).toBe(false);
    expect(shouldPollStatus(drive([{ kind: "started", info }, { kind: "stage", stage: "scanning" }]))).toBe(true);
    expect(shouldPollStatus({ phase: "inflight", info })).toBe(false);
    expect(shouldPollStatus({ phase: "idle" })).toBe(false);
  });

  it("renders `error` outside the card contract — as plain failure state", () => {
    const state = drive([
      { kind: "started", info },
      { kind: "stage", stage: "scanning" },
      { kind: "error", detail: "File content does not match its media type" },
    ]);
    expect(state.phase).toBe("failed");
    expect(state.phase === "failed" && state.detail).toBe("File content does not match its media type");
    expect(documentStage(state)).toBeNull();
  });

  it("ignores non-stage payloads and late events after failure", () => {
    const state = drive([
      { kind: "started", info },
      { kind: "error", detail: "expired" },
      { kind: "stage", stage: "done" },
    ]);
    expect(state.phase).toBe("failed");
    const weird = drive([{ kind: "started", info }, { kind: "stage", stage: 42 }]);
    expect(weird.phase).toBe("inflight");
  });

  it("consumes the attachment when the review turn is sent", () => {
    const state = drive([{ kind: "started", info }, { kind: "stage", stage: "done" }, { kind: "consumed" }]);
    expect(state.phase).toBe("idle");
  });

  it("formats human file sizes", () => {
    expect(formatFileSize(182_000)).toBe("178 KB");
    expect(formatFileSize(2_500_000)).toBe("2.4 MB");
    expect(formatFileSize(900)).toBe("900 B");
  });
});
