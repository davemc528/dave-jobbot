from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class CandidateFact:
    category: str
    field_name: str
    value: str | None = None
    verified: bool = False
    source: str | None = None
    notes: str | None = None


@dataclass(slots=True)
class ReusableAnswer:
    question_pattern: str
    answer: str
    source: str | None = None
    verification_status: str = "unverified"
    last_reviewed: str | None = None
    sensitivity_level: str = "low"
    may_autofill: bool = False


@dataclass(slots=True)
class JobPost:
    source: str
    company: str | None = None
    title: str | None = None
    location: str | None = None
    remote_status: str | None = None
    salary: str | None = None
    url: str | None = None
    ats_type: str | None = None
    description: str = ""
    required_qualifications: list[str] = field(default_factory=list)
    preferred_qualifications: list[str] = field(default_factory=list)
    responsibilities: list[str] = field(default_factory=list)
    travel_requirement: str | None = None
    date_discovered: str = field(default_factory=lambda: utc_now())


@dataclass(slots=True)
class JobScore:
    job_id: int | None = None
    overall_score: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    selected_track: str | None = None
    confidence: float = 0.0
    human_review_required: bool = True

    @property
    def score(self) -> float:
        return self.overall_score

    @property
    def score_breakdown(self) -> dict[str, float]:
        return self.breakdown


@dataclass(slots=True)
class ApplicationRecord:
    id: int | None = None
    job_id: int | None = None
    status: str = "draft"
    selected_track: str | None = None
    resume_path: str | None = None
    answers: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
