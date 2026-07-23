from __future__ import annotations

import csv
import html
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright
from pydantic import BaseModel, Field

from jobbot.documents.extract import extract_text
from jobbot.jobs.fetch import JobURLFetchError, fetch_job_url
from jobbot.jobs.scoring import normalize_job
from jobbot.security import redact_url_tracking_parameters


class PostingIntake(BaseModel):
    raw_text: str
    normalized_text: str
    normalized_url: str | None = None
    retrieval_method: str
    complete: bool
    warnings: list[str] = Field(default_factory=list)
    fields: dict[str, object] = Field(default_factory=dict)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def strip_html(value: str) -> str:
    without_scripts = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", value, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", "\n", without_scripts)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(text)).strip()


def posting_is_complete(text: str) -> tuple[bool, list[str]]:
    normalized = text.casefold()
    warnings: list[str] = []
    if len(text.strip()) < 500:
        warnings.append("Posting text is unusually short")
    if '<div id="root"></div>' in normalized or "window.workday" in normalized:
        warnings.append("Application shell detected instead of a rendered posting")
    if any(marker in normalized for marker in ("sign in to view", "login required")):
        warnings.append("Posting is login-gated")
    qualification_signal = any(
        marker in normalized
        for marker in ("qualification", "requirement", "experience", "education")
    )
    responsibility_signal = any(
        marker in normalized for marker in ("responsibilit", "what you will", "job duties")
    )
    if not qualification_signal:
        warnings.append("No qualification section detected")
    if not responsibility_signal:
        warnings.append("No responsibility section detected")
    return not warnings, warnings


def _lines_after_heading(text: str, headings: tuple[str, ...]) -> list[str]:
    lines = [line.strip(" \t•*-") for line in text.splitlines() if line.strip()]
    output: list[str] = []
    collecting = False
    for line in lines:
        lowered = line.casefold().rstrip(":")
        if any(heading in lowered for heading in headings):
            collecting = True
            continue
        if collecting and len(line) < 80 and line.endswith(":"):
            break
        if collecting:
            output.append(line)
    return output[:30]


def normalize_posting(raw: str, *, source_url: str | None, method: str) -> PostingIntake:
    text = strip_html(raw) if "<html" in raw.casefold() else raw.strip()
    text = re.sub(r"[ \t]+", " ", text)
    complete, warnings = posting_is_complete(text)
    raw_lowered = raw.casefold()
    if (
        '<div id="root"></div>' in raw_lowered or "window.workday" in raw_lowered
    ) and "Application shell detected instead of a rendered posting" not in warnings:
        warnings.append("Application shell detected instead of a rendered posting")
        complete = False
    post = normalize_job(text, source=method)
    lowered = text.casefold()
    travel_match = re.search(r"\b(?:up to\s+)?(\d{1,3})\s*%\s*travel", text, re.I)
    loaded_title = re.search(r"\n([^\n]{20,200}) page is loaded\n", text)
    employer_match = re.search(r"\bAt ([A-Z][A-Za-z0-9 &.-]{2,50}),", text)
    fields: dict[str, object] = {
        "employer": employer_match.group(1) if employer_match else post.company,
        "title": loaded_title.group(1).strip() if loaded_title else post.title,
        "location": post.location,
        "remote_status": (
            "remote"
            if "remote" in lowered
            else "hybrid"
            if "hybrid" in lowered
            else "on-site"
            if "on-site" in lowered or "onsite" in lowered
            else "unknown"
        ),
        "compensation": post.salary,
        "employment_type": next(
            (
                value
                for value in ("full-time", "part-time", "contract", "temporary")
                if value in lowered
            ),
            None,
        ),
        "territory": post.location,
        "travel_percentage": travel_match.group(1) if travel_match else None,
        "responsibilities": _lines_after_heading(text, ("responsibilit", "what you will do")),
        "mandatory_qualifications": _lines_after_heading(
            text, ("required qualification", "minimum qualification", "requirements")
        ),
        "preferred_qualifications": _lines_after_heading(
            text, ("preferred qualification", "preferred experience")
        ),
        "education_requirements": [
            line
            for line in text.splitlines()
            if re.search(r"\b(ph\.?d|degree|master|bachelor)", line, re.I)
        ][:15],
        "experience_requirements": [
            line for line in text.splitlines() if re.search(r"\b\d+\+?\s+years?", line, re.I)
        ][:15],
        "therapeutic_area": "oncology" if "oncology" in lowered else None,
        "technical_keywords": sorted(
            term
            for term in (
                "oncology",
                "CAR T",
                "immunology",
                "flow cytometry",
                "RNA-seq",
                "medical affairs",
                "scientific communication",
            )
            if term.casefold() in lowered
        ),
        "application_url": source_url,
        "ats_platform": (
            "workday" if source_url and "myworkdayjobs.com" in source_url else "unknown"
        ),
    }
    return PostingIntake(
        raw_text=raw,
        normalized_text=text,
        normalized_url=redact_url_tracking_parameters(source_url) if source_url else None,
        retrieval_method=method,
        complete=complete,
        warnings=warnings,
        fields=fields,
    )


