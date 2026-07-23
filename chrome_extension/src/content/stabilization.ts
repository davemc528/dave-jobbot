import { visibleText } from "./adapters/generic";

export interface StabilizationResult {
  timedOut: boolean;
  stillMutating: boolean;
  durationMs: number;
  visibleTextLength: number;
}

const LIKELY_CONTENT =
  "main,article,[role='main'],[data-bind*='job' i],[class*='job-detail' i]," +
  "[class*='job-description' i],[id*='job-detail' i],[id*='job-description' i]";

function observedRoots(document: Document): Element[] {
  const roots = [...document.querySelectorAll(LIKELY_CONTENT)];
  return roots.length ? roots : document.body ? [document.body] : [];
}

function currentLength(document: Document): number {
  return visibleText(document.body).length;
}

export async function waitForRenderedContent(
  document: Document,
  options: { stableMs?: number; maxWaitMs?: number; sampleMs?: number } = {}
): Promise<StabilizationResult> {
  const stableMs = options.stableMs ?? 850;
  const maxWaitMs = options.maxWaitMs ?? 9_000;
  const sampleMs = options.sampleMs ?? 150;
  const started = Date.now();
  let lastChange = started;
  let lastLength = currentLength(document);
  let mutationCount = 0;
  const MutationObserverClass = document.defaultView?.MutationObserver;
  const observer = MutationObserverClass
    ? new MutationObserverClass(() => {
        mutationCount += 1;
        lastChange = Date.now();
      })
    : undefined;
  for (const root of observedRoots(document)) {
    observer?.observe(root, { childList: true, subtree: true, characterData: true });
  }
  try {
    while (Date.now() - started < maxWaitMs) {
      await new Promise((resolve) => setTimeout(resolve, sampleMs));
      const length = currentLength(document);
      if (length !== lastLength) {
        lastLength = length;
        lastChange = Date.now();
      }
      const ready = ["interactive", "complete"].includes(document.readyState);
      if (ready && Date.now() - lastChange >= stableMs) {
        return {
          timedOut: false,
          stillMutating: false,
          durationMs: Date.now() - started,
          visibleTextLength: length
        };
      }
    }
    return {
      timedOut: true,
      stillMutating: mutationCount > 0 && Date.now() - lastChange < stableMs,
      durationMs: Date.now() - started,
      visibleTextLength: currentLength(document)
    };
  } finally {
    observer?.disconnect();
  }
}
