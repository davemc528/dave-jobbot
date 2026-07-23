import { greenhouseAdapter } from "./adapters/greenhouse";
import { genericAdapter, AdapterResult } from "./adapters/generic";
import { leverAdapter } from "./adapters/lever";
import { lillyAdapter } from "./adapters/lilly";
import { oracleAdapter } from "./adapters/oracle";
import { workdayAdapter } from "./adapters/workday";
import { CapturePayload } from "../shared/types";
import { exactHostname, normalizeUrl } from "../shared/urls";

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
  const description = typeof data.description === "string"
    ? new document.defaultView!.DOMParser()
      .parseFromString(data.description, "text/html").body.textContent ?? ""
    : "";
  const hiring = data.hiringOrganization as Record<string, unknown> | undefined;
  const location = data.jobLocation as Record<string, unknown> | undefined;
  const address = location?.address as Record<string, unknown> | undefined;
  return {
    text: description,
    title: typeof data.title === "string" ? data.title : undefined,
    employer: typeof hiring?.name === "string" ? hiring.name : undefined,
    location: [address?.addressLocality, address?.addressRegion]
      .filter((value): value is string => typeof value === "string")
      .join(", "),
    requisitionId: typeof data.identifier === "string" ? data.identifier : undefined,
    method: "json-ld-job-posting",
    confidence: description.length > 500 ? 0.98 : 0.5
  };
}

function atsAdapter(hostname: string, document: Document): AdapterResult {
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

export function extractPosting(document: Document, location: Location, tabKey: string): CapturePayload {
  const hostname = exactHostname(location.href);
  const selection = document.getSelection()?.toString().trim() ?? "";
  const jsonLd = jobPostingJsonLd(document);
  let result: AdapterResult;
  if (selection.length >= 300) {
    result = { text: selection, method: "user-selection", confidence: 1.0 };
  } else if (jsonLd) {
    result = jsonLdResult(jsonLd, document);
  } else {
    result = atsAdapter(hostname, document);
  }
  const canonical = (document.querySelector("link[rel='canonical']") as HTMLLinkElement | null)?.href;
  const apply = [...document.querySelectorAll<HTMLAnchorElement>("a[href]")]
    .find((anchor) => /^(apply|apply now|start application)$/i.test(anchor.innerText.trim()));
  const text = result.text.replace(/\n{3,}/g, "\n\n").trim();
  if (text.length < 120) throw new Error("No job detected: visible posting content is too short");
  return {
    tab_key: tabKey,
    current_url: location.href,
    canonical_url: normalizeUrl(canonical || location.href),
    application_url: apply ? normalizeUrl(apply.href) : undefined,
    hostname,
    employer: result.employer,
    job_title: result.title ?? document.querySelector("h1")?.textContent?.trim(),
    location: result.location,
    requisition_id: result.requisitionId,
    responsibilities: section(text, /responsibilit|what you will do/i),
    required_qualifications: section(text, /required|min(?:imum)? qualifications?|requirements/i),
    preferred_qualifications: section(text, /preferred qualifications?/i),
    full_text: text,
    ats_type: result.method.split("-")[0] ?? "generic",
    extraction_method: result.method,
    extraction_confidence: result.confidence,
    page_title: document.title,
    json_ld: jsonLd,
    captured_at: new Date().toISOString()
  };
}
