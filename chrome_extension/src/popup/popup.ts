import { BRIDGE, STORAGE_KEYS } from "../shared/messages";
import { FreshCaptureLifecycle, TRANSIENT_CAPTURE_KEYS } from "../shared/capture";
import { exactHostname } from "../shared/urls";
import {
  CapturePayload,
  CaptureResult,
  ExtractionDiagnostics,
  FillInstruction
} from "../shared/types";

interface PopupState {
  jobId?: number;
  resumeId?: number;
  hostname?: string;
  capture?: CapturePayload;
  captureId?: string;
  diagnostics?: ExtractionDiagnostics;
  capturePending: boolean;
  withheld: Array<{ identifier: string; reason: string }>;
}

const state: PopupState = { withheld: [], capturePending: false };
const captureLifecycle = new FreshCaptureLifecycle();
const element = <T extends HTMLElement>(id: string): T => {
  const value = document.getElementById(id);
  if (!value) throw new Error(`Missing popup element: ${id}`);
  return value as T;
};

async function storedToken(): Promise<string | undefined> {
  const values = await chrome.storage.local.get(STORAGE_KEYS.token);
  return values[STORAGE_KEYS.token] as string | undefined;
}

async function api<T>(
  path: string,
  options: RequestInit = {},
  authenticated = true
): Promise<T> {
  const token = await storedToken();
  const response = await fetch(`${BRIDGE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(authenticated && token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers
    }
  });
  const body = await response.json() as Record<string, unknown>;
  if (!response.ok) throw new Error(JSON.stringify(body));
  return body as T;
}

function text(id: string, value: unknown): void {
  element(id).textContent = value === undefined || value === null || value === "" ? "—" : String(value);
}

function message(value: string): void {
  text("message", value);
}

async function activeTab(): Promise<chrome.tabs.Tab> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !tab.url) throw new Error("No active tab");
  return tab;
}

async function ensureHostApproved(hostname: string): Promise<boolean> {
  const result = await api<{ hosts: Array<{ hostname: string }> }>("/hosts");
  if (result.hosts.some((host) => host.hostname === hostname)) return true;
  state.hostname = hostname;
  text("host-request", hostname);
  element("host-approval").hidden = false;
  message("Exact-host approval is required before capture or filling.");
  return false;
}

async function sendToTab(type: string, payload?: unknown): Promise<Record<string, unknown>> {
  return chrome.runtime.sendMessage({ type, payload }) as Promise<Record<string, unknown>>;
}

async function checkBridge(): Promise<void> {
  try {
    await api("/health", {}, false);
    const badge = element("status");
    badge.textContent = "Bridge connected";
    badge.className = "badge connected";
  } catch {
    const badge = element("status");
    badge.textContent = "Bridge disconnected";
    badge.className = "badge disconnected";
  }
}

function setCaptureBusy(busy: boolean): void {
  state.capturePending = busy;
  element<HTMLButtonElement>("capture").disabled = busy;
  element<HTMLButtonElement>("refresh").disabled = busy;
  element<HTMLButtonElement>("capture-again").disabled = busy;
  element<HTMLButtonElement>("use-selection").disabled = busy;
}

function renderDiagnostics(diagnostics: ExtractionDiagnostics): void {
  state.diagnostics = diagnostics;
  element("diagnostics-output").textContent = JSON.stringify(diagnostics, null, 2);
}

async function saveCapture(payload: CapturePayload): Promise<void> {
  message("Analyzing job");
  text("stage", "Analyzing job");
  const result = await api<{
    job_id: number;
    existing: boolean;
    message: string;
    analysis: { overall_score: number; selected_track: string };
  }>("/jobs/capture", { method: "POST", body: JSON.stringify(payload) });
  state.jobId = result.job_id;
  const tab = await activeTab();
  const stored = await chrome.storage.local.get(STORAGE_KEYS.tabs);
  const associations = (stored[STORAGE_KEYS.tabs] ?? {}) as Record<string, unknown>;
  await chrome.storage.local.set({
    [STORAGE_KEYS.tabs]: {
      ...associations,
      [String(tab.id)]: { jobId: result.job_id, hostname: payload.hostname }
    },
    [STORAGE_KEYS.lastCapturePayload]: payload,
    [STORAGE_KEYS.extractionConfidence]: payload.extraction_confidence,
    [STORAGE_KEYS.capturedAt]: payload.captured_at
  });
  text("employer", payload.employer);
  text("job-title", payload.job_title);
  text("location", payload.location);
  text("ats", payload.ats_type);
  text("job-id", result.job_id);
  text("fit-score", result.analysis.overall_score);
  text("resume-state", result.analysis.selected_track);
  text("stage", "Analysis ready");
  element<HTMLButtonElement>("capture").textContent = "Capture Again and Reanalyze";
  message(
    result.existing
      ? `Existing job refreshed: Job ID ${result.job_id}`
      : `New job created: Job ID ${result.job_id}`
  );
}

async function capture(saveLowConfidence = false): Promise<void> {
  if (state.capturePending) return;
  setCaptureBusy(true);
  const pendingRequest = captureLifecycle.begin("pending");
  const captureId = pendingRequest.capture_id;
  state.captureId = captureId;
  state.capture = undefined;
  state.diagnostics = undefined;
  await chrome.storage.local.remove([...TRANSIENT_CAPTURE_KEYS]);
  text("capture-id", captureId);
  text("capture-time", new Date().toISOString());
  element("short-actions").hidden = true;
  element("extraction-preview").textContent = "";
  message("Reading current page");
  text("stage", "Reading current page");
  try {
  const tab = await activeTab();
  const hostname = exactHostname(tab.url!);
  text("page-title", tab.title);
  text("hostname", hostname);
  if (!(await ensureHostApproved(hostname))) return;
  message("Waiting for job content");
  text("stage", "Waiting for job content");
  const started = performance.now();
  const response = await sendToTab("capture_current_page", {
    ...pendingRequest,
    tabKey: String(tab.id)
  });
  if (state.captureId !== captureId) return;
  if (response.error) throw new Error(String(response.error));
  const result = response as unknown as CaptureResult;
  if (result.capture_id !== captureId) throw new Error("Stale capture response ignored");
  renderDiagnostics(result.diagnostics);
  message("Extracting rendered posting");
  text("stage", "Extracting rendered posting");
  const payload = result.payload;
  console.debug("Dave Jobbot fresh capture", {
    capture_id: captureId,
    tab_id: tab.id,
    url: result.diagnostics.url,
    extraction_method: payload?.extraction_method,
    text_length: payload?.full_text.length ?? 0,
    confidence: payload?.extraction_confidence,
    duration_ms: Math.round(performance.now() - started),
    stabilization_timeout: result.diagnostics.stabilization_timed_out,
    error_category: payload ? undefined : "short_content"
  });
  if (!payload) {
    element("short-actions").hidden = false;
    text("stage", "No job detected");
    message("Rendered content is still too short. Review Capture Diagnostics and retry.");
    return;
  }
  state.capture = payload;
  text("extraction-method", payload.extraction_method);
  text("text-length", payload.full_text.length);
  text("capture-confidence", payload.extraction_confidence.toFixed(2));
  text("capture-time", payload.captured_at);
  if ((result.short_content || payload.requires_human_review) && !saveLowConfidence) {
    await chrome.storage.local.set({
      [STORAGE_KEYS.extractionPreview]: payload.full_text,
      [STORAGE_KEYS.lastCapturePayload]: payload
    });
    element("short-actions").hidden = false;
    element("extraction-preview").textContent = payload.full_text.slice(0, 2_000);
    message("Low-confidence capture requires review before it can be saved.");
    return;
  }
  await saveCapture(payload);
  } catch (error) {
    await chrome.storage.local.set({
      [STORAGE_KEYS.lastCaptureError]:
        error instanceof Error ? error.message : "capture_failed"
    });
    throw error;
  } finally {
    if (captureLifecycle.finish(captureId)) setCaptureBusy(false);
  }
}

async function tailor(): Promise<void> {
  if (!state.jobId) throw new Error("Capture a job first");
  const result = await api<{ id: number; version: number; status: string; validation_status: string }>(
    `/jobs/${state.jobId}/tailor`,
    { method: "POST", body: "{}" }
  );
  state.resumeId = result.id;
  text("resume-state", `v${result.version} · ${result.status} · ${result.validation_status}`);
  text("stage", "Resume needs review");
}

async function approveResume(): Promise<void> {
  if (!state.resumeId) throw new Error("Tailor and review a resume first");
  const result = await api<{ version: number; status: string }>(
    `/resumes/${state.resumeId}/approve`,
    { method: "POST", body: JSON.stringify({ confirmed: true }) }
  );
  text("resume-state", `v${result.version} · ${result.status}`);
  text("stage", "Resume approved");
}

async function discoverAndFill(fill: boolean): Promise<void> {
  const tab = await activeTab();
  const hostname = exactHostname(tab.url!);
  if (!(await ensureHostApproved(hostname))) return;
  if (!state.jobId) {
    const stored = await chrome.storage.local.get(STORAGE_KEYS.tabs);
    const associations = (stored[STORAGE_KEYS.tabs] ?? {}) as Record<string, { jobId: number }>;
    state.jobId = associations[String(tab.id)]?.jobId;
  }
  if (!state.jobId) throw new Error("No job is associated with this tab");
  const discovery = await sendToTab("discover");
  if (discovery.status === "human_intervention_required") {
    text("stage", "Human intervention required");
    return;
  }
  const plan = await api<{
    status: string;
    fields: FillInstruction[];
    approved_resume?: { filename: string } | null;
  }>("/applications/plan", {
    method: "POST",
    body: JSON.stringify({
      job_id: state.jobId,
      tab_key: String(tab.id),
      hostname,
      fields: discovery.fields
    })
  });
  state.withheld = plan.fields
    .filter((item) => item.decision === "withhold")
    .map((item) => ({
      identifier: item.field_identifier,
      reason: item.withhold_reason ?? "withheld"
    }));
  renderWithheld();
  if (plan.status === "stopped_before_submit") {
    await sendToTab("highlight-submit");
    text("stage", "Stopped before submit");
    message("Ready for your review. Dave Jobbot will not activate the final control.");
    await audit("stopped_before_submit", { job_id: state.jobId, hostname });
    return;
  }
  if (fill) {
    const result = await sendToTab("fill", { fields: plan.fields });
    text("stage", String(result.status ?? "Fill completed"));
    await audit("supervised_page_fill", {
      job_id: state.jobId,
      hostname,
      filled_count: Array.isArray(result.filled) ? result.filled.length : 0
    });
  } else {
    text("stage", "Application page detected");
  }
}

async function audit(action: string, metadata: Record<string, unknown>): Promise<void> {
  await api("/applications/audit", {
    method: "POST",
    body: JSON.stringify({ action, metadata })
  });
}

function renderWithheld(): void {
  const list = element<HTMLUListElement>("withheld-list");
  list.replaceChildren(
    ...state.withheld.map((item) => {
      const li = document.createElement("li");
      li.textContent = `${item.identifier}: ${item.reason}`;
      return li;
    })
  );
}

function bind(id: string, handler: () => Promise<void> | void): void {
  element<HTMLButtonElement>(id).addEventListener("click", () => {
    Promise.resolve(handler()).catch((error: unknown) => {
      message(error instanceof Error ? error.message : "Unexpected extension error");
    });
  });
}

bind("pair", async () => {
  const code = element<HTMLInputElement>("pair-code").value;
  const result = await api<{ token: string }>(
    "/pair",
    { method: "POST", body: JSON.stringify({ code }) },
    false
  );
  await chrome.storage.local.set({ [STORAGE_KEYS.token]: result.token });
  element("pairing").hidden = true;
  await checkBridge();
});
bind("allow-host", async () => {
  if (!state.hostname) return;
  await api("/hosts/approve", {
    method: "POST",
    body: JSON.stringify({ hostname: state.hostname })
  });
  element("host-approval").hidden = true;
  message(`Approved exact hostname: ${state.hostname}`);
});
bind("capture", capture);
bind("refresh", capture);
bind("capture-again", capture);
bind("use-selection", capture);
bind("preview-visible", () => {
  element("extraction-preview").textContent =
    state.capture?.full_text ??
    `Visible body length: ${state.diagnostics?.visible_body_text_length ?? 0}`;
});
bind("save-low-confidence", async () => {
  if (!state.capture) throw new Error("No usable low-confidence capture is available");
  if (!confirm("Save this low-confidence capture for required human review?")) return;
  await saveCapture({
    ...state.capture,
    requires_human_review: true,
    extraction_confidence: Math.min(state.capture.extraction_confidence, 0.35)
  });
});
bind("cancel-capture", async () => {
  captureLifecycle.cancel();
  state.captureId = undefined;
  setCaptureBusy(false);
  await sendToTab("stop");
  text("stage", "Capture cancelled");
  message("Capture cancelled. Existing job and pairing state were preserved.");
});
bind("tailor", tailor);
bind("approve", approveResume);
bind("fill", () => discoverAndFill(true));
bind("continue", () => discoverAndFill(false));
bind("show-withheld", () => { element("withheld").setAttribute("open", ""); });
bind("stop", async () => {
  await sendToTab("stop");
  text("stage", "Stopped");
});
bind("dashboard", async () => { await chrome.tabs.create({ url: "http://127.0.0.1:8501" }); });
bind("open-resume", async () => { await chrome.tabs.create({ url: "http://127.0.0.1:8501" }); });
bind("audit", async () => { await chrome.tabs.create({ url: "http://127.0.0.1:8501" }); });
bind("settings", () => { element("settings-panel").hidden = !element("settings-panel").hidden; });
bind("refresh-hosts", async () => {
  const result = await api<{ hosts: Array<{ hostname: string }> }>("/hosts");
  const list = element<HTMLUListElement>("approved-hosts");
  list.replaceChildren(...result.hosts.map((host) => {
    const li = document.createElement("li");
    li.textContent = host.hostname;
    const revoke = document.createElement("button");
    revoke.textContent = "Revoke";
    revoke.addEventListener("click", () => {
      void api("/hosts/revoke", {
        method: "POST",
        body: JSON.stringify({ hostname: host.hostname })
      }).then(() => li.remove());
    });
    li.append(" ", revoke);
    return li;
  }));
});
bind("clear-state", async () => {
  if (!confirm("Clear pairing token, approved-host cache, tab associations, and previews?")) return;
  try {
    const result = await api<{ hosts: Array<{ hostname: string }> }>("/hosts");
    for (const host of result.hosts) {
      await api("/hosts/revoke", {
        method: "POST",
        body: JSON.stringify({ hostname: host.hostname })
      });
    }
    await api("/session/revoke", { method: "POST", body: "{}" });
  } catch {
    // Local state is still cleared if the bridge is unavailable.
  }
  await chrome.storage.local.clear();
  state.jobId = undefined;
  state.resumeId = undefined;
  state.capture = undefined;
  state.withheld = [];
  location.reload();
});

void checkBridge();
