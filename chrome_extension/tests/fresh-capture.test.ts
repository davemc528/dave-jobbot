import { JSDOM } from "jsdom";
import {
  FreshCaptureLifecycle,
  newCaptureRequest,
  responseMatchesCapture,
  TRANSIENT_CAPTURE_KEYS
} from "../src/shared/capture";
import { waitForRenderedContent } from "../src/content/stabilization";

describe("fresh capture lifecycle", () => {
  it("creates a new capture ID for every click-equivalent request", () => {
    const first = newCaptureRequest("7");
    const second = newCaptureRequest("7");
    expect(first.capture_id).not.toBe(second.capture_id);
    expect(first.force_refresh).toBe(true);
  });

  it("enumerates extraction cache fields without pairing or host state", () => {
    expect(TRANSIENT_CAPTURE_KEYS).toContain("cachedExtraction");
    expect(TRANSIENT_CAPTURE_KEYS).toContain("lastCaptureError");
    expect(TRANSIENT_CAPTURE_KEYS).not.toContain("bridgeToken");
    expect(TRANSIENT_CAPTURE_KEYS).not.toContain("approvedHosts");
  });

  it("rejects stale responses", () => {
    expect(responseMatchesCapture("new-id", "old-id")).toBe(false);
    expect(responseMatchesCapture("new-id", "new-id")).toBe(true);
  });

  it("re-enables capture after failure and permits retry on the same tab", () => {
    const lifecycle = new FreshCaptureLifecycle();
    const failed = lifecycle.begin("7");
    expect(lifecycle.busy).toBe(true);
    expect(lifecycle.finish(failed.capture_id)).toBe(true);
    expect(lifecycle.busy).toBe(false);
    const retry = lifecycle.begin("7");
    expect(retry.capture_id).not.toBe(failed.capture_id);
    expect(lifecycle.busy).toBe(true);
  });

  it("stabilizes once newly inserted text stops changing", async () => {
    const dom = new JSDOM("<main>Loading</main>");
    Object.defineProperty(dom.window.document, "readyState", {
      configurable: true,
      value: "complete"
    });
    setTimeout(() => {
      dom.window.document.querySelector("main")!.textContent = "Rendered posting content";
    }, 20);
    const result = await waitForRenderedContent(dom.window.document, {
      stableMs: 60,
      sampleMs: 10,
      maxWaitMs: 500
    });
    expect(result.timedOut).toBe(false);
    expect(result.visibleTextLength).toBeGreaterThan(10);
  });

  it("times out safely while content continues mutating", async () => {
    const dom = new JSDOM("<main>Loading</main>");
    Object.defineProperty(dom.window.document, "readyState", {
      configurable: true,
      value: "complete"
    });
    const interval = setInterval(() => {
      dom.window.document.querySelector("main")!.textContent += ".";
    }, 10);
    const result = await waitForRenderedContent(dom.window.document, {
      stableMs: 80,
      sampleMs: 10,
      maxWaitMs: 100
    });
    clearInterval(interval);
    expect(result.timedOut).toBe(true);
    expect(result.stillMutating).toBe(true);
  });
});
