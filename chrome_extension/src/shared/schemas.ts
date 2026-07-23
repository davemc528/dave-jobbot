export interface ExtensionMessage {
  type:
    | "capture_current_page"
    | "discover"
    | "fill"
    | "highlight-submit"
    | "stop";
  payload?: unknown;
}

export function isExtensionMessage(value: unknown): value is ExtensionMessage {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.type === "string" &&
    ["capture_current_page", "discover", "fill", "highlight-submit", "stop"].includes(
      candidate.type
    )
  );
}

export function isCaptureRequest(
  value: unknown
): value is {
  capture_id: string;
  force_refresh: true;
  requested_at: string;
  tabKey: string;
  max_wait_ms?: number;
} {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.capture_id === "string" &&
    candidate.capture_id.length >= 16 &&
    candidate.force_refresh === true &&
    typeof candidate.requested_at === "string" &&
    typeof candidate.tabKey === "string" &&
    (candidate.max_wait_ms === undefined ||
      (typeof candidate.max_wait_ms === "number" &&
        candidate.max_wait_ms >= 100 &&
        candidate.max_wait_ms <= 10_000))
  );
}

export function isFillPlan(value: unknown): value is { fields: unknown[] } {
  return (
    typeof value === "object" &&
    value !== null &&
    Array.isArray((value as Record<string, unknown>).fields)
  );
}
