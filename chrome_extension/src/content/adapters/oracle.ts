import {
  AdapterResult,
  likelyContentContainers,
  openShadowText,
  visibleText
} from "./generic";

export function oracleAdapter(document: Document): AdapterResult {
  const candidates = likelyContentContainers(document);
  const headingSections = [...document.querySelectorAll("h1,h2,h3,h4")]
    .filter((heading) =>
      /responsibilit|qualification|requirement|job information|job description|about the role/i.test(
        heading.textContent ?? ""
      )
    )
    .map((heading) => heading.closest("section,article,div"))
    .filter((element): element is Element => Boolean(element));
  const frameText: string[] = [];
  for (const frame of document.querySelectorAll<HTMLIFrameElement>("iframe")) {
    try {
      if (frame.contentDocument) frameText.push(visibleText(frame.contentDocument.body));
    } catch {
      // Cross-origin frames are intentionally inaccessible.
    }
  }
  const scored = [...new Set([...candidates, ...headingSections])]
    .map((element) => visibleText(element))
    .sort((a, b) => b.length - a.length);
  const text = [scored[0] ?? "", openShadowText(document), ...frameText]
    .filter(Boolean)
    .join("\n")
    .trim();
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
