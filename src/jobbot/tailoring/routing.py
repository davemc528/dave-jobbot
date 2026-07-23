from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ResumeRouteResult:
    selected_track: str
    confidence_score: float
    reasons: list[str]
    conflicting_signals: list[str]
    human_review_required: bool


MSL_KEYWORDS = {
    "medical science liaison",
    "medical affairs",
    "scientific communications",
    "oncology liaison",
    "field medical",
}

FAS_KEYWORDS = {
    "field application scientist",
    "technical applications",
    "flow cytometry",
    "cell therapy platform",
    "instrumentation",
    "customer training",
    "technical support",
}

ACADEMIC_KEYWORDS = {
    "faculty",
    "adjunct",
    "instructor",
    "curriculum",
    "teaching",
}


def select_resume_track(job_title: str, description: str) -> ResumeRouteResult:
    title = (job_title or "").lower()
    description_text = (description or "").lower()
    combined = f"{title} {description_text}"

    scores: dict[str, int] = {
        "MSL / Medical Affairs": 0,
        "FAS / Technical Applications": 0,
        "Biology Teaching / Academic": 0,
    }
    reasons: list[str] = []
    conflicting_signals: list[str] = []

    for keyword in MSL_KEYWORDS:
        if keyword in combined:
            scores["MSL / Medical Affairs"] += 2
            reasons.append(f"Matched MSL-related keyword: {keyword}")
    for keyword in FAS_KEYWORDS:
        if keyword in combined:
            scores["FAS / Technical Applications"] += 2
            reasons.append(f"Matched FAS-related keyword: {keyword}")
    for keyword in ACADEMIC_KEYWORDS:
        if keyword in combined:
            scores["Biology Teaching / Academic"] += 2
            reasons.append(f"Matched academic keyword: {keyword}")

    if "liaison" in title or "liaison" in description_text:
        scores["MSL / Medical Affairs"] += 2
        reasons.append("Role contains liaison language")
    if "scientist" in title or "applications" in title:
        scores["FAS / Technical Applications"] += 1
        reasons.append("Role title suggests technical applications work")
    if "teaching" in title or "faculty" in title or "instructor" in title:
        scores["Biology Teaching / Academic"] += 2
        reasons.append("Role title suggests academic/teaching")

    winner = max(scores, key=lambda track: scores[track])
    confidence = min(0.95, max(0.55, scores[winner] / 8.0))

    if scores["MSL / Medical Affairs"] and scores["FAS / Technical Applications"]:
        conflicting_signals.append("Mixed MSL/FAS signals detected")
    if scores["Biology Teaching / Academic"] and winner != "Biology Teaching / Academic":
        conflicting_signals.append("Academic signal conflicts with non-academic track")

    return ResumeRouteResult(
        selected_track=winner,
        confidence_score=round(confidence, 2),
        reasons=sorted(set(reasons)),
        conflicting_signals=sorted(set(conflicting_signals)),
        human_review_required=True,
    )


def select_track(job_title: str, description: str) -> ResumeRouteResult:
    return select_resume_track(job_title, description)


def build_tailoring_summary(
    job_title: str, description: str, selected_track: str | None = None
) -> dict[str, Any]:
    route = select_resume_track(job_title, description)
    return {
        "selected_track": selected_track or route.selected_track,
        "confidence_score": route.confidence_score,
        "reasons": route.reasons,
        "conflicting_signals": route.conflicting_signals,
        "human_review_required": route.human_review_required,
    }
