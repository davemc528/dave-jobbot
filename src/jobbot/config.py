from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
SCREENSHOT_DIR = BASE_DIR / "screenshots"
SOURCE_DIR = BASE_DIR / "source_documents"
DB_PATH = BASE_DIR / os.getenv("JOBBOT_DB_PATH", "data/jobbot.db")


class AutomationConfig(BaseModel):
    enabled: bool = False
    mode: str = "automatic_dry_run"
    final_submit_enabled: bool = False
    visible_browser: bool = True
    stop_before_submit: bool = True
    captcha_policy: str = "pause_for_human"
    minimum_autofill_confidence: float = 0.92
    maximum_travel_percentage: int = 60


AUTOMATION = AutomationConfig()


def ensure_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
