import { greenhouseAdapter } from "./adapters/greenhouse";
import {
  AdapterResult,
  genericAdapter,
  likelyContentContainers,
  visibleText
} from "./adapters/generic";
import { leverAdapter } from "./adapters/lever";
import { lillyAdapter } from "./adapters/lilly";
import { oracleAdapter } from "./adapters/oracle";
import { workdayAdapter } from "./adapters/workday";
import { waitForRenderedContent } from "./stabilization";
import type { StabilizationResult } from "./stabilization";
import {
  CapturePayload,
  CaptureResult,
  ExtractionDiagnostics
} from "../shared/types";
import { exactHostname, normalizeUrl } from "../shared/urls";

const PREFERRED_TEXT_LENGTH = 300;
const MINIMUM_SAVE_LENGTH = 80;

function jobPostingJsonLd(document: Document): Record<string, unknown> | undefined {
  for (const script of document.querySelectorAll("script[type='application/ld+json']")) {
    try {
      const parsed = JSON.parse(script.textContent ?? "") as unknown;
      const values = Array.isArray(parsed) ? parsed : [parsed];
      for (const value of values) {
        if (
          typeof value === "object" &&
          value !== null &&
          (value as Record<string, unknown>)["@type"] === "JobPosting"
        ) return value as Record<string, unknown>;
      }
    } catch {
      // Invalid third-party JSON-LD is ignored.
    }
  }
  return undefined;
}

function jsonLdResult(data: Record<string, unknown>, document: Document): AdapterResult {
  const description =
    typeof data.description === "string"
      ? (new document.defaultView!.DOMParser()
          .parseFromString(data.description, "text/html").body.textContent ?? "")
      : "";
  const hiring = data.hiringOrganization as Record<string, unknown> | undefined;
  const location = data.jobLocation as Record<string, unknown> | undefined;
  const address = location?.address as Record<string, unknown> | undefined;
  const identifier = data.identifier;
  return {
    text: description,
    title: typeof data.title === "string" ? data.title : undefined,
    employer: typeof hiring?.name === "string" ? hiring.name : undefined,
    location: [address?.addressLocality, address?.addressRegion]
      .filter((value): value is string => typeof value === "string")
      .join(", "),
    requisitionId:
      typeof identifier === "string"
        ? identifier
        : typeof identifier === "object" &&
            identifier !== null &&
            typeof (identifier as Record<string, unknown>).value === "string"
          ? String((identifier as Record<string, unknown>).value)
          : undefined,
    method: "json-ld-job-posting",
    confidence: description.length > 500 ? 0.98 : 0.5
  };
}

function atsType(hostname: string): string {
  if (hostname.includes("oraclecloud.com")) return "oracle";
  if (hostname.includes("myworkdayjobs.com")) return "workday";
  if (hostname.includes("greenhouse.io")) return "greenhouse";
  if (hostname.includes("lever.co")) return "lever";
  if (hostname.includes("lilly.com")) return "lilly";
  return "generic";
}

function specificAdapter(hostname: string, document: Document): AdapterResult {
  if (hostname.includes("oraclecloud.com")) return oracleAdapter(document);
  if (hostname.includes("myworkdayjobs.com")) return workdayAdapter(document);
  if (hostname.includes("greenhouse.io")) return greenhouseAdapter(document);
  if (hostname.includes("lever.co")) return leverAdapter(document);
  if (hostname.includes("lilly.com")) return lillyAdapter(document);
  return genericAdapter(document);
}

function section(text: string, heading: RegExp): string[] {
  const lines = text.split("\n").map((line) => line.trim()).filter(Boolean);
  const start = lines.findIndex((line) => heading.test(line));
  if (start < 0) return [];
  const result: string[] = [];
  for (const line of lines.slice(start + 1)) {
    if (result.length && line.endsWith(":") && line.length < 80) break;
    result.push(line.replace(/^[•*-]\s*/, ""));
  }
  return result.slice(0, 40);
}

function extractionAttempts(
  document: Document,
  hostname: string
): {
  best: AdapterResult;
  jsonLd?: Record<string, unknown>;
  attempts: ExtractionDiagnostics["attempts"];
} {
  const attempts: ExtractionDiagnostics["attempts"] = [];
  const selection = document.getSelection()?.toString().trim() ?? "";
  const jsonLd = jobPostingJsonLd(document);
  const candidates: AdapterResult[] = [];
  const record = (result: AdapterResult): void => {
    const text = result.text.trim();
    attempts.push({
      method: result.method,
      text_length: text.length,
      rejection_reason:
        text.length >= PREFERRED_TEXT_LENGTH ? undefined : "below_preferred_threshold"
    });
    candidates.push({ ...result, text });
  };
  if (selection) {
    record({
      text: selection,
      method: "user-selection",
      confidence: selection.length >= PREFERRED_TEXT_LENGTH ? 1 : 0.35
    });
  }
  if (jsonLd) record(jsonLdResult(jsonLd, document));
  record(specificAdapter(hostname, document));
  if (atsType(hostname) !== "generic") record(genericAdapter(document));
  const semantic = [...document.querySelectorAll("main,article,[role='main']")]
    .map((candidate) => visibleText(candidate))
    .sort((a, b) => b.length - a.length)[0] ?? "";
  record({
    text: semantic,
    method: "semantic-visible-content",
    confidence: semantic.length > 800 ? 0.7 : 0.3
  });
  const body = visibleText(document.body);
  record({
    text: body,
    method: "full-visible-body-review",
    confidence: 0.2
  });
  const preferred = candidates.find(
    (candidate) =>
      candidate.method === "user-selection" &&
      candidate.text.length >= PREFERRED_TEXT_LENGTH
  );
  const ranked = [...candidates].sort((a, b) => {
      const sufficientA = Number(a.text.length >= PREFERRED_TEXT_LENGTH);
      const sufficientB = Number(b.text.length >= PREFERRED_TEXT_LENGTH);
      return sufficientB - sufficientA || b.confidence - a.confidence || b.text.length - a.text.length;
    });
  const best =
    preferred ??
    ranked.find((candidate) => candidate.text.length >= PREFERRED_TEXT_LENGTH) ??
    candidates.find((candidate) => candidate.method === "full-visible-body-review") ??
    ranked[0]!;
  return { best, jsonLd, attempts };
}

