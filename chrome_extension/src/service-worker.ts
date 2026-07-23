import { isExtensionMessage } from "./shared/schemas";
import { responseMatchesCapture } from "./shared/capture";
import { withDeadline } from "./shared/timeouts";

const activeCaptures = new Map<number, string>();

chrome.runtime.onMessage.addListener((message: unknown, sender, sendResponse) => {
  if (!isExtensionMessage(message)) {
    sendResponse({ error: "invalid_message" });
    return false;
  }
  let responded = false;
  const respond = (value: object): void => {
    if (responded) return;
    responded = true;
    sendResponse(value);
  };
  void (async () => {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id || !tab.url) throw new Error("No active browser tab");
    if (sender.id !== chrome.runtime.id) throw new Error("Untrusted extension sender");
    const captureId =
      message.type === "capture_current_page" &&
      typeof message.payload === "object" &&
      message.payload !== null
        ? (message.payload as Record<string, unknown>).capture_id
        : undefined;
    if (typeof captureId === "string") activeCaptures.set(tab.id, captureId);
    await withDeadline(
      chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: ["content.js"]
      }),
      3_000,
      "content_injection_timeout",
      "The content script could not be injected before its deadline."
    );
    const response = await withDeadline(
      chrome.tabs.sendMessage(tab.id, message),
      12_000,
      "extension_message_timeout",
      "The page did not return a capture response in time."
    );
    if (
      typeof captureId === "string" &&
      (!responseMatchesCapture(activeCaptures.get(tab.id), captureId) ||
        typeof response !== "object" ||
        response === null ||
        !responseMatchesCapture(captureId, (response as Record<string, unknown>).captureId))
    ) {
      respond({
        ok: false,
        captureId,
        stage: "capture_failed",
        error: {
          code: "stale_capture_response",
          message: "A stale content-script response was ignored."
        }
      });
      return;
    }
    respond({ ...response, tabId: tab.id, tabUrl: tab.url });
  })().catch((error: unknown) => {
    respond({
      ok: false,
      captureId:
        typeof message.payload === "object" &&
        message.payload !== null &&
        "capture_id" in message.payload
          ? String(message.payload.capture_id)
          : "unknown",
      stage: "capture_failed",
      error: {
        code:
          typeof error === "object" && error !== null && "code" in error
            ? String(error.code)
            : "service_worker_error",
        message:
          error instanceof Error ? error.message : "The extension service worker failed."
      }
    });
  });
  return true;
});
