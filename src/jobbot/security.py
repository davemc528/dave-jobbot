from __future__ import annotations

import re
from typing import Any

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?){2}\d{4}\b")
ADDRESS_RE = re.compile(
    r"\b\d{1,5}\s+[A-Za-z0-9.\- ]+"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Court|Ct)"
    r",?\s+[A-Za-z .\-]+(?:,\s*[A-Z]{2})?(?:\s+\d{5}(?:-\d{4})?)?\b",
    re.IGNORECASE,
)
KEY_RE = re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?([A-Za-z0-9_\-]{6,})")


def redact_text(text: str) -> str:
    redacted = EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    redacted = PHONE_RE.sub("[REDACTED_PHONE]", redacted)
    redacted = ADDRESS_RE.sub("[REDACTED_ADDRESS]", redacted)

    def _mask(match: re.Match[str]) -> str:
        return f"{match.group(1)}=[REDACTED_SECRET]"

    redacted = KEY_RE.sub(_mask, redacted)
    return redacted


def safe_log(message: str, **metadata: Any) -> str:
    cleaned: dict[str, Any] = {k: redact_text(str(v)) for k, v in metadata.items()}
    output = f"{redact_text(message)} {cleaned}"
    print(output)
    return output
