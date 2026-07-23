import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jobbot.browser.automation import authentication_required, run_automation
from jobbot.browser.preflight import (
    build_preflight,
    domain_is_allowed,
    redirect_chain_is_allowed,
)
from jobbot.config import AutomationConfig, RealSiteConfig
from jobbot.db import initialize_schema

EISAI = "eisai.wd5.myworkdayjobs.com"
JOB_URL = f"https://{EISAI}/eisai/job/example/apply?utm_source=test"


def database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    connection.execute(
        """
        INSERT INTO jobs (id, title, description, url, ats_type, source)
        VALUES (2, 'Oncology Medical Science Liaison', 'MSL oncology field role', ?,
                'workday', 'url')
        """,
        (JOB_URL,),
    )
    connection.commit()
    return connection


def configs(resume: Path, *, enabled: bool = True) -> tuple[AutomationConfig, RealSiteConfig]:
    return (
        AutomationConfig(
            enabled=enabled,
            real_site_dry_run_enabled=enabled,
            visible_browser=True,
            stop_before_submit=True,
            final_submit_enabled=False,
        ),
        RealSiteConfig(
            allowed_domains=[EISAI],
            approved_resumes={"MSL / Medical Affairs": str(resume)},
        ),
    )


def ready_profile() -> SimpleNamespace:
    return SimpleNamespace(readiness=SimpleNamespace(ready=True, failures=[]))


def test_two_key_authorization_and_conservative_default(tmp_path: Path) -> None:
    resume = tmp_path / "approved.resume.docx"
    resume.write_bytes(b"approved")
    automation, real_site = configs(resume)
    connection = database()
    with patch("jobbot.browser.preflight.resolve_effective_profile", return_value=ready_profile()):
        missing_flag = build_preflight(
            connection,
            2,
            allow_real_site=False,
            automation=automation,
            real_site=real_site,
        )
        config_disabled = build_preflight(
            connection,
            2,
            allow_real_site=True,
            automation=AutomationConfig(),
            real_site=real_site,
        )
        permitted = build_preflight(
            connection,
            2,
            allow_real_site=True,
            automation=automation,
            real_site=real_site,
        )
    assert AutomationConfig().real_site_dry_run_enabled is False
    assert AutomationConfig().visible_browser is True
    assert missing_flag.permitted is False
    assert "Pass --allow-real-site for this invocation" in missing_flag.blockers
    assert config_disabled.permitted is False
    assert "Local configuration does not enable real-site dry runs" in config_disabled.blockers
    assert permitted.permitted is True
    assert permitted.selected_resume.approved is True
    assert permitted.selected_resume.track == "MSL / Medical Affairs"


def test_exact_domain_allowlist_and_redirect_protection() -> None:
    assert domain_is_allowed(EISAI, [EISAI])
    assert domain_is_allowed(f"{EISAI}.", [EISAI])
    assert not domain_is_allowed(f"{EISAI}.attacker.example", [EISAI])
    assert not domain_is_allowed(f"evil-{EISAI}", [EISAI])
    assert redirect_chain_is_allowed(
        [JOB_URL, f"https://{EISAI}/eisai/apply/next"],
        [EISAI],
    )
    assert not redirect_chain_is_allowed(
        [JOB_URL, "https://accounts.attacker.example/steal"],
        [EISAI],
    )


def test_missing_resume_approval_blocks_run(tmp_path: Path) -> None:
    automation, real_site = configs(tmp_path / "missing.docx")
    connection = database()
    with patch("jobbot.browser.preflight.resolve_effective_profile", return_value=ready_profile()):
        report = build_preflight(
            connection,
            2,
            allow_real_site=True,
            automation=automation,
            real_site=real_site,
        )
    assert report.permitted is False
    assert report.selected_resume.approved is False
    assert "missing or is tracked" in report.selected_resume.explanation


def test_authentication_is_human_intervention_and_submit_mode_is_unavailable() -> None:
    assert authentication_required("Create Account or Sign In") is True
    assert authentication_required("Application questions") is False
    connection = database()
    try:
        run_automation(
            connection,
            JOB_URL,
            mode="automatic_submit",
            allow_real_site=True,
        )
    except NotImplementedError as exc:
        assert "disabled and unimplemented" in str(exc)
    else:
        raise AssertionError("automatic_submit must remain unavailable")
