import { AdapterResult, visibleText } from "./generic";

export function greenhouseAdapter(document: Document): AdapterResult {
  const root = document.querySelector("#content, .job-post, main");
  const text = visibleText(root);
  return {
    text,
    title: document.querySelector("h1")?.textContent?.trim(),
    location: document.querySelector(".location")?.textContent?.trim(),
    method: "greenhouse-rendered",
    confidence: text.length > 500 ? 0.9 : 0.35
  };
}
