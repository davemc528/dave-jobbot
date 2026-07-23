from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from jobbot.profile.canonical import CanonicalFact, list_canonical_facts
from jobbot.tailoring.routing import select_resume_track


class RequirementMatch(BaseModel):
    requirement: str
    status: str
    supporting_fact_ids: list[int] = Field(default_factory=list)
    explanation: str


class JobAnalysis(BaseModel):
    job_id: int
    overall_score: float
    breakdown: dict[str, float]
    matches: list[RequirementMatch]
    unverified_requirements: list[str]
    missing_requirements: list[str]
    disqualifiers: list[str]
    selected_track: str
    track_confidence: float
    route_reasons: list[str]
    conflicting_signals: list[str]
    emphasis_areas: list[str]
    human_questions: list[str]


def _terms(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9+#-]{3,}", text.casefold())
        if token not in {"with", "from", "that", "this", "years", "experience", "required"}
    }


def analyze_job(connection: sqlite3.Connection, job_id: int) -> JobAnalysis:
    row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown job id: {job_id}")
    posting = connection.execute("SELECT * FROM job_postings WHERE job_id=?", (job_id,)).fetchone()
    text = str(posting["normalized_text"] if posting else row["description"] or "")
    try:
        requirements = json.loads(row["required_qualifications"] or "[]")
    except json.JSONDecodeError:
        requirements = []
    if not requirements:
        requirements = [
            line.strip(" •*-")
            for line in text.splitlines()
            if re.search(r"\b(ph\.?d|degree|\d+\+?\s+years?|required|must have)\b", line, re.I)
        ][:20]
    facts = [
        fact
        for fact in list_canonical_facts(connection)
        if fact.active
        and fact.verification_status == "verified"
        and fact.category != "document_track"
    ]
    matches: list[RequirementMatch] = []
    for requirement in requirements:
        requirement_terms = _terms(str(requirement))
        restricted_experience = any(
            marker in str(requirement).casefold()
            for marker in (
                "minimum of 3 years msl",
                "clinical trial development",
                "drug launch",
                "fda regulations",
                "oig guidelines",
                "treatment guidelines",
                "key opinion leaders",
                "kol",
                "pharmaceutical knowledge",
            )
        )
        supporting: list[CanonicalFact] = []
        for fact in facts:
            fact_terms = _terms(f"{fact.field_name} {fact.display_value or ''}")
            overlap = requirement_terms & fact_terms
            if (
                requirement_terms
                and not restricted_experience
                and len(overlap) >= min(2, len(requirement_terms))
            ):
                supporting.append(fact)
        ids = [int(fact.id) for fact in supporting if fact.id is not None]
        matches.append(
            RequirementMatch(
                requirement=str(requirement),
                status="verified_match" if ids else "unverified_or_not_found",
                supporting_fact_ids=ids,
                explanation=(
                    "Matched verified canonical profile facts"
                    if ids
                    else "Not found in verified profile; this does not establish absence"
                ),
            )
        )
    verified_count = sum(match.status == "verified_match" for match in matches)
    coverage = verified_count / len(matches) if matches else 0.0
    route = select_resume_track(str(row["title"] or ""), text)
    lowered = text.casefold()
    scientific = min(
        1.0,
        sum(term in lowered for term in ("oncology", "immunology", "medical", "scientific")) / 3,
    )
    communication = min(
        1.0,
        sum(term in lowered for term in ("communication", "presentation", "education", "training"))
        / 3,
    )
    geographic = 1.0 if "texas" in lowered or "houston" in lowered else 0.5
    breakdown = {
        "mandatory_qualification_coverage": round(coverage * 35, 2),
        "scientific_technical_match": round(scientific * 25, 2),
        "communication_teaching_match": round(communication * 15, 2),
        "role_track_match": 15.0 if route.confidence_score >= 0.7 else 10.0,
        "geographic_travel_match": round(geographic * 10, 2),
    }
    overall = min(100.0, round(sum(breakdown.values()), 2))
    emphasis = [
        term
        for term in (
            "oncology",
            "CAR T",
            "immunology",
            "scientific communication",
            "teaching",
            "flow cytometry",
            "RNA-seq",
        )
        if term.casefold() in lowered
        and any(term.casefold() in (fact.display_value or "").casefold() for fact in facts)
    ]
    unverified = [
        match.requirement for match in matches if match.status == "unverified_or_not_found"
    ]
    analysis = JobAnalysis(
        job_id=job_id,
        overall_score=overall,
        breakdown=breakdown,
        matches=matches,
        unverified_requirements=unverified,
        missing_requirements=[],
        disqualifiers=[],
        selected_track=route.selected_track,
        track_confidence=route.confidence_score,
        route_reasons=route.reasons,
        conflicting_signals=route.conflicting_signals,
        emphasis_areas=emphasis,
        human_questions=[
            f"Can this requirement be verified from another source? {item}" for item in unverified
        ],
    )
    connection.execute("DELETE FROM job_analyses WHERE job_id=?", (job_id,))
    connection.execute(
        """
        INSERT INTO job_analyses
          (job_id, overall_score, breakdown, matched_requirements, unverified_requirements,
           missing_requirements, disqualifiers, selected_track, track_confidence,
           emphasis_areas, human_questions, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            overall,
            json.dumps(breakdown),
            json.dumps([match.model_dump() for match in matches]),
            json.dumps(unverified),
            "[]",
            "[]",
            route.selected_track,
            route.confidence_score,
            json.dumps(emphasis),
            json.dumps(analysis.human_questions),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    connection.commit()
    return analysis
