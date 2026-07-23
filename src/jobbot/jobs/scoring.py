from __future__ import annotations

import re
from dataclasses import dataclass

from jobbot.models import JobPost, JobScore


@dataclass(slots=True)
class ScoreBreakdown:
    scientific_technical: float = 0.0
    seniority: float = 0.0
    transferable: float = 0.0
    communication: float = 0.0
    industry_feasibility: float = 0.0
    geographic: float = 0.0
    degree_match: float = 0.0
    missing_qualifications: float = 0.0
    dealbreakers: float = 0.0


def score_job(
    job: JobPost | str | None = None,
    *,
    title: str | None = None,
    description: str | None = None,
    location: str | None = None,
) -> JobScore:
    if isinstance(job, JobPost):
        job_obj = job
    elif isinstance(job, str):
        job_obj = JobPost(
            source="manual", title=job, description=description or "", location=location
        )
    else:
        job_obj = JobPost(
            source="manual", title=title, description=description or "", location=location
        )

    description_text = (job_obj.description or "").lower()
    title = (job_obj.title or "").lower()
    full_text = f"{title} {description_text}"

    weights = {
        "scientific_technical": 25,
        "seniority": 10,
        "transferable": 15,
        "communication": 10,
        "industry_feasibility": 10,
        "geographic": 10,
        "degree_match": 10,
        "missing_qualifications": 5,
        "dealbreakers": 5,
    }

    scientific_technical = 0.0
    if any(
        token in full_text for token in ["medical", "scientific", "clinical", "oncology", "biology"]
    ):
        scientific_technical += 0.8
    if any(token in full_text for token in ["flow cytometry", "instrumentation", "cell therapy"]):
        scientific_technical += 0.8

    seniority = (
        0.5
        if any(token in full_text for token in ["senior", "lead", "principal", "manager"])
        else 0.3
    )
    transferable = (
        0.7
        if any(token in full_text for token in ["liaison", "customer", "training", "applications"])
        else 0.4
    )
    communication = (
        0.6
        if any(
            token in full_text
            for token in ["communication", "presentation", "training", "scientific"]
        )
        else 0.3
    )
    industry_feasibility = (
        0.6
        if any(token in full_text for token in ["medical", "science", "biotech", "academic"])
        else 0.4
    )
    geographic = (
        0.9
        if job_obj.location
        and any(
            token in job_obj.location.lower()
            for token in ["san diego", "bay area", "cambridge", "boston", "remote"]
        )
        else 0.4
    )
    degree_match = (
        0.8
        if any(token in full_text for token in ["phd", "masters", "bachelors", "degree"])
        else 0.4
    )
    missing_qualifications = 1.0 if "phd" in full_text or "experience" in full_text else 0.8
    dealbreakers = (
        0.8
        if any(
            token in full_text
            for token in ["visa", "citizenship", "relocation", "security clearance"]
        )
        else 0.5
    )

    breakdown = {
        "scientific_technical": round(scientific_technical * weights["scientific_technical"], 2),
        "seniority": round(seniority * weights["seniority"], 2),
        "transferable": round(transferable * weights["transferable"], 2),
        "communication": round(communication * weights["communication"], 2),
        "industry_feasibility": round(industry_feasibility * weights["industry_feasibility"], 2),
        "geographic": round(geographic * weights["geographic"], 2),
        "degree_match": round(degree_match * weights["degree_match"], 2),
        "missing_qualifications": round(
            missing_qualifications * weights["missing_qualifications"], 2
        ),
        "dealbreakers": round(dealbreakers * weights["dealbreakers"], 2),
    }

    overall = round(sum(breakdown.values()), 2)
    reasons = [
        f"Scientific/technical signal: {scientific_technical:.2f}",
        f"Transferable experience signal: {transferable:.2f}",
        f"Geographic match: {geographic:.2f}",
    ]
    conflicts: list[str] = []
    if "remote" in (job_obj.location or "").lower() and "onsite" in full_text:
        conflicts.append("Location conflicts with on-site requirement")

    return JobScore(
        overall_score=overall,
        breakdown=breakdown,
        reasons=reasons,
        conflicts=conflicts,
        selected_track="MSL / Medical Affairs"
        if scientific_technical >= 0.65
        else "FAS / Technical Applications",
        confidence=round(min(0.95, overall / 100), 2),
        human_review_required=overall < 70,
    )


def normalize_job(raw_text: str, source: str = "manual") -> JobPost:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    title = lines[0] if lines else "Unknown role"
    description = "\n".join(lines[1:])
    location_match = re.search(r"Location:?\s*(.+)", description, flags=re.IGNORECASE)
    location = location_match.group(1).strip() if location_match else None
    salary_match = re.search(r"\$\s?\d[\d,]*(?:\s*[-–]\s*\$?\d[\d,]*)?", description)
    salary = salary_match.group(0) if salary_match else None

    return JobPost(
        source=source,
        company="Unknown",
        title=title,
        location=location,
        remote_status="unknown",
        salary=salary,
        url=None,
        ats_type="unknown",
        description=description,
        required_qualifications=[],
        preferred_qualifications=[],
        responsibilities=[],
        travel_requirement=None,
    )
