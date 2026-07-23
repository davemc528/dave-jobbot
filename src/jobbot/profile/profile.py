from __future__ import annotations

from typing import Any


def build_fact_snapshot(
    *,
    contact: dict[str, Any] | None = None,
    employment_history: list[dict[str, Any]] | None = None,
    education: list[dict[str, Any]] | None = None,
    technical_skills: list[str] | None = None,
    facts_requiring_confirmation: list[str] | None = None,
) -> dict[str, Any]:
    snapshot = {
        "contact": contact or {},
        "employment_history": employment_history or [],
        "education": education or [],
        "technical_skills": technical_skills or [],
        "facts_requiring_confirmation": facts_requiring_confirmation or [],
    }
    return snapshot
