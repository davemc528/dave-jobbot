from __future__ import annotations

import importlib.util
import json
import os
import platform
import sqlite3
from pathlib import Path
from typing import cast
from urllib.request import Request, urlopen

import typer

from jobbot.applications.records import create_application
from jobbot.browser.playwright_mvp import dry_run_autofill, inspect_form
from jobbot.config import BASE_DIR, DB_PATH, SOURCE_DIR, ensure_directories
from jobbot.db import get_connection
from jobbot.documents.extract import extract_text, infer_document_type
from jobbot.documents.facts import EXPECTED_DOCUMENTS, extract_candidate_facts
from jobbot.jobs.scoring import normalize_job, score_job
from jobbot.models import JobPost
from jobbot.profile.store import load_profile
from jobbot.profile.canonical import (
    apply_canonical_proposals,
    profile_readiness,
    propose_canonical_groups,
    seed_phase_15_proposals,
)
from jobbot.security import safe_log
from jobbot.tailoring.routing import select_resume_track

app = typer.Typer(help="Local, human-in-the-loop job application assistant.")
profile_app = typer.Typer(help="Review canonical candidate facts.")
job_app = typer.Typer(help="Ingest a job.")
jobs_app = typer.Typer(help="List and score jobs.")
application_app = typer.Typer(help="Prepare applications.")
browser_app = typer.Typer(help="Safely inspect and dry-run application forms.")
app.add_typer(profile_app, name="profile")
app.add_typer(job_app, name="job")
app.add_typer(jobs_app, name="jobs")
app.add_typer(application_app, name="application")
app.add_typer(browser_app, name="browser")


@app.command()
def init() -> None:
    ensure_directories()
    with get_connection():
        pass
    typer.echo(f"Initialized local datastore at {DB_PATH}.")


@app.command("import-documents")
def import_documents() -> None:
    if not SOURCE_DIR.exists():
        raise typer.BadParameter(f"Source directory does not exist: {SOURCE_DIR}")
    paths = [SOURCE_DIR / filename for filename in EXPECTED_DOCUMENTS]
    missing = [path.name for path in paths if not path.is_file()]
    if missing:
        raise typer.BadParameter(f"Missing required source documents: {', '.join(missing)}")
    connection = get_connection()
    for path in paths:
        text = extract_text(path)
        checksum = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
        analysis = extract_candidate_facts(path.name, text)
        connection.execute("DELETE FROM candidate_facts WHERE source = ?", (path.name,))
        connection.execute("DELETE FROM documents WHERE filename = ?", (path.name,))
        connection.execute(
            """
            INSERT INTO documents
              (filename, document_type, source_path, extracted_text, metadata, checksum)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                path.name,
                infer_document_type(path),
                str(path),
                text,
                json.dumps(
                    {
                        "candidate_track": analysis.track,
                        "fact_count": len(analysis.facts),
                        "ambiguous_items": analysis.ambiguous_items,
                    }
                ),
                checksum,
            ),
        )
        connection.executemany(
            """
            INSERT INTO candidate_facts
              (category, field_name, value, verified, source, notes)
            VALUES (?, ?, ?, 0, ?, ?)
            """,
            [
                (fact.category, fact.field_name, fact.value, fact.source, fact.notes)
                for fact in analysis.facts
            ],
        )
        connection.execute(
            "DELETE FROM review_items WHERE item_type = 'document_facts' AND summary LIKE ?",
            (f"%{path.name}%",),
        )
        connection.execute(
            """
            INSERT INTO review_items (item_type, summary, status, created_at)
            VALUES ('document_facts', ?, 'pending', datetime('now'))
            """,
            (f"Review extracted facts from {path.name}; all facts remain unverified",),
        )
        typer.echo(
            f"Imported {path.name}: {len(analysis.facts)} unverified facts; track={analysis.track}"
        )
    connection.commit()
    connection.close()


@profile_app.command("review")
def profile_review() -> None:
    profile = load_profile()
    typer.echo("Profile facts requiring confirmation:")
    facts = cast(list[object], profile.get("facts_that_require_confirmation", []))
    for fact in facts:
        typer.echo(f"- {fact}")
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT category, field_name, value, source
            FROM candidate_facts WHERE verified = 0
            ORDER BY source, category, field_name
            """
        ).fetchall()
    typer.echo(f"Extracted unverified candidates: {len(rows)}")
    for row in rows:
        typer.echo(f"- [{row['source']}] {row['category']}.{row['field_name']}: {row['value']}")


