import { isExtensionMessage } from "./shared/schemas";

chrome.runtime.onMessage.addListener((message: unknown, sender, sendResponse) => {
  if (!isExtensionMessage(message)) {
    sendResponse({ error: "invalid_message" });
    return false;
  }
  void (async () => {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id || !tab.url) throw new Error("No active browser tab");
    if (sender.id !== chrome.runtime.id) throw new Error("Untrusted extension sender");
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["content.js"]
    });
    const response = await chrome.tabs.sendMessage(tab.id, message);
    sendResponse({ ...response, tabId: tab.id, tabUrl: tab.url });
  })().catch((error: unknown) => {
    sendResponse({ error: error instanceof Error ? error.message : "service_worker_error" });
  });
  return true;
});
