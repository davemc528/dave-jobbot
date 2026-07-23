from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from jobbot.config import SCREENSHOT_DIR


@dataclass(slots=True)
class BrowserRun:
    url: str
    mode: str
    status: str
    screenshot_path: str | None = None
    audit_log: list[dict[str, str]] | None = None


def inspect_form(url: str, visible: bool = True) -> dict[str, Any]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not visible)
        page = browser.new_page()
        page.goto(url)
        details = page.evaluate(
            """
            () => {
                const fields = [];
                for (const el of document.querySelectorAll('input, select, textarea')) {
                    const type = el.type || el.tagName.toLowerCase();
                    const name = el.name || el.id || el.placeholder || ('field-' + fields.length);
                    const label = el.labels?.[0]?.innerText || el.getAttribute('aria-label') || '';
                    fields.push({ tag: el.tagName.toLowerCase(), type, name, label });
                }
                const text = document.body.innerText.toLowerCase();
                const captcha = Boolean(
                    document.querySelector('[class*="captcha" i], [id*="captcha" i], iframe[src*="captcha" i]')
                ) || text.includes('captcha') || text.includes("i'm not a robot");
                const submitButtons = [...document.querySelectorAll(
                    'button[type="submit"], input[type="submit"]'
                )].map(el => el.innerText || el.value || 'Submit');
                return {fields, captcha, submitButtons};
            }
            """
        )
        browser.close()
    status = "manual_intervention" if details["captcha"] else "inspected"
    return {
        "url": url,
        "fields": details["fields"],
        "captcha_detected": details["captcha"],
        "submit_controls": details["submitButtons"],
        "status": status,
        "stopped_before_submit": bool(details["submitButtons"]),
    }


def dry_run_autofill(
    url: str,
    values: dict[str, str] | None = None,
    *,
    verified_fields: set[str] | None = None,
    visible: bool = True,
) -> dict[str, Any]:
    result = inspect_form(url, visible=visible)
    fields: list[dict[str, str]] = result["fields"]
    values = values or {}
    verified_fields = verified_fields or set()
    summary: list[dict[str, str]] = []
    if result["captcha_detected"]:
        return {
            **result,
            "mode": "dry-run",
            "actions": [],
            "status": "manual_intervention",
            "reason": "CAPTCHA-like element detected",
        }
    for field in fields:
        name = field["name"]
        recognized = name in values and name in verified_fields
        summary.append(
            {
                "field": name,
                "type": field["type"],
                "status": "would_fill" if recognized else "needs_review",
            }
        )
    return {
        **result,
        "mode": "dry-run",
        "actions": summary,
        "status": "stopped_before_submit",
        "stopped_before_submit": True,
    }


def save_screenshot(
    url: str, output_name: str = "browser-screenshot.png", *, visible: bool = True
) -> str:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = SCREENSHOT_DIR / output_name
    timestamp = datetime.now(timezone.utc).isoformat()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not visible)
        page = browser.new_page()
        page.goto(url)
        page.screenshot(path=output_path)
        browser.close()
    return json.dumps({"path": str(output_path), "timestamp": timestamp})


def verified_mock_autofill(
    url: str,
    facts: dict[str, dict[str, Any]],
    *,
    visible: bool = True,
) -> dict[str, Any]:
    """Fill a local fixture only, using explicitly verified and permitted values."""
    if urlparse(url).scheme != "file":
        raise ValueError("Phase I.5 verified autofill validation is restricted to local fixtures")
    review_items: list[str] = []
    withheld: list[str] = []
    filled: dict[str, str] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not visible)
        page = browser.new_page()
        page.goto(url)
        captcha = page.locator('[class*="captcha" i], [id*="captcha" i]').count() > 0
        if captcha:
            browser.close()
            return {
                "status": "manual_intervention",
                "captcha_detected": True,
                "filled": {},
                "withheld": [],
                "review_items": ["CAPTCHA-like element detected"],
                "stopped_before_submit": True,
            }
        for name, fact in facts.items():
            locator = page.locator(f'[name="{name}"]')
            if locator.count() == 0:
                continue
            allowed = (
                fact.get("verification_status") == "verified"
                and bool(fact.get("autofill_permission"))
                and fact.get("sensitivity") != "restricted"
            )
            if not allowed:
                withheld.append(name)
                continue
            value = str(fact.get("value", ""))
            input_type = locator.first.get_attribute("type") or "text"
            if input_type == "file":
                if value and Path(value).is_file():
                    locator.first.set_input_files(value)
                    filled[name] = Path(value).name
                else:
                    review_items.append(f"Approved file is missing for {name}")
            elif input_type in {"checkbox", "radio"}:
                if value.casefold() in {"yes", "true", "1", "on"}:
                    locator.first.check()
                    filled[name] = value
            elif locator.first.evaluate("(el) => el.tagName.toLowerCase()") == "select":
                locator.first.select_option(label=value)
                filled[name] = value
            else:
                locator.first.fill(value)
                filled[name] = value
        if page.locator('[name="linkedin_url"]').count() and "linkedin_url" not in filled:
            review_items.append("LinkedIn URL requires review")
        submit_count = page.locator('button[type="submit"], input[type="submit"]').count()
        browser.close()
    return {
        "status": "stopped_before_submit",
        "captcha_detected": False,
        "filled": filled,
        "withheld": withheld,
        "review_items": review_items,
        "stopped_before_submit": bool(submit_count),
    }
