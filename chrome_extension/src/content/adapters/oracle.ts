import { AdapterResult, visibleText } from "./generic";

export function oracleAdapter(document: Document): AdapterResult {
  const root =
    document.querySelector("[data-bind*='job']") ??
    document.querySelector(".job-details") ??
    document.querySelector("main");
  const text = visibleText(root);
  const requisition = text.match(/\b(?:requisition|job)\s*(?:id|number)?\s*:?\s*(\d{3,})/i);
  return {
    text,
    title: document.querySelector("h1")?.textContent?.trim(),
    location: document.querySelector("[class*='location' i]")?.textContent?.trim(),
    requisitionId: requisition?.[1],
    method: "oracle-rendered",
    confidence: text.length > 800 ? 0.9 : 0.35
  };
}
