from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
SCREENSHOT_DIR = BASE_DIR / "screenshots"
BROWSER_PROFILE_DIR = BASE_DIR / "browser_profiles"
SOURCE_DIR = BASE_DIR / "source_documents"


def resolve_db_path(explicit: Path | str | None = None) -> Path:
    configured = (
        Path(explicit)
        if explicit is not None
        else Path(os.getenv("JOBBOT_DB_PATH", "data/jobbot.db"))
    )
    return configured.resolve() if configured.is_absolute() else (BASE_DIR / configured).resolve()


DB_PATH = resolve_db_path()


class AutomationConfig(BaseModel):
    enabled: bool = False
    mode: str = "automatic_dry_run"
    final_submit_enabled: bool = False
    visible_browser: bool = True
    stop_before_submit: bool = True
    captcha_policy: str = "pause_for_human"
    minimum_autofill_confidence: float = 0.92
    maximum_travel_percentage: int = 60
    real_site_dry_run_enabled: bool = False
    require_cli_confirmation: bool = True


class RealSiteConfig(BaseModel):
    allowed_domains: list[str] = []
    block_unknown_domains: bool = True
    preserve_browser_state: bool = True
    screenshot_checkpoints: bool = True
    approved_resumes: dict[str, str] = {}


class RuntimeConfig(BaseModel):
    automation: AutomationConfig = AutomationConfig()
    real_site: RealSiteConfig = RealSiteConfig()


LOCAL_AUTOMATION_CONFIG = BASE_DIR / "config" / "automation.local.yaml"


def load_runtime_config(path: Path | None = None) -> RuntimeConfig:
    target = path or LOCAL_AUTOMATION_CONFIG
    if not target.is_file():
        return RuntimeConfig()
    content = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    return RuntimeConfig.model_validate(content)


RUNTIME = load_runtime_config()
AUTOMATION = RUNTIME.automation
REAL_SITE = RUNTIME.real_site


def ensure_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
