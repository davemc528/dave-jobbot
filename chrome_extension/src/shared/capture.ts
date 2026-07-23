import { STORAGE_KEYS } from "./messages";

export const TRANSIENT_CAPTURE_KEYS = [
  STORAGE_KEYS.cachedExtraction,
  STORAGE_KEYS.extractionPreview,
  STORAGE_KEYS.lastCapturePayload,
  STORAGE_KEYS.lastCaptureError,
  STORAGE_KEYS.extractionConfidence,
  STORAGE_KEYS.capturedAt
] as const;

export function newCaptureRequest(tabKey: string, maxWaitMs = 9_000): {
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

export class FreshCaptureLifecycle {
  currentCaptureId?: string;
  busy = false;

  begin(tabKey: string, maxWaitMs = 9_000): ReturnType<typeof newCaptureRequest> {
    const request = newCaptureRequest(tabKey, maxWaitMs);
    this.currentCaptureId = request.capture_id;
    this.busy = true;
    return request;
  }

  finish(captureId: string): boolean {
    if (this.currentCaptureId !== captureId) return false;
    this.busy = false;
    return true;
  }

  cancel(): void {
    this.currentCaptureId = undefined;
    this.busy = false;
  }
}
