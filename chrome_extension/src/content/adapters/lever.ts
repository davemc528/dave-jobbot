import { AdapterResult, visibleText } from "./generic";

export function leverAdapter(document: Document): AdapterResult {
  const root = document.querySelector(".posting-page, .posting, main");
  const text = visibleText(root);
  return {
    text,
    title: document.querySelector(".posting-headline h2,h1")?.textContent?.trim(),
    location: document.querySelector(".location")?.textContent?.trim(),
    method: "lever-rendered",
    confidence: text.length > 500 ? 0.9 : 0.35
  };
}
