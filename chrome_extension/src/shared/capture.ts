import { STORAGE_KEYS } from "./messages";

export type CaptureStage =
  | "idle"
  | "reading_tab"
  | "waiting_for_content"
  | "extracting"
  | "sending_to_bridge"
  | "analyzing"
  | "succeeded"
  | "low_confidence"
  | "failed"
  | "timed_out"
  | "cancelled";

export const TRANSIENT_CAPTURE_KEYS = [
  STORAGE_KEYS.cachedExtraction,
  STORAGE_KEYS.extractionPreview,
  STORAGE_KEYS.lastCapturePayload,
  STORAGE_KEYS.lastCaptureError,
  STORAGE_KEYS.extractionConfidence,
  STORAGE_KEYS.capturedAt
] as const;

export function newCaptureRequest(tabKey: string, maxWaitMs = 8_000): {
  action: "capture_current_page";
  capture_id: string;
  force_refresh: true;
  requested_at: string;
  tabKey: string;
  max_wait_ms: number;
} {
  return {
    action: "capture_current_page",
    capture_id: crypto.randomUUID(),
    force_refresh: true,
    requested_at: new Date().toISOString(),
    tabKey,
    max_wait_ms: maxWaitMs
  };
}

export function responseMatchesCapture(
  currentCaptureId: string | undefined,
  responseCaptureId: unknown
): boolean {
  return (
    typeof responseCaptureId === "string" &&
    currentCaptureId === responseCaptureId
  );
}

export function selectTopFrameCaptureResult(
  results: Array<{ frameId: number; result?: unknown }>,
  captureId: string
): Record<string, unknown> {
  const topFrame = results.find((entry) => entry.frameId === 0) ?? results[0];
  if (!topFrame || typeof topFrame.result !== "object" || topFrame.result === null) {
    return {
      ok: false,
      captureId,
      stage: "capture_failed",
      error: {
        code: "missing_content_script_response",
        message: "The injected capture entrypoint returned no result."
      }
    };
  }
  const response = topFrame.result as Record<string, unknown>;
  if (!responseMatchesCapture(captureId, response.captureId)) {
    return {
      ok: false,
      captureId,
      stage: "capture_failed",
      error: {
        code: "stale_capture_response",
        message: "The injected capture result did not match the active capture."
      }
    };
  }
  return response;
}

export class FreshCaptureLifecycle {
  currentCaptureId?: string;
  busy = false;
  stage: CaptureStage = "idle";
  startedAt?: number;
  stageStartedAt?: number;

  begin(tabKey: string, maxWaitMs = 8_000): ReturnType<typeof newCaptureRequest> {
    const request = newCaptureRequest(tabKey, maxWaitMs);
    this.currentCaptureId = request.capture_id;
    this.busy = true;
    this.startedAt = Date.now();
    this.transition("reading_tab");
    return request;
  }

  transition(stage: CaptureStage): void {
    this.stage = stage;
    this.stageStartedAt = Date.now();
  }

  finish(captureId: string): boolean {
    if (this.currentCaptureId !== captureId) return false;
    this.busy = false;
    return true;
  }

  cancel(): void {
    this.currentCaptureId = undefined;
    this.busy = false;
    this.transition("cancelled");
  }
}