@profile_app.command("canonicalize")
def profile_canonicalize(
    apply: bool = typer.Option(False, "--apply", help="Apply the displayed merge proposal."),
) -> None:
    connection = get_connection()
    proposals = propose_canonical_groups(connection)
    typer.echo(f"Proposed canonical groups: {len(proposals)}")
    for proposal in proposals:
        typer.echo(
            f"- {proposal.category}.{proposal.field_name}: {proposal.display_value} "
            f"<- {', '.join(proposal.source_documents)} [{proposal.verification_status}]"
        )
    if apply:
        ids = apply_canonical_proposals(connection)
        seed_phase_15_proposals(connection)
        typer.echo(f"Applied {len(ids)} canonical groups; none were automatically verified.")
    else:
        typer.echo("Preview only. Re-run with --apply to persist these groups.")
    connection.close()


@profile_app.command("readiness")
def profile_readiness_command() -> None:
    with get_connection() as connection:
        report = profile_readiness(connection)
    typer.echo("READY" if report.ready else "NOT READY")
    for failure in report.failures:
        typer.echo(f"- {failure}")
    if not report.ready:
        raise typer.Exit(1)


def _fetch_url(url: str) -> str:
    request = Request(url, headers={"User-Agent": "dave-jobbot/0.1 (manual review)"})
    with urlopen(request, timeout=20) as response:  # noqa: S310
        return response.read().decode("utf-8", errors="replace")


@job_app.command("add")
def job_add(
    url: str | None = typer.Option(None, "--url"),
    file: Path | None = typer.Option(None, "--file", exists=True, dir_okay=False),
) -> None:
    if (url is None) == (file is None):
        raise typer.BadParameter("Provide exactly one of --url or --file")
    raw_text = _fetch_url(url) if url else file.read_text(encoding="utf-8")  # type: ignore[union-attr]
    job = normalize_job(raw_text, source="url" if url else "manual")
    job.url = url
    connection = get_connection()
    duplicate = (
        connection.execute("SELECT id FROM jobs WHERE url = ?", (url,)).fetchone() if url else None
    )
    if duplicate:
        connection.close()
        raise typer.BadParameter(f"Duplicate job URL (existing id {duplicate['id']})")
    cursor = connection.execute(
        """
        INSERT INTO jobs
          (source, company, title, location, remote_status, salary, url, ats_type,
           description, required_qualifications, preferred_qualifications, responsibilities,
           travel_requirement, date_discovered)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job.source,
            job.company,
            job.title,
            job.location,
            job.remote_status,
            job.salary,
            job.url,
            job.ats_type,
            job.description,
            json.dumps(job.required_qualifications),
            json.dumps(job.preferred_qualifications),
            json.dumps(job.responsibilities),
            job.travel_requirement,
            job.date_discovered,
        ),
    )
    connection.commit()
    typer.echo(f"Added job with id {cursor.lastrowid}")
    connection.close()


def _job_from_row(row: sqlite3.Row) -> JobPost:
    return JobPost(
        source=row["source"],
        company=row["company"],
        title=row["title"],
        location=row["location"],
        url=row["url"],
        description=row["description"] or "",
    )


@jobs_app.command("score")
def jobs_score() -> None:
    connection = get_connection()
    rows = connection.execute("SELECT * FROM jobs ORDER BY id DESC").fetchall()
    for row in rows:
        result = score_job(_job_from_row(row))
        connection.execute("DELETE FROM job_scores WHERE job_id = ?", (row["id"],))
        connection.execute(
            """
            INSERT INTO job_scores
              (job_id, overall_score, reasons, conflicts, selected_track, confidence,
               human_review_required)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                result.overall_score,
                json.dumps(result.reasons),
                json.dumps(result.conflicts),
                result.selected_track,
                result.confidence,
                int(result.human_review_required),
            ),
        )
        typer.echo(
            f"Job {row['id']} {row['title']}: {result.overall_score}/100 "
            f"({result.selected_track})\nBreakdown: {json.dumps(result.breakdown)}"
        )
    connection.commit()
    connection.close()


