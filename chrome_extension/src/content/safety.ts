const CAPTCHA_SELECTORS = [
  ".g-recaptcha",
  ".h-captcha",
  ".cf-turnstile",
  "iframe[src*='recaptcha' i]",
  "iframe[src*='hcaptcha' i]",
  "iframe[src*='challenges.cloudflare.com' i]",
  "[aria-label*='captcha' i]"
];

const TERMINAL = /^(submit|submit application|complete application|finish application|send application|finalize|confirm and submit)$/i;

export function captchaDetected(document: Document): boolean {
  if (CAPTCHA_SELECTORS.some((selector) => document.querySelector(selector))) return true;
  return /verify you are human|i'?m not a robot|checking your browser/i.test(
    document.body?.innerText ?? ""
  );
}

export function authenticationDetected(document: Document): boolean {
  return Boolean(
    document.querySelector("input[type='password']") ||
    /create account|verify your email|multi-factor|one-time code|security question/i.test(
      document.body?.innerText ?? ""
    )
  );
}

export function isTerminalLabel(label: string): boolean {
  return TERMINAL.test(label.trim());
}

export function highlightTerminalControls(document: Document): number {
  let count = 0;
  document.querySelectorAll<HTMLElement>("button,input[type='submit'],[role='button']").forEach(
    (element) => {
      const label =
        element.innerText ||
        element.getAttribute("value") ||
        element.getAttribute("aria-label") ||
        "";
      if (isTerminalLabel(label)) {
        element.style.outline = "4px solid #c62828";
        element.style.outlineOffset = "3px";
        count += 1;
      }
    }
  );
  return count;
}
