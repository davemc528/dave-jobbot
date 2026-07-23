import { AdapterResult, visibleText } from "./generic";

export function workdayAdapter(document: Document): AdapterResult {
  const root =
    document.querySelector("[data-automation-id='jobPostingDescription']") ??
    document.querySelector("[data-automation-id='jobPostingPage']") ??
    document.querySelector("main");
  const text = visibleText(root);
  const requisition = text.match(/\b(?:requisition id|job id)\s*:?\s*([A-Z]?\d[\w-]+)/i);
  return {
    text,
    title:
      document.querySelector("[data-automation-id='jobPostingHeader'] h2")?.textContent?.trim() ??
      document.querySelector("h1,h2")?.textContent?.trim(),
    location: document
      .querySelector("[data-automation-id='locations']")
      ?.textContent?.trim(),
    requisitionId: requisition?.[1],
    method: "workday-rendered",
    confidence: text.length > 800 ? 0.92 : 0.35
  };
}
