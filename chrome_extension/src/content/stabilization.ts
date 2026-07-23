import { visibleText } from "./adapters/generic";

export interface StabilizationResult {
  stable: boolean;
  timedOut: boolean;
  durationMs: number;
  initialTextLength: number;
  finalTextLength: number;
  mutationCount: number;
  meaningfulMutationCount: number;
  lastMutationAt: string | null;
}

const LIKELY_CONTENT =
  "main,article,[role='main'],[data-bind*='job' i],[class*='job-detail' i]," +
  "[class*='job-description' i],[id*='job-detail' i],[id*='job-description' i]";

function observedRoots(document: Document): Element[] {
  const roots = [...document.querySelectorAll(LIKELY_CONTENT)];
  return roots.length ? roots : document.body ? [document.body] : [];
}

function usefulContent(document: Document): string {
  const roots = observedRoots(document);
  const rootText = roots.map((root) => visibleText(root)).join("\n");
  const headings = [...document.querySelectorAll("h1,[class*='location' i]")]
    .map((node) => node.textContent?.trim() ?? "")
    .join("\n");
  const jsonLd = [...document.querySelectorAll("script[type='application/ld+json']")]
    .map((node) => node.textContent ?? "")
    .join("\n");
  return `${headings}\n${rootText}\n${jsonLd}`.replace(/\s+/g, " ").trim();
}

export function waitForRenderedContent(
  document: Document,
  options: { stableMs?: number; maxWaitMs?: number; sampleMs?: number } = {}
): Promise<StabilizationResult> {
  const stableMs = options.stableMs ?? 850;
  const maxWaitMs = options.maxWaitMs ?? 8_000;
  const sampleMs = options.sampleMs ?? 150;
  const started = Date.now();
  const initialContent = usefulContent(document);
  const initialTextLength = initialContent.length;
  let lastContent = initialContent;
  let mutationCount = 0;
  let meaningfulMutationCount = 0;
  let lastMeaningfulChange = started;
  let lastMutationAt: string | null = null;
  let observer: MutationObserver | undefined;
  let sampleTimer: ReturnType<typeof setInterval> | undefined;
  let deadlineTimer: ReturnType<typeof setTimeout> | undefined;
  let quietTimer: ReturnType<typeof setTimeout> | undefined;

  return new Promise<StabilizationResult>((resolve) => {
    let settled = false;
    const cleanup = (): void => {
      observer?.disconnect();
      if (sampleTimer !== undefined) clearInterval(sampleTimer);
      if (deadlineTimer !== undefined) clearTimeout(deadlineTimer);
      if (quietTimer !== undefined) clearTimeout(quietTimer);
    };
    const settle = (timedOut: boolean): void => {
      if (settled) return;
      settled = true;
      const finalContent = usefulContent(document);
      cleanup();
      resolve({
        stable: !timedOut,
        timedOut,
        durationMs: Date.now() - started,
        initialTextLength,
        finalTextLength: finalContent.length,
        mutationCount,
        meaningfulMutationCount,
        lastMutationAt
      });
    };
    const scheduleQuietDeadline = (): void => {
      if (quietTimer !== undefined) clearTimeout(quietTimer);
      quietTimer = setTimeout(() => {
        const ready = ["interactive", "complete"].includes(document.readyState);
        if (ready && Date.now() - lastMeaningfulChange >= stableMs) settle(false);
      }, stableMs);
    };
    const sample = (): void => {
      const content = usefulContent(document);
      if (content !== lastContent) {
        lastContent = content;
        meaningfulMutationCount += 1;
        lastMeaningfulChange = Date.now();
        lastMutationAt = new Date(lastMeaningfulChange).toISOString();
        scheduleQuietDeadline();
      }
    };

    // Install the absolute deadline before observers or recurring sampling.
    deadlineTimer = setTimeout(() => settle(true), maxWaitMs);
    const Observer = document.defaultView?.MutationObserver;
    if (Observer) {
      observer = new Observer(() => {
        mutationCount += 1;
      });
      for (const root of observedRoots(document)) {
        observer.observe(root, { childList: true, subtree: true, characterData: true });
      }
    }
    sampleTimer = setInterval(sample, sampleMs);
    scheduleQuietDeadline();
  });
}
