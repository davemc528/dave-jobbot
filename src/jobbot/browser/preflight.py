from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field

from jobbot.config import AUTOMATION, BASE_DIR, REAL_SITE, AutomationConfig, RealSiteConfig
from jobbot.profile.effective_profile import resolve_effective_profile
from jobbot.tailoring.routing import select_resume_track


class ResumeApproval(BaseModel):
    track: str
    path: str | None = None
    exists: bool = False
    ignored_or_untracked: bool = False
    approved: bool = False
    explanation: str


class RealSitePreflight(BaseModel):
    job_id: int
    original_url: str
    normalized_url: str
    hostname: str
    domain_allowed: bool
    ats_type: str
    browser_mode: str
    visible_browser: bool
    selected_resume: ResumeApproval
    profile_ready: bool
    profile_failures: list[str] = Field(default_factory=list)
    outstanding_review_blockers: int = 0
    captcha_policy: str
    stop_before_submit: bool
    final_submit_enabled: bool
    configuration_enabled: bool
    invocation_confirmed: bool
    permitted: bool
    blockers: list[str] = Field(default_factory=list)


def normalize_hostname(url: str) -> str:
    hostname = (urlsplit(url).hostname or "").rstrip(".").casefold()
    try:
        return hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return ""


def normalize_application_url(url: str) -> str:
    parts = urlsplit(url)
    hostname = normalize_hostname(url)
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit((parts.scheme.casefold(), f"{hostname}{port}", parts.path or "/", "", ""))


def domain_is_allowed(hostname: str, allowed_domains: list[str]) -> bool:
    normalized = hostname.rstrip(".").casefold()
    return normalized in {domain.rstrip(".").casefold() for domain in allowed_domains}


def redirect_chain_is_allowed(urls: list[str], allowed_domains: list[str]) -> bool:
    return all(domain_is_allowed(normalize_hostname(url), allowed_domains) for url in urls)


def _path_is_ignored_or_untracked(path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(BASE_DIR)
    except ValueError:
        return True
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", str(relative)],
        cwd=BASE_DIR,
        capture_output=True,
        check=False,
        text=True,
    )
    if tracked.returncode == 0:
        return False
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", "--", str(relative)],
        cwd=BASE_DIR,
        capture_output=True,
        check=False,
        text=True,
    )
    return ignored.returncode == 0


def approved_resume(
    track: str,
    real_site: RealSiteConfig = REAL_SITE,
) -> ResumeApproval:
    configured = real_site.approved_resumes.get(track)
    if not configured:
        return ResumeApproval(track=track, explanation="No approved resume is configured")
    path = Path(configured)
    resolved = path.resolve() if path.is_absolute() else (BASE_DIR / path).resolve()
    exists = resolved.is_file()
    safe_location = _path_is_ignored_or_untracked(resolved) if exists else False
    return ResumeApproval(
        track=track,
        path=str(resolved),
        exists=exists,
        ignored_or_untracked=safe_location,
        approved=exists and safe_location,
        explanation=(
            "Approved local resume exists outside Git tracking"
            if exists and safe_location
            else "Configured resume is missing or is tracked by Git"
        ),
    )


def build_preflight(
    connection: sqlite3.Connection,
    job_id: int,
    *,
    allow_real_site: bool,
    automation: AutomationConfig = AUTOMATION,
    real_site: RealSiteConfig = REAL_SITE,
) -> RealSitePreflight:
    row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None or not row["url"]:
        raise ValueError(f"Job {job_id} has no application URL")
    url = str(row["url"])
    hostname = normalize_hostname(url)
    allowed = domain_is_allowed(hostname, real_site.allowed_domains)
    route = select_resume_track(str(row["title"] or ""), str(row["description"] or ""))
    from jobbot.resumes.tailoring import approved_resume_for_job

    tailored_rows = connection.execute(
        "SELECT count(*) FROM tailored_resumes WHERE job_id=?", (job_id,)
    ).fetchone()[0]
    tailored = approved_resume_for_job(connection, job_id)
    if tailored:
        resume = ResumeApproval(
            track=tailored.selected_track,
            path=tailored.docx_path,
            exists=Path(tailored.docx_path).is_file(),
            ignored_or_untracked=_path_is_ignored_or_untracked(Path(tailored.docx_path)),
            approved=True,
            explanation=(
                f"Approved tailored resume version {tailored.version}; "
                f"validation={tailored.validation_status}"
            ),
        )
    elif tailored_rows:
        resume = ResumeApproval(
            track=route.selected_track,
            explanation="A tailored resume exists but is not approved and valid",
        )
    else:
        resume = approved_resume(route.selected_track, real_site)
    profile = resolve_effective_profile(connection).readiness
    pending = connection.execute(
        """
        SELECT count(*) FROM review_items
        WHERE status='pending' AND metadata IS NOT NULL
          AND (json_extract(metadata, '$.job_id')=? OR json_extract(metadata, '$.run_id') IN
            (SELECT id FROM automation_runs WHERE job_id=?))
        """,
        (job_id, job_id),
    ).fetchone()[0]
    blockers: list[str] = []
    if not automation.enabled or not automation.real_site_dry_run_enabled:
        blockers.append("Local configuration does not enable real-site dry runs")
    if automation.require_cli_confirmation and not allow_real_site:
        blockers.append("Pass --allow-real-site for this invocation")
    if not allowed:
        blockers.append(f"Hostname is not allowlisted: {hostname}")
    if not automation.visible_browser:
        blockers.append("Visible browser mode is required")
    if not automation.stop_before_submit or automation.final_submit_enabled:
        blockers.append("Submission protection is not configured safely")
    if not resume.approved:
        blockers.append(resume.explanation)
    if not profile.ready:
        blockers.extend(profile.failures)
    if pending:
        blockers.append(f"{pending} job-specific review blocker(s) remain")
    return RealSitePreflight(
        job_id=job_id,
        original_url=url,
        normalized_url=normalize_application_url(url),
        hostname=hostname,
        domain_allowed=allowed,
        ats_type=str(row["ats_type"] or "unknown"),
        browser_mode="automatic_dry_run",
        visible_browser=automation.visible_browser,
        selected_resume=resume,
        profile_ready=profile.ready,
        profile_failures=profile.failures,
        outstanding_review_blockers=int(pending),
        captcha_policy=automation.captcha_policy,
        stop_before_submit=automation.stop_before_submit,
        final_submit_enabled=automation.final_submit_enabled,
        configuration_enabled=automation.enabled and automation.real_site_dry_run_enabled,
        invocation_confirmed=allow_real_site,
        permitted=not blockers,
        blockers=blockers,
    )


def audit_preflight(connection: sqlite3.Connection, report: RealSitePreflight) -> None:
    connection.execute(
        """
        INSERT INTO profile_audit_log (action, actor, after_json, notes, created_at)
        VALUES ('real_site_preflight', 'system', ?, ?, datetime('now'))
        """,
        (
            json.dumps(report.model_dump()),
            "Protected local audit of real-site authorization and URL normalization",
        ),
    )
    connection.commit()
