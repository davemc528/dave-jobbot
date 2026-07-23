from __future__ import annotations

import importlib.util
import json
import os
import platform
import sqlite3
import ssl
from pathlib import Path
from typing import cast

import certifi
import typer

from jobbot.applications.records import create_application
from jobbot.browser.automation import AutomationMode, resume_run, run_automation
from jobbot.browser.playwright_mvp import inspect_form
from jobbot.config import BASE_DIR, SOURCE_DIR, ensure_directories, resolve_db_path
from jobbot.db import get_connection
from jobbot.documents.extract import extract_text, infer_document_type
from jobbot.documents.facts import EXPECTED_DOCUMENTS, extract_candidate_facts
from jobbot.jobs.scoring import normalize_job, score_job
from jobbot.jobs.fetch import JobURLFetchError, fetch_job_url
from jobbot.models import JobPost
from jobbot.profile.store import load_profile
from jobbot.profile.canonical import (
    apply_canonical_proposals,
    profile_readiness,
    propose_canonical_groups,
    seed_phase_15_proposals,
)
from jobbot.profile.application_answers import (
    apply_approved_defaults,
    effective_answer_state,
    list_answers,
)
from jobbot.profile.effective_profile import resolve_effective_profile
from jobbot.security import safe_log
from jobbot.tailoring.routing import select_resume_track

app = typer.Typer(help="Local, human-in-the-loop job application assistant.")
profile_app = typer.Typer(help="Review canonical candidate facts.")
job_app = typer.Typer(help="Ingest a job.")
jobs_app = typer.Typer(help="List and score jobs.")
application_app = typer.Typer(help="Prepare applications.")
browser_app = typer.Typer(help="Safely inspect and dry-run application forms.")
intake_app = typer.Typer(help="Apply explicitly approved profile answers.")
answers_app = typer.Typer(help="Inspect verified application answers.")
review_app = typer.Typer(help="Review automation blockers.")
profile_app.add_typer(intake_app, name="intake")
profile_app.add_typer(answers_app, name="answers")
app.add_typer(profile_app, name="profile")
app.add_typer(job_app, name="job")
app.add_typer(jobs_app, name="jobs")
app.add_typer(application_app, name="application")
app.add_typer(browser_app, name="browser")
app.add_typer(review_app, name="review")


@app.command()
def init() -> None:
    ensure_directories()
    with get_connection():
        pass
    typer.echo(f"Initialized local datastore at {resolve_db_path()}.")


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
        existing_rows = connection.execute(
            """
            SELECT id, category, field_name, value
            FROM candidate_facts WHERE source = ? ORDER BY id
            """,
            (path.name,),
        ).fetchall()
        existing: dict[tuple[str, str, str], list[int]] = {}
        for row in existing_rows:
            key = (str(row["category"]), str(row["field_name"]), str(row["value"] or ""))
            existing.setdefault(key, []).append(int(row["id"]))
        retained_ids: set[int] = set()
        for fact in analysis.facts:
            key = (fact.category, fact.field_name, str(fact.value or ""))
            matched_ids = existing.get(key, [])
            if matched_ids:
                raw_id = matched_ids.pop(0)
                retained_ids.add(raw_id)
                connection.execute(
                    """
                    UPDATE candidate_facts SET verified = 0, notes = ?
                    WHERE id = ?
                    """,
                    (fact.notes, raw_id),
                )
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO candidate_facts
                      (category, field_name, value, verified, source, notes)
                    VALUES (?, ?, ?, 0, ?, ?)
                    """,
                    (fact.category, fact.field_name, fact.value, fact.source, fact.notes),
                )
                if cursor.lastrowid is not None:
                    retained_ids.add(int(cursor.lastrowid))
        for row in existing_rows:
            raw_id = int(row["id"])
            if raw_id in retained_ids:
                continue
            referenced = connection.execute(
                "SELECT 1 FROM canonical_fact_sources WHERE raw_fact_id = ?", (raw_id,)
            ).fetchone()
            if referenced:
                connection.execute(
                    """
                    UPDATE candidate_facts SET notes =
                      'No longer present in latest extraction; retained for canonical provenance'
                    WHERE id = ?
                    """,
                    (raw_id,),
                )
            else:
                connection.execute("DELETE FROM candidate_facts WHERE id = ?", (raw_id,))
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


@intake_app.command("apply-approved-defaults")
def apply_profile_defaults() -> None:
    with get_connection() as connection:
        result = apply_approved_defaults(connection)
    typer.echo(
        f"Approved defaults: inserted={result.inserted}, updated={result.updated}, "
        f"unchanged={result.unchanged}, superseded={result.superseded}, "
        f"active={result.active_count}"
    )


@answers_app.command("list")
def profile_answers_list() -> None:
    with get_connection() as connection:
        answers = list_answers(connection)
        profile = resolve_effective_profile(connection)
    for answer in answers:
        state = effective_answer_state(answer)
        logical = (
            "requires_sponsorship"
            if answer.field_name == "sponsorship_required"
            else answer.field_name
        )
        resolved = profile.fields.get(logical)
        source = (
            f"{resolved.source_table}#{resolved.source_record_id}"
            if resolved and resolved.source_table
            else "unresolved"
        )
        typer.echo(
            f"{answer.field_name}: {answer.display_value} "
            f"[{answer.verification_status}; {state.status}; effective_source={source}]"
        )


@answers_app.command("audit")
def profile_answers_audit() -> None:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT created_at, action, after_json, notes FROM profile_audit_log
            WHERE action LIKE 'approved_default_%'
               OR action IN ('application_answer_superseded', 'application_answer_edited')
            ORDER BY id DESC
            """
        ).fetchall()
    for row in rows:
        typer.echo(dict(row))


