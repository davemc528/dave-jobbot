from __future__ import annotations

import importlib
from pathlib import Path

from jobbot import cli
from jobbot.config import get_runtime_config, resolve_db_path


def test_dashboard_imports_without_launching_streamlit(monkeypatch) -> None:
    import jobbot.ui.dashboard as dashboard

    monkeypatch.setattr(
        dashboard.st.sidebar,
        "radio",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("dashboard launched during import")
        ),
    )
    imported = importlib.reload(dashboard)
    assert callable(imported.launch_dashboard)


def test_dashboard_and_cli_share_database_path(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "shared-dashboard.db"
    monkeypatch.setenv("JOBBOT_DB_PATH", str(database))
    assert resolve_db_path() == database.resolve()
    assert cli.resolve_db_path() == database.resolve()


def test_dashboard_reads_current_runtime_configuration(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "automation.yaml"
    config_path.write_text(
        """
automation:
  enabled: true
  mode: automatic_dry_run
  final_submit_enabled: false
  visible_browser: true
  stop_before_submit: true
  captcha_policy: pause_for_human
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("JOBBOT_AUTOMATION_CONFIG", str(config_path))

    from jobbot.ui.dashboard import dashboard_runtime_status

    status = dashboard_runtime_status()
    assert status["automatic_dry_run_enabled"] is True
    assert status["configured_mode"] == "automatic_dry_run"
    assert status["final_submit_enabled"] is False
    assert status["stop_before_submit"] is True


def test_missing_optional_local_configuration_uses_safe_defaults(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("JOBBOT_AUTOMATION_CONFIG", str(tmp_path / "missing-automation.yaml"))
    runtime = get_runtime_config()
    assert runtime.automation.enabled is False
    assert runtime.automation.final_submit_enabled is False
    assert runtime.automation.stop_before_submit is True
    assert runtime.automation.captcha_policy == "pause_for_human"