@jobs_app.command("list")
def jobs_list() -> None:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT id, title, company, location, status FROM jobs ORDER BY id DESC"
        ).fetchall()
    for row in rows:
        typer.echo(dict(row))


@application_app.command("prepare")
def application_prepare(job_id: int) -> None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise typer.BadParameter(f"Unknown job id: {job_id}")
    route = select_resume_track(row["title"] or "", row["description"] or "")
    record = create_application(job_id, route.selected_track)
    typer.echo(f"Prepared application {record.id}; resume route requires human approval.")


def _application_url(job_id: int) -> str:
    with get_connection() as connection:
        row = connection.execute("SELECT url FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None or not row["url"]:
        raise typer.BadParameter(f"Job {job_id} has no application URL")
    return str(row["url"])


@browser_app.command("inspect")
def browser_inspect(
    job_id: int, visible: bool = typer.Option(True, "--visible/--headless")
) -> None:
    typer.echo(json.dumps(inspect_form(_application_url(job_id), visible=visible), indent=2))


@browser_app.command("autofill")
def browser_autofill(
    job_id: int,
    dry_run: bool = typer.Option(False, "--dry-run"),
    visible: bool = typer.Option(True, "--visible/--headless"),
) -> None:
    if not dry_run:
        raise typer.BadParameter("Phase 1 permits only --dry-run autofill")
    result = dry_run_autofill(_application_url(job_id), visible=visible)
    safe_log("Browser dry run completed", job_id=job_id, status=result["status"])
    typer.echo(json.dumps(result, indent=2))


@app.command()
def dashboard() -> None:
    from streamlit.web import bootstrap

    bootstrap.run(str(BASE_DIR / "src/jobbot/ui/dashboard.py"), False, [], {})


@app.command()
def doctor() -> None:
    checks: list[tuple[str, bool, str]] = []
    checks.append(
        (
            "Python 3.12+",
            tuple(map(int, platform.python_version_tuple()[:2])) >= (3, 12),
            platform.python_version(),
        )
    )
    checks.append(
        ("Playwright package", importlib.util.find_spec("playwright") is not None, "installed")
    )
    found_documents = [name for name in EXPECTED_DOCUMENTS if (SOURCE_DIR / name).is_file()]
    checks.append(
        (
            "Required source documents",
            len(found_documents) == len(EXPECTED_DOCUMENTS),
            f"{len(found_documents)}/{len(EXPECTED_DOCUMENTS)} detected: "
            + ", ".join(found_documents),
        )
    )
    try:
        with get_connection() as connection:
            connection.execute("SELECT 1")
        checks.append(("SQLite access", True, str(DB_PATH)))
    except sqlite3.Error as exc:
        checks.append(("SQLite access", False, str(exc)))
    chromium_found = False
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            chromium_found = Path(playwright.chromium.executable_path).exists()
    except Exception:
        pass
    checks.append(
        ("Playwright Chromium", chromium_found, "run: uv run playwright install chromium")
    )
    checks.append(
        (
            "Remote LLM disabled by default",
            os.getenv("JOBBOT_ALLOW_REMOTE_LLM", "false").lower() != "true",
            "privacy default",
        )
    )
    failed = False
    for name, passed, detail in checks:
        failed |= not passed
        typer.echo(f"{'PASS' if passed else 'FAIL'} {name}: {detail}")
    if failed:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
