const TRACKING = new Set([
  "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
  "source", "src", "trackingid", "ref", "referral", "gclid", "fbclid"
]);

export function normalizeUrl(raw: string): string {
  const url = new URL(raw);
  for (const key of [...url.searchParams.keys()]) {
    if (TRACKING.has(key.toLowerCase())) url.searchParams.delete(key);
  }
  url.hash = "";
  return url.toString();
}

export function exactHostname(raw: string): string {
  return new URL(raw).hostname.toLowerCase().replace(/\.$/, "");
}
