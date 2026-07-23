from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright
from pydantic import BaseModel, Field

from jobbot.config import AUTOMATION, SCREENSHOT_DIR
from jobbot.profile.application_answers import (
    QuestionContext,
    answer_map,
    match_question,
)
from jobbot.security import redact_text

AutomationMode = Literal["inspect", "supervised", "automatic_dry_run", "automatic_submit"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CaptchaDetection(BaseModel):
    detected: bool = False
    provider: str | None = None
    selectors: list[str] = Field(default_factory=list)


class AutomationResult(BaseModel):
    run_id: int | None = None
    status: str
    mode: str
    filled: list[dict[str, Any]] = Field(default_factory=list)
    withheld: list[dict[str, Any]] = Field(default_factory=list)
    review_item_ids: list[int] = Field(default_factory=list)
    screenshot_path: str | None = None
    captcha: CaptchaDetection = Field(default_factory=CaptchaDetection)
    stopped_before_submit: bool = True
    resume_command: str | None = None


class AutomationReadiness(BaseModel):
    ready: bool
    blockers: list[str] = Field(default_factory=list)


def automatic_run_readiness(
    *,
    required_matches: list[Any],
    captcha_active: bool = False,
    submit_control_detected: bool = True,
    selected_resume: str | None = None,
    duplicate_application: bool = False,
) -> AutomationReadiness:
    blockers: list[str] = []
    for match in required_matches:
        if match.category == "legally_authorized_to_work" and match.review_required:
            blockers.append("Required legal work-authorization answer is unresolved")
        if match.review_type == "required_numeric_compensation":
            blockers.append("Required numeric compensation has no approved numeric value")
        if match.proposed_answer is None or not match.autofill_permitted:
            blockers.append(f"Required field {match.category} has no eligible verified answer")
    if captcha_active:
        blockers.append("A CAPTCHA or anti-bot challenge is active")
    if not submit_control_detected:
        blockers.append("Final-submit control was not detected safely")
    if not selected_resume or not Path(selected_resume).is_file():
        blockers.append("The selected approved resume does not exist")
    if duplicate_application:
        blockers.append("This application is a duplicate")
    return AutomationReadiness(ready=not blockers, blockers=list(dict.fromkeys(blockers)))


CAPTCHA_SELECTORS: tuple[tuple[str, str], ...] = (
    (".g-recaptcha", "recaptcha"),
    ('iframe[src*="recaptcha" i]', "recaptcha"),
    (".h-captcha", "hcaptcha"),
    ('iframe[src*="hcaptcha" i]', "hcaptcha"),
    (".cf-turnstile", "cloudflare_turnstile"),
    ('iframe[src*="challenges.cloudflare.com" i]', "cloudflare_turnstile"),
    ('[id*="captcha" i]', "unknown_captcha"),
    ('[class*="captcha" i]', "unknown_captcha"),
    ('input[name*="captcha" i]', "unknown_captcha"),
)


def detect_captcha(page: Page) -> CaptchaDetection:
    found: list[str] = []
    provider: str | None = None
    for selector, name in CAPTCHA_SELECTORS:
        if page.locator(selector).count():
            found.append(selector)
            provider = provider or name
    body = page.locator("body").inner_text().casefold()
    text_indicators = {
        "verify you are human": "anti_bot_interstitial",
        "i'm not a robot": "recaptcha",
        "checking your browser": "anti_bot_interstitial",
        "challenge-platform": "anti_bot_interstitial",
    }
    for phrase, name in text_indicators.items():
        if phrase in body:
            found.append(f"text:{phrase}")
            provider = provider or name
    return CaptchaDetection(detected=bool(found), provider=provider, selectors=found)


def _review(
    connection: sqlite3.Connection,
    item_type: str,
    summary: str,
    recommended_action: str,
    metadata: dict[str, Any],
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO review_items
          (item_type, summary, status, created_at, recommended_action, metadata)
        VALUES (?, ?, 'pending', ?, ?, ?)
        """,
        (item_type, summary, utc_now(), recommended_action, json.dumps(metadata)),
    )
    if cursor.lastrowid is None:
        raise RuntimeError("SQLite did not return a review item id")
    return int(cursor.lastrowid)


def _options(page: Page, selector: str) -> list[str]:
    locator = page.locator(selector)
    if locator.evaluate("(el) => el.tagName.toLowerCase()") == "select":
        return locator.locator("option").all_inner_texts()
    return []


def _fill(page: Page, selector: str, input_type: str, answer: str) -> None:
    locator = page.locator(selector)
    tag = locator.evaluate("(el) => el.tagName.toLowerCase()")
    if tag == "select":
        locator.select_option(label=answer)
    elif input_type in {"checkbox", "radio"}:
        if answer.casefold() in {"yes", "true", "on", "1"}:
            locator.check()
    elif input_type == "file":
        if not Path(answer).is_file():
            raise FileNotFoundError(answer)
        locator.set_input_files(answer)
    else:
        locator.fill(answer)


def run_automation(
    connection: sqlite3.Connection,
    url: str,
    *,
    mode: AutomationMode,
    context: QuestionContext | None = None,
    visible: bool = True,
    job_id: int | None = None,
) -> AutomationResult:
    if mode == "automatic_submit":
        raise NotImplementedError(
            "automatic_submit is disabled and unimplemented; final submission requires a future phase"
        )
    if urlparse(url).scheme != "file" and not AUTOMATION.enabled:
        raise RuntimeError("Real-site automatic execution is disabled by configuration")
    answers = answer_map(connection)
    now = utc_now()
    cursor = connection.execute(
        """
        INSERT INTO automation_runs (job_id, mode, status, url, created_at, updated_at)
        VALUES (?, ?, 'running', ?, ?, ?)
        """,
        (job_id, mode, url, now, now),
    )
    run_id = int(cursor.lastrowid or 0)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    screenshot_path = SCREENSHOT_DIR / f"automation-run-{run_id}.png"
    result = AutomationResult(run_id=run_id, status="running", mode=mode)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not visible)
        page = browser.new_page()
        try:
            page.goto(url)
        except Exception as exc:
            result.status = "navigation_failure"
            result.review_item_ids.append(
                _review(
                    connection,
                    "navigation_failure",
                    "Application page could not be loaded",
                    "Check the URL and resume the run",
                    {"error": redact_text(str(exc)), "run_id": run_id},
                )
            )
            browser.close()
            connection.commit()
            return result

        captcha = detect_captcha(page)
        if captcha.detected:
            page.screenshot(path=screenshot_path)
            review_id = _review(
                connection,
                "human_intervention_required",
                f"CAPTCHA detected: {captcha.provider}",
                "Complete the challenge manually, then use the browser resume command",
                {
                    "url": url,
                    "timestamp": utc_now(),
                    "provider": captcha.provider,
                    "selectors": captcha.selectors,
                    "run_id": run_id,
                },
            )
            result.status = "blocked_by_captcha"
            result.captcha = captcha
            result.review_item_ids.append(review_id)
            result.screenshot_path = str(screenshot_path)
            result.resume_command = f"jobbot browser resume {run_id}"
            connection.execute(
                """
                UPDATE automation_runs SET status=?, screenshot_path=?, state_json=?,
                  updated_at=? WHERE id=?
                """,
                (
                    result.status,
                    str(screenshot_path),
                    json.dumps(result.model_dump()),
                    utc_now(),
                    run_id,
                ),
            )
            connection.commit()
            browser.close()
            return result

        fields = page.locator("input, select, textarea")
        for index in range(fields.count()):
            field = fields.nth(index)
            name = field.get_attribute("name") or field.get_attribute("id") or f"field-{index}"
            input_type = field.get_attribute("type") or field.evaluate(
                "(el) => el.tagName.toLowerCase()"
            )
            label = field.evaluate(
                "(el) => el.labels?.[0]?.innerText || "
                "el.getAttribute('aria-label') || el.placeholder || el.name || el.id || ''"
            )
            selector = f'[name="{name}"]' if field.get_attribute("name") else f"#{name}"
            match = match_question(
                str(label),
                input_type=str(input_type),
                options=_options(page, selector),
                context=context,
                answers=answers,
            )
            action = {
                "field": name,
                "category": match.category,
                "confidence": match.confidence,
                "rationale": match.rationale,
                "source_fact": match.source_fact,
            }
            eligible = match.autofill_permitted and (
                (mode == "automatic_dry_run" and match.confidence >= 0.92)
                or (mode == "supervised" and match.confidence >= 0.75)
            )
            if mode == "inspect":
                result.withheld.append({**action, "reason": "inspect_only"})
                continue
            if not eligible or match.proposed_answer is None:
                result.withheld.append({**action, "reason": match.review_type or "not_permitted"})
                if match.review_type:
                    result.review_item_ids.append(
                        _review(
                            connection,
                            match.review_type,
                            f"{label}: {match.rationale}",
                            match.recommended_action or "Review manually",
                            {"field": name, "run_id": run_id},
                        )
                    )
                continue
            original = field.input_value() if input_type not in {"checkbox", "radio"} else ""
            try:
                _fill(page, selector, str(input_type), match.proposed_answer)
                if input_type in {"checkbox", "radio"}:
                    verified_value = field.is_checked()
                elif input_type == "file":
                    verified_value = bool(field.input_value())
                else:
                    verified_value = field.input_value() == match.proposed_answer
                if not verified_value:
                    raise RuntimeError("Field value did not match the intended value after filling")
                result.filled.append(
                    {
                        **action,
                        "original": redact_text(original),
                        "value": redact_text(match.proposed_answer),
                        "verified": True,
                    }
                )
                connection.execute(
                    """
                    INSERT INTO profile_audit_log
                      (action, actor, after_json, notes, created_at)
                    VALUES ('automation_field_filled', 'system', ?, ?, ?)
                    """,
                    (
                        json.dumps(
                            {
                                "run_id": run_id,
                                "field": name,
                                "category": match.category,
                                "confidence": match.confidence,
                                "source_fact": match.source_fact,
                                "value_redacted": True,
                            }
                        ),
                        "Verified field state after local autofill",
                        utc_now(),
                    ),
                )
            except Exception as exc:
                result.review_item_ids.append(
                    _review(
                        connection,
                        "upload_failure" if input_type == "file" else "unsupported_field_type",
                        f"Could not fill {label}",
                        "Complete this field manually",
                        {"error": redact_text(str(exc)), "field": name, "run_id": run_id},
                    )
                )
        submit_count = page.locator('button[type="submit"], input[type="submit"]').count()
        page.screenshot(path=screenshot_path)
        result.screenshot_path = str(screenshot_path)
        result.stopped_before_submit = bool(submit_count)
        result.status = (
            "stopped_before_submit" if submit_count else "blocked_submit_control_not_detected"
        )
        if not submit_count:
            result.review_item_ids.append(
                _review(
                    connection,
                    "navigation_failure",
                    "Final submit control was not detected safely",
                    "Inspect the form manually; do not continue automatically",
                    {"run_id": run_id},
                )
            )
        browser.close()
    connection.execute(
        """
        UPDATE automation_runs SET status=?, screenshot_path=?, state_json=?, updated_at=?
        WHERE id=?
        """,
        (
            result.status,
            result.screenshot_path,
            json.dumps(result.model_dump()),
            utc_now(),
            run_id,
        ),
    )
    connection.commit()
    return result


def resume_run(
    connection: sqlite3.Connection, run_id: int, *, visible: bool = True
) -> AutomationResult:
    row = connection.execute("SELECT * FROM automation_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown automation run: {run_id}")
    if row["status"] != "blocked_by_captcha":
        raise ValueError("Only CAPTCHA-blocked runs can be resumed")
    return run_automation(
        connection,
        str(row["url"]),
        mode="automatic_dry_run",
        visible=visible,
        job_id=row["job_id"],
    )
