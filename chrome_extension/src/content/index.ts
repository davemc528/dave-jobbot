import { captureFreshPosting } from "./extractor";
import { discoverFields } from "./form-discovery";
import { fillFields } from "./form-fill";
import {
  authenticationDetected,
  captchaDetected,
  highlightTerminalControls
} from "./safety";
import {
  isCaptureRequest,
  isExtensionMessage,
  isFillPlan
} from "../shared/schemas";
import { withDeadline } from "../shared/timeouts";

interface JobbotContentGlobal {
  __daveJobbotContentInstalled?: boolean;
  __daveJobbotCaptureId?: string;
  __daveJobbotStopped?: boolean;
}

const contentState = globalThis as typeof globalThis & JobbotContentGlobal;

if (!contentState.__daveJobbotContentInstalled) {
  contentState.__daveJobbotContentInstalled = true;
  contentState.__daveJobbotStopped = false;

  chrome.runtime.onMessage.addListener((message: unknown, _sender, sendResponse) => {
    if (!isExtensionMessage(message)) {
      sendResponse({ error: "invalid_message" });
      return false;
    }
    if (message.type === "stop") {
      contentState.__daveJobbotStopped = true;
      contentState.__daveJobbotCaptureId = undefined;
      sendResponse({ status: "stopped" });
      return false;
    }
    if (message.type === "capture_current_page") {
      if (!isCaptureRequest(message.payload)) {
        sendResponse({ error: "invalid_capture_request" });
        return false;
      }
      const request = message.payload;
      let responded = false;
      const respond = (value: object): void => {
        if (responded) return;
        responded = true;
        sendResponse(value);
      };
      contentState.__daveJobbotStopped = false;
      contentState.__daveJobbotCaptureId = request.capture_id;
      void withDeadline(
        captureFreshPosting(document, location, request),
        10_000,
        "page_extraction_timeout",
        "Page extraction did not finish before its deadline."
      )
        .then((result) => {
          if (
            contentState.__daveJobbotStopped ||
            contentState.__daveJobbotCaptureId !== result.capture_id
          ) {
            respond({
              ok: false,
              captureId: result.capture_id,
              stage: "capture_cancelled",
              error: {
                code: "stale_capture_response",
                message: "A newer capture replaced this response."
              }
            });
            return;
          }
          respond({
            ok: true,
            captureId: result.capture_id,
            stage: result.short_content
              ? "capture_succeeded_low_confidence"
              : "capture_succeeded",
            result
          });
        })
        .catch((error: unknown) => {
          respond({
            ok: false,
            captureId: request.capture_id,
            stage: "capture_failed",
            error: {
              code:
                typeof error === "object" &&
                error !== null &&
                "code" in error
                  ? String(error.code)
                  : "content_script_error",
              message:
                error instanceof Error ? error.message : "Content-script capture failed."
            }
          });
        });
      return true;
    }
    try {
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
        if (contentState.__daveJobbotStopped) {
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
}
