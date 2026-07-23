export interface AdapterResult {
  text: string;
  title?: string;
  employer?: string;
  location?: string;
  requisitionId?: string;
  method: string;
  confidence: number;
}

export function visibleText(element: Element | null): string {
  if (!element) return "";
  const clone = element.cloneNode(true) as HTMLElement;
  clone.querySelectorAll(
    "nav,footer,header,aside,script,style,noscript,[aria-hidden='true']," +
      "[class*='cookie' i],[class*='social' i],[class*='recommended' i]"
  ).forEach((item) => item.remove());
  return (clone.innerText || clone.textContent || "").replace(/\n{3,}/g, "\n\n").trim();
}

export function genericAdapter(document: Document): AdapterResult {
  const candidates = [
    document.querySelector("main"),
    document.querySelector("article"),
    document.querySelector("[role='main']"),
    document.body
  ];
  const scored = candidates
    .map((element) => ({ element, text: visibleText(element) }))
    .sort((a, b) => b.text.length - a.text.length);
  const best = scored[0]?.text ?? "";
  return {
    text: best,
    title: document.querySelector("h1")?.textContent?.trim(),
    method: "generic-visible-content",
    confidence: best.length > 800 ? 0.65 : 0.25
  };
}