@job_app.command("add")
def job_add(
    url: str | None = typer.Option(None, "--url"),
    file: Path | None = typer.Option(None, "--file", exists=True, dir_okay=False),
    timeout: float | None = typer.Option(
        None,
        "--timeout",
        min=0.1,
        help="URL request timeout in seconds (or set JOBBOT_REQUEST_TIMEOUT).",
    ),
) -> None:
    if (url is None) == (file is None):
        raise typer.BadParameter("Provide exactly one of --url or --file")
    try:
        raw_text = (
            fetch_job_url(url, timeout=timeout) if url else file.read_text(encoding="utf-8")  # type: ignore[union-attr]
        )
    except JobURLFetchError as exc:
        raise typer.BadParameter(str(exc), param_hint="--url") from exc
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
    mode: str = typer.Option("supervised", "--mode"),
    visible: bool = typer.Option(True, "--visible/--headless"),
) -> None:
    normalized_mode = mode.replace("-", "_")
    if normalized_mode not in {
        "inspect",
        "supervised",
        "automatic_dry_run",
        "automatic_submit",
    }:
        raise typer.BadParameter(f"Unsupported mode: {mode}")
    with get_connection() as connection:
        result = run_automation(
            connection,
            _application_url(job_id),
            mode=cast(AutomationMode, normalized_mode),
            visible=visible,
            job_id=job_id,
        )
    safe_log("Browser dry run completed", job_id=job_id, status=result.status)
    typer.echo(result.model_dump_json(indent=2))


@browser_app.command("resume")
def browser_resume(run_id: int, visible: bool = typer.Option(True, "--visible/--headless")) -> None:
    with get_connection() as connection:
        result = resume_run(connection, run_id, visible=visible)
    typer.echo(result.model_dump_json(indent=2))


@review_app.command("list")
def review_list() -> None:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, item_type, summary, recommended_action, status
            FROM review_items WHERE status='pending' ORDER BY id
            """
        ).fetchall()
    for row in rows:
        typer.echo(dict(row))


@review_app.command("resolve")
def review_resolve(review_id: int) -> None:
    with get_connection() as connection:
        cursor = connection.execute(
            "UPDATE review_items SET status='resolved' WHERE id=?", (review_id,)
        )
    if cursor.rowcount == 0:
        raise typer.BadParameter(f"Unknown review item: {review_id}")
    typer.echo(f"Resolved review item {review_id}.")


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
    default_paths = ssl.get_default_verify_paths()
    ssl_cert_file = os.getenv("SSL_CERT_FILE")
    selected_bundle = Path(ssl_cert_file) if ssl_cert_file else Path(certifi.where())
    checks.append(
        (
            "TLS certificate configuration",
            selected_bundle.is_file(),
            f"default_paths={default_paths}; certifi={certifi.where()}; "
            f"SSL_CERT_FILE={ssl_cert_file or 'not configured'}; "
            f"selected_bundle_exists={selected_bundle.is_file()}",
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
            effective = resolve_effective_profile(connection)
            latest = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY applied_at DESC, version DESC LIMIT 1"
            ).fetchone()
            counts = connection.execute(
                """
                SELECT count(*) active,
                  sum(CASE WHEN verification_status='verified' THEN 1 ELSE 0 END) verified,
                  sum(CASE WHEN autofill_permission=1 AND verification_status='verified'
                           THEN 1 ELSE 0 END) autofill,
                  sum(CASE WHEN autofill_permission=0 THEN 1 ELSE 0 END) manual,
                  sum(CASE WHEN verification_status='conflicted' THEN 1 ELSE 0 END) conflicted,
                  sum(CASE WHEN review_required=1 THEN 1 ELSE 0 END) review_required
                FROM application_answers WHERE active=1
                """
            ).fetchone()
        resolved_db = resolve_db_path()
        checks.append(
            (
                "SQLite access",
                resolved_db.exists(),
                f"resolved={resolved_db}; exists={resolved_db.exists()}",
            )
        )
        checks.append(
            (
                "Database diagnostics",
                True,
                f"migration={latest['version'] if latest else 'none'}; "
                f"active={counts['active']}; verified={counts['verified'] or 0}; "
                f"autofill={counts['autofill'] or 0}; manual_only={counts['manual'] or 0}; "
                f"conflicted={counts['conflicted'] or 0}; "
                f"requiring_review={counts['review_required'] or 0}",
            )
        )
        checks.append(
            (
                "Effective profile resolver",
                True,
                f"ready={effective.readiness.ready}; "
                f"missing_conditions={len(effective.readiness.failures)}",
            )
        )
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