function buildCapture(
  document: Document,
  location: Location,
  tabKey: string,
  captureId: string,
  stabilization?: StabilizationResult
): CaptureResult {
  const hostname = exactHostname(location.href);
  const { best, jsonLd, attempts } = extractionAttempts(document, hostname);
  const canonical = (document.querySelector("link[rel='canonical']") as HTMLLinkElement | null)?.href;
  const apply = [...document.querySelectorAll<HTMLAnchorElement>("a[href]")].find((anchor) =>
    /^(apply|apply now|start application)$/i.test(
      (anchor.innerText || anchor.textContent || "").trim()
    )
  );
  const text = best.text.replace(/\n{3,}/g, "\n\n").trim();
  const bodyLength = visibleText(document.body).length;
  const containerLength = likelyContentContainers(document)
    .map((container) => visibleText(container).length)
    .sort((a, b) => b - a)[0] ?? 0;
  const diagnostics: ExtractionDiagnostics = {
    capture_id: captureId,
    visible_body_text_length: bodyLength,
    best_candidate_text_length: Math.max(containerLength, text.length),
    page_title: document.title,
    url: normalizeUrl(location.href),
    hostname,
    ats_type: atsType(hostname),
    document_ready_state: document.readyState,
    json_ld_block_count: document.querySelectorAll("script[type='application/ld+json']").length,
    likely_container_count: likelyContentContainers(document).length,
    page_still_mutating: Boolean(
      stabilization?.timedOut && stabilization.meaningfulMutationCount
    ),
    selection_present: Boolean(document.getSelection()?.toString().trim()),
    stabilization_timed_out: stabilization?.timedOut ?? false,
    stabilization_duration_ms: stabilization?.durationMs,
    initial_visible_text_length: stabilization?.initialTextLength,
    final_visible_text_length: stabilization?.finalTextLength,
    mutation_count: stabilization?.mutationCount,
    meaningful_mutation_count: stabilization?.meaningfulMutationCount,
    last_mutation_at: stabilization?.lastMutationAt,
    attempts
  };
  if (text.length < MINIMUM_SAVE_LENGTH) {
    return { capture_id: captureId, diagnostics, short_content: true };
  }
  const lowConfidence = text.length < PREFERRED_TEXT_LENGTH || best.method === "full-visible-body-review";
  const payload: CapturePayload = {
    capture_id: captureId,
    tab_key: tabKey,
    current_url: location.href,
    canonical_url: normalizeUrl(canonical || location.href),
    application_url: apply ? normalizeUrl(apply.href) : undefined,
    hostname,
    employer: best.employer,
    job_title: best.title ?? document.querySelector("h1")?.textContent?.trim(),
    location: best.location,
    requisition_id: best.requisitionId,
    responsibilities: section(text, /responsibilit|what you will do/i),
    required_qualifications: section(
      text,
      /required|min(?:imum)? qualifications?|requirements/i
    ),
    preferred_qualifications: section(text, /preferred qualifications?/i),
    full_text: text,
    ats_type: atsType(hostname),
    extraction_method: best.method,
    extraction_confidence: lowConfidence ? Math.min(best.confidence, 0.35) : best.confidence,
    page_title: document.title,
    json_ld: jsonLd,
    captured_at: new Date().toISOString(),
    requires_human_review: lowConfidence || Boolean(stabilization?.timedOut),
    stabilization_timed_out: stabilization?.timedOut ?? false
  };
  return { capture_id: captureId, payload, diagnostics, short_content: lowConfidence };
}

export function extractPosting(
  document: Document,
  location: Location,
  tabKey: string,
  captureId = crypto.randomUUID()
): CapturePayload {
  const result = buildCapture(document, location, tabKey, captureId);
  if (!result.payload) throw new Error("No job detected: visible posting content is too short");
  return result.payload;
}

export async function captureFreshPosting(
  document: Document,
  location: Location,
  request: { tabKey: string; capture_id: string; max_wait_ms?: number }
): Promise<CaptureResult> {
  const stabilization = await waitForRenderedContent(document, {
    maxWaitMs: request.max_wait_ms
  });
  return buildCapture(
    document,
    location,
    request.tabKey,
    request.capture_id,
    stabilization
  );
}
