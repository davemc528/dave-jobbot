import { extractPosting } from "./extractor";
import { discoverFields } from "./form-discovery";
import { fillFields } from "./form-fill";
import {
  authenticationDetected,
  captchaDetected,
  highlightTerminalControls
} from "./safety";
import { isExtensionMessage, isFillPlan } from "../shared/schemas";

let stopped = false;

chrome.runtime.onMessage.addListener((message: unknown, _sender, sendResponse) => {
  if (!isExtensionMessage(message)) {
    sendResponse({ error: "invalid_message" });
    return false;
  }
  try {
    if (message.type === "stop") {
      stopped = true;
      sendResponse({ status: "stopped" });
      return false;
    }
    if (message.type === "extract") {
      const tabKey =
        typeof message.payload === "object" &&
        message.payload !== null &&
        typeof (message.payload as Record<string, unknown>).tabKey === "string"
          ? String((message.payload as Record<string, unknown>).tabKey)
          : "active-tab";
      sendResponse({ payload: extractPosting(document, location, tabKey) });
      return false;
    }
    if (captchaDetected(document)) {
      sendResponse({ status: "human_intervention_required", reason: "captcha" });
      return false;
    }
    if (authenticationDetected(document)) {
      sendResponse({ status: "human_intervention_required", reason: "authentication" });
      return false;
    }
    if (message.type === "discover") {
      const terminalCount = highlightTerminalControls(document);
      sendResponse({
        status: terminalCount ? "stopped_before_submit" : "application_page_detected",
        fields: discoverFields(document),
        terminalCount
      });
      return false;
    }
    if (message.type === "highlight-submit") {
      sendResponse({ count: highlightTerminalControls(document) });
      return false;
    }
    if (message.type === "fill") {
      if (stopped) {
        sendResponse({ status: "stopped" });
        return false;
      }
      if (!isFillPlan(message.payload)) {
        sendResponse({ error: "invalid_fill_plan" });
        return false;
      }
      const result = fillFields(
        document,
        message.payload.fields as Parameters<typeof fillFields>[1]
      );
      sendResponse({ status: "filled", ...result });
      return false;
    }
  } catch (error) {
    sendResponse({ error: error instanceof Error ? error.message : "content_script_error" });
  }
  return false;
});