def fetch_rendered_posting(url: str, *, visible: bool = False, timeout: float | None = None) -> str:
    timeout_ms = int((timeout or 30.0) * 1000)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not visible)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            page.wait_for_function(
                """
                () => {
                  const text = document.body?.innerText || "";
                  return text.length > 1000 &&
                    /qualifications|responsibilities|job description/i.test(text);
                }
                """,
                timeout=15_000,
            )
        except Exception:
            # The completeness validator will reject the captured shell or gated response.
            pass
        text = page.locator("body").inner_text()
        browser.close()
    return text


def intake_url(url: str, *, timeout: float | None = None) -> PostingIntake:
    host = (urlsplit(url).hostname or "").casefold()
    if "myworkdayjobs.com" in host:
        return normalize_posting(
            fetch_rendered_posting(url, timeout=timeout),
            source_url=url,
            method="playwright",
        )
    try:
        raw = fetch_job_url(url, timeout=timeout)
    except JobURLFetchError:
        raise
    return normalize_posting(raw, source_url=url, method="https")


def save_posting(connection: sqlite3.Connection, intake: PostingIntake) -> int:
    post = normalize_job(intake.normalized_text, source=intake.retrieval_method)
    cursor = connection.execute(
        """
        INSERT INTO jobs
          (source, company, title, location, remote_status, salary, url, ats_type,
           description, required_qualifications, preferred_qualifications,
           responsibilities, travel_requirement, date_discovered)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            post.source,
            intake.fields.get("employer") or post.company,
            intake.fields.get("title") or post.title,
            intake.fields.get("location") or post.location,
            intake.fields.get("remote_status"),
            intake.fields.get("compensation"),
            intake.normalized_url,
            intake.fields.get("ats_platform"),
            intake.normalized_text,
            json.dumps(intake.fields.get("mandatory_qualifications", [])),
            json.dumps(intake.fields.get("preferred_qualifications", [])),
            json.dumps(intake.fields.get("responsibilities", [])),
            intake.fields.get("travel_percentage"),
            utc_now(),
        ),
    )
    job_id = int(cursor.lastrowid or 0)
    save_posting_for_job(connection, job_id, intake)
    return job_id


def save_posting_for_job(
    connection: sqlite3.Connection,
    job_id: int,
    intake: PostingIntake,
    *,
    preserve_existing_url: bool = False,
) -> None:
    connection.execute(
        """
        INSERT INTO job_postings
          (job_id, raw_text, normalized_text, normalized_url, retrieval_method,
           retrieved_at, complete, completeness_warnings, normalized_fields)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_id) DO UPDATE SET
          raw_text=excluded.raw_text, normalized_text=excluded.normalized_text,
          normalized_url=excluded.normalized_url, retrieval_method=excluded.retrieval_method,
          retrieved_at=excluded.retrieved_at, complete=excluded.complete,
          completeness_warnings=excluded.completeness_warnings,
          normalized_fields=excluded.normalized_fields
        """,
        (
            job_id,
            intake.raw_text,
            intake.normalized_text,
            intake.normalized_url,
            intake.retrieval_method,
            utc_now(),
            int(intake.complete),
            json.dumps(intake.warnings),
            json.dumps(intake.fields),
        ),
    )
    connection.execute(
        """
        UPDATE jobs SET company=?, title=?, location=?, remote_status=?, salary=?,
          url=CASE WHEN ? THEN url ELSE ? END, ats_type=?, description=?, required_qualifications=?,
          preferred_qualifications=?, responsibilities=?, travel_requirement=?
        WHERE id=?
        """,
        (
            intake.fields.get("employer"),
            intake.fields.get("title"),
            intake.fields.get("location"),
            intake.fields.get("remote_status"),
            intake.fields.get("compensation"),
            int(preserve_existing_url),
            intake.normalized_url,
            intake.fields.get("ats_platform"),
            intake.normalized_text,
            json.dumps(intake.fields.get("mandatory_qualifications", [])),
            json.dumps(intake.fields.get("preferred_qualifications", [])),
            json.dumps(intake.fields.get("responsibilities", [])),
            intake.fields.get("travel_percentage"),
            job_id,
        ),
    )
    connection.commit()


def intake_file(path: Path) -> PostingIntake:
    if path.suffix.casefold() in {".md", ".markdown"}:
        raw = path.read_text(encoding="utf-8")
    else:
        raw = extract_text(path)
    return normalize_posting(raw, source_url=None, method=f"file:{path.suffix.casefold()}")


def import_csv(connection: sqlite3.Connection, path: Path) -> list[int]:
    ids: list[int] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            raw = row.get("text") or row.get("description") or ""
            url = row.get("url") or None
            intake = (
                intake_url(url)
                if url and not raw
                else normalize_posting(raw, source_url=url, method="csv")
            )
            ids.append(save_posting(connection, intake))
    return ids
