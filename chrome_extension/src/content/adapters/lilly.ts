import { AdapterResult, visibleText } from "./generic";

export function lillyAdapter(document: Document): AdapterResult {
  const root = document.querySelector("main, [class*='job-description' i]");
  const text = visibleText(root);
  return {
    text,
    title: document.querySelector("h1")?.textContent?.trim(),
    location: document.querySelector("[class*='location' i]")?.textContent?.trim(),
    method: "lilly-rendered",
    confidence: text.length > 700 ? 0.85 : 0.3
  };
}
