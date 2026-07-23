import { JSDOM } from "jsdom";
import {
  FreshCaptureLifecycle,
  newCaptureRequest,
  responseMatchesCapture,
  selectTopFrameCaptureResult,
  TRANSIENT_CAPTURE_KEYS
} from "../src/shared/capture";
import { waitForRenderedContent } from "../src/content/stabilization";
import { DeadlineError, withDeadline } from "../src/shared/timeouts";

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

  it("selects the serialized top-frame executeScript result", () => {
    const response = selectTopFrameCaptureResult(
      [
        {
          frameId: 0,
          result: { ok: true, captureId: "current-id", result: { full_text: "job" } }
        }
      ],
      "current-id"
    );
    expect(response.ok).toBe(true);
  });

  it("turns empty and stale executeScript results into structured failures", () => {
    const missing = selectTopFrameCaptureResult([], "current-id");
    expect(missing).toMatchObject({
      ok: false,
      error: { code: "missing_content_script_response" }
    });
    const stale = selectTopFrameCaptureResult(
      [{ frameId: 0, result: { ok: true, captureId: "old-id" } }],
      "current-id"
    );
    expect(stale).toMatchObject({
      ok: false,
      error: { code: "stale_capture_response" }
    });
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
    expect(result.finalTextLength).toBeGreaterThan(10);
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
    expect(result.meaningfulMutationCount).toBeGreaterThan(0);
  });

  it("ignores continuous irrelevant mutations outside the job container", async () => {
    const dom = new JSDOM("<header>Clock</header><main>Stable job description</main>");
    Object.defineProperty(dom.window.document, "readyState", {
      configurable: true,
      value: "complete"
    });
    const interval = setInterval(() => {
      dom.window.document.querySelector("header")!.textContent = String(Date.now());
    }, 10);
    const result = await waitForRenderedContent(dom.window.document, {
      stableMs: 60,
      sampleMs: 10,
      maxWaitMs: 300
    });
    clearInterval(interval);
    expect(result.stable).toBe(true);
    expect(result.timedOut).toBe(false);
    expect(result.meaningfulMutationCount).toBe(0);
  });

  it("disconnects its observer when stabilization completes", async () => {
    const dom = new JSDOM("<main>Stable job description</main>");
    Object.defineProperty(dom.window.document, "readyState", {
      configurable: true,
      value: "complete"
    });
    const NativeObserver = dom.window.MutationObserver;
    let disconnected = false;
    class TrackingObserver extends NativeObserver {
      override disconnect(): void {
        disconnected = true;
        super.disconnect();
      }
    }
    Object.defineProperty(dom.window, "MutationObserver", {
      configurable: true,
      value: TrackingObserver
    });
    await waitForRenderedContent(dom.window.document, {
      stableMs: 30,
      sampleMs: 5,
      maxWaitMs: 100
    });
    expect(disconnected).toBe(true);
  });

  it("runs timeout cleanup so a bridge request can be aborted", async () => {
    let aborted = false;
    await expect(
      withDeadline(
        new Promise<never>(() => undefined),
        20,
        "bridge_timeout",
        "Bridge timed out",
        () => {
          aborted = true;
        }
      )
    ).rejects.toEqual(expect.objectContaining<Partial<DeadlineError>>({
      code: "bridge_timeout"
    }));
    expect(aborted).toBe(true);
  });
});
