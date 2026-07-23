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

export function likelyContentContainers(document: Document): Element[] {
  const selector =
    "main,article,[role='main'],[data-bind*='job' i],[class*='job-detail' i]," +
    "[class*='job-description' i],[id*='job-detail' i],[id*='job-description' i]";
  return [...document.querySelectorAll(selector)];
}

export function openShadowText(document: Document): string {
  const values: string[] = [];
  const visit = (root: Document | ShadowRoot): void => {
    for (const element of root.querySelectorAll<HTMLElement>("*")) {
      if (element.shadowRoot) {
        values.push(visibleText(element.shadowRoot.host));
        visit(element.shadowRoot);
      }
    }
  };
  visit(document);
  return values.join("\n").trim();
}

export function genericAdapter(document: Document): AdapterResult {
  const candidates = [
    ...likelyContentContainers(document),
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
