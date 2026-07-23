export interface ExtensionMessage {
  type: "extract" | "discover" | "fill" | "highlight-submit" | "stop";
  payload?: unknown;
}

export function isExtensionMessage(value: unknown): value is ExtensionMessage {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.type === "string" &&
    ["extract", "discover", "fill", "highlight-submit", "stop"].includes(candidate.type)
  );
}

export function isFillPlan(value: unknown): value is { fields: unknown[] } {
  return (
    typeof value === "object" &&
    value !== null &&
    Array.isArray((value as Record<string, unknown>).fields)
  );
}
