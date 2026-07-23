from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from jobbot.models import CandidateFact

ACADEMIC_FILENAME = "Dave Cunningham.CV.pdf"
MSL_FILENAME = "David_Cunningham_MSL_Resume.docx"
FAS_FILENAME = "David_Cunningham_FAS_Resume.docx"
EXPECTED_DOCUMENTS = (ACADEMIC_FILENAME, MSL_FILENAME, FAS_FILENAME)

TRACKS = {
    ACADEMIC_FILENAME: "Biology Teaching / Academic",
    MSL_FILENAME: "MSL / Medical Affairs",
    FAS_FILENAME: "FAS / Technical Applications",
}

SKILL_TERMS = (
    "CAR T cells",
    "cell culture",
    "flow cytometry",
    "FlowJo",
    "RNA-seq",
    "bioinformatics",
    "Linux",
    "Python",
    "R",
    "molecular biology",
    "in vivo modeling",
    "functional immune assays",
    "data visualization",
)


@dataclass(slots=True)
class DocumentAnalysis:
    filename: str
    track: str
    facts: list[CandidateFact]
    ambiguous_items: list[str]


def assign_document_track(filename: str) -> str:
    try:
        return TRACKS[Path(filename).name]
    except KeyError as exc:
        raise ValueError(f"Unsupported source document: {filename}") from exc


def _fact(category: str, field_name: str, value: str, source: str) -> CandidateFact:
    return CandidateFact(
        category=category,
        field_name=field_name,
        value=value.strip(),
        verified=False,
        source=source,
        notes="Deterministically extracted; human confirmation required",
    )


def extract_candidate_facts(filename: str, text: str) -> DocumentAnalysis:
    """Extract conservative candidates; presence in a resume is not verification."""
    source = Path(filename).name
    track = assign_document_track(source)
    flattened = re.sub(r"\s+", " ", text)
    facts = [_fact("document_track", "candidate_track", track, source)]

    name_match = re.search(r"\bDAVID CUNNINGHAM(?:,\s*PhD)?\b", flattened, re.IGNORECASE)
    if name_match:
        facts.append(_fact("identity", "name", "David Cunningham", source))

    if re.search(r"\bHouston,\s*TX\b", flattened, re.IGNORECASE):
        facts.append(_fact("geography", "current_location", "Houston, TX", source))

    education_patterns = (
        (
            "phd",
            r"Ph\.?D\.?,?\s*Biomedical Sciences.{0,3}(?:\||at)?\s*Tulane University School of Medicine",
        ),
        (
            "ms",
            r"M\.?S\.?,?\s*Medical Science.{0,3}(?:\||at)?\s*Drexel University College of Medicine",
        ),
        ("bs", r"B\.?S\.?,?\s*Biology.{0,3}(?:\||at)?\s*Marist College"),
    )
    for field_name, pattern in education_patterns:
        match = re.search(pattern, flattened, re.IGNORECASE)
        if match:
            facts.append(_fact("education", field_name, match.group(0), source))
    education_fallbacks = (
        ("phd", "Ph.D., Biomedical Sciences | Tulane University School of Medicine"),
        ("ms", "M.S., Medical Science | Drexel University College of Medicine"),
        ("bs", "B.S., Biology | Marist College"),
    )
    existing_education = {fact.field_name for fact in facts if fact.category == "education"}
    for field_name, value in education_fallbacks:
        degree, institution = value.split(" | ")
        if (
            field_name not in existing_education
            and degree in flattened
            and institution in flattened
        ):
            facts.append(_fact("education", field_name, value, source))

    roles = (
        (
            "baylor_postdoc_translational",
            r"Postdoctoral Fellow,?\s*Translational Immuno-Oncology.{0,100}?May 2020\s*[-–]\s*Oct\.?\s*2024",
        ),
        (
            "baylor_postdoc_hematology",
            r"Postdoctoral Fellow,?\s*Hematology/Oncology.{0,100}?Oct\.?\s*2018\s*[-–]\s*May 2020",
        ),
        (
            "tulane_graduate_research",
            r"Graduate Research Assistant,?\s*Structural and Cellular Biology.{0,120}?Aug\.?\s*2013\s*[-–]\s*May 2018",
        ),
        (
            "san_jacinto_adjunct",
            r"(?:Adjunct Professor,?\s*Department of Natural Sciences.{0,100}?)?"
            r"San Jacinto Community College.{0,100}?Aug\.?\s*2025\s*[-–]\s*Present",
        ),
    )
    for field_name, pattern in roles:
        match = re.search(pattern, flattened, re.IGNORECASE)
        if match:
            facts.append(_fact("employment_history", field_name, match.group(0), source))

    experience_match = re.search(r"\b7\+\s+years(?:\s+of)?\s+([^.,;]+)", flattened, re.IGNORECASE)
    if experience_match:
        facts.append(
            _fact("experience_claims", "seven_plus_years", experience_match.group(0), source)
        )

    for skill in SKILL_TERMS:
        if re.search(rf"\b{re.escape(skill)}\b", flattened, re.IGNORECASE):
            facts.append(_fact("technical_skills", skill.lower(), skill, source))

    doi_match = re.search(r"10\.1038/s41586-025-09962-4", flattened)
    if doi_match:
        facts.append(_fact("publications", "nature_ai_benchmark_doi", doi_match.group(0), source))
    if "Effector differentiation with sustained proliferative capacity" in flattened:
        status = (
            "accepted; 2026 epub ahead of print" if "ACCEPTED" in flattened else "2026 citation"
        )
        facts.append(_fact("publications", "alppl2_car_t_publication_status", status, source))

    patent_match = re.search(r"U\.S\. Provisional Patent Application No\. 63/781,787", flattened)
    if patent_match:
        facts.append(
            _fact(
                "patents_and_intellectual_property",
                "alppl2_car_provisional_application",
                patent_match.group(0),
                source,
            )
        )

    ambiguous: list[str] = []
    if "[insert URL]" in text:
        ambiguous.append("LinkedIn URL is a placeholder and must not be autofilled")
    if "7+" in text:
        ambiguous.append("The 7+ years claim requires human confirmation of its calculation")
    if source == ACADEMIC_FILENAME and len(text.splitlines()) < 10:
        ambiguous.append("PDF layout collapsed into few lines; section boundaries require review")

    return DocumentAnalysis(source, track, facts, ambiguous)


def compare_analyses(
    analyses: list[DocumentAnalysis],
) -> tuple[list[str], list[str], list[str]]:
    grouped: dict[tuple[str, str], list[CandidateFact]] = {}
    for analysis in analyses:
        for fact in analysis.facts:
            grouped.setdefault((fact.category, fact.field_name), []).append(fact)

    duplicates: list[str] = []
    conflicts: list[str] = []
    for (category, field_name), facts in sorted(grouped.items()):
        if category == "document_track":
            continue
        if len(facts) < 2:
            continue
        values = {re.sub(r"\s+", " ", fact.value or "").casefold() for fact in facts}
        sources = ", ".join(sorted(fact.source or "" for fact in facts))
        label = f"{category}.{field_name}"
        if len(values) == 1:
            duplicates.append(f"{label} agrees across: {sources}")
        else:
            conflicts.append(f"{label} differs across: {sources}")

    ambiguous = [
        f"{analysis.filename}: {item}" for analysis in analyses for item in analysis.ambiguous_items
    ]
    return duplicates, conflicts, ambiguous
