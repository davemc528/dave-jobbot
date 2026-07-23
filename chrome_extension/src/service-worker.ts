import { isExtensionMessage } from "./shared/schemas";
import { responseMatchesCapture } from "./shared/capture";

const activeCaptures = new Map<number, string>();

chrome.runtime.onMessage.addListener((message: unknown, sender, sendResponse) => {
  if (!isExtensionMessage(message)) {
    sendResponse({ error: "invalid_message" });
    return false;
  }
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
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["content.js"]
    });
    const response = await chrome.tabs.sendMessage(tab.id, message);
    if (
      typeof captureId === "string" &&
      (!responseMatchesCapture(activeCaptures.get(tab.id), captureId) ||
        typeof response !== "object" ||
        response === null ||
        !responseMatchesCapture(
          captureId,
          (response as Record<string, unknown>).capture_id
        ))
    ) {
      sendResponse({ error: "stale_capture_response", capture_id: captureId });
      return;
    }
    sendResponse({ ...response, tabId: tab.id, tabUrl: tab.url });
  })().catch((error: unknown) => {
    sendResponse({ error: error instanceof Error ? error.message : "service_worker_error" });
  });
  return true;
});
