from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from jobbot.config import BASE_DIR

PROFILE_PATH = BASE_DIR / "data" / "profile.yaml"
ANSWERS_PATH = BASE_DIR / "data" / "answers.yaml"

DEFAULT_PROFILE: dict[str, Any] = {
    "contact_information": {},
    "work_authorization": {},
    "geographic_preferences": {
        "priorities": [
            "San Diego",
            "San Francisco Bay Area",
            "Cambridge/Boston",
            "Remote",
            "Other locations considered when strategically valuable",
        ]
    },
    "employment_history": [],
    "education": [],
    "publications": [],
    "patents_and_intellectual_property": [],
    "technical_skills": [],
    "teaching_experience": [],
    "medical_affairs_capabilities": [],
    "field_applications_capabilities": [],
    "compensation_preferences": {},
    "travel_and_relocation_preferences": {},
    "eeo_answers": {},
    "verified_application_answers": {},
    "facts_that_require_confirmation": [],
}

DEFAULT_ANSWERS: dict[str, Any] = {
    "answers": [],
}


def _ensure_file(path: Path, content: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(yaml.safe_dump(content, sort_keys=False), encoding="utf-8")


def load_profile() -> dict[str, object]:
    _ensure_file(PROFILE_PATH, DEFAULT_PROFILE)
    with PROFILE_PATH.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return {**DEFAULT_PROFILE, **data}


def save_profile(data: dict[str, object]) -> None:
    _ensure_file(PROFILE_PATH, DEFAULT_PROFILE)
    with PROFILE_PATH.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)


def load_answers() -> dict[str, object]:
    _ensure_file(ANSWERS_PATH, DEFAULT_ANSWERS)
    with ANSWERS_PATH.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return {**DEFAULT_ANSWERS, **data}


def save_answers(data: dict[str, object]) -> None:
    _ensure_file(ANSWERS_PATH, DEFAULT_ANSWERS)
    with ANSWERS_PATH.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)


def upsert_fact(category: str, field_name: str, value: str | None, verified: bool = False) -> None:
    data = load_profile()
    facts = data.setdefault(category, [])
    if not isinstance(facts, list):
        facts = []
        data[category] = facts
    for entry in facts:
        if isinstance(entry, dict) and entry.get("field_name") == field_name:
            entry["value"] = value
            entry["verified"] = verified
            save_profile(data)
            return
    facts.append({"field_name": field_name, "value": value, "verified": verified})
    save_profile(data)


def list_facts() -> list[dict[str, object]]:
    data = load_profile()
    facts: list[dict[str, object]] = []
    for category, entries in data.items():
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    facts.append({"category": category, **entry})
    return facts
