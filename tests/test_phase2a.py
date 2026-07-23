import hashlib
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from docx import Document

from jobbot.db import initialize_schema
from jobbot.jobs.analysis import analyze_job
from jobbot.jobs.intake import (
    fetch_rendered_posting,
    intake_file,
    intake_url,
    normalize_posting,
    save_posting,
)
from jobbot.profile.canonical import CanonicalFact, _insert_fact
from jobbot.resumes import tailoring
from jobbot.resumes.tailoring import (
    approved_resume_for_job,
    contains_placeholder,
    set_version_status,
    tailor_resume,
)
from jobbot.tailoring.routing import select_resume_track


POSTING = (
    """
Eisai
Medical Science Liaison, Oncology
Location: Houston, TX
Responsibilities:
Communicate complex scientific information and provide medical education.
Required Qualifications:
Ph.D. degree in biomedical sciences.
6 years of oncology research experience.
Preferred Qualifications:
CAR T, immunology, and flow cytometry knowledge.
Travel:
Up to 60% travel.
"""
    * 3
)


def database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    return connection


def add_verified(
    connection: sqlite3.Connection,
    category: str,
    field: str,
    value: str,
    *,
    active: bool = True,
) -> int:
    return _insert_fact(
        connection,
        CanonicalFact(
            category=category,
            field_name=field,
            canonical_value=value,
            display_value=value,
            verification_status="verified",
            source_documents=["base.docx"],
            active=active,
        ),
    )


def prepare_tailoring(connection: sqlite3.Connection, tmp_path: Path, monkeypatch) -> int:
    job_id = save_posting(
        connection,
        normalize_posting(
            POSTING,
            source_url="https://example.test/job?utm_source=email&id=2",
            method="text",
        ),
    )
    add_verified(connection, "identity", "name", "Verified Candidate")
    add_verified(
        connection,
        "education",
        "phd",
        "Ph.D., Biomedical Sciences | Verified University",
    )
    add_verified(connection, "experience_duration", "oncology", "6 years")
    add_verified(connection, "technical_skills", "car_t", "CAR T cells")
    add_verified(connection, "technical_skills", "immunology", "immunology")
    add_verified(
        connection,
        "publications",
        "alppl2",
        "ALPPL2 CAR T publication | 10.1158/2326-6066.CIR-25-0609 | published",
    )
    add_verified(
        connection,
        "publications",
        "old_alppl2_status",
        "accepted; epub ahead of print",
        active=False,
    )
    base = tmp_path / "base.docx"
    document = Document()
    document.add_paragraph("Original immutable base resume")
    document.save(str(base))
    monkeypatch.setitem(tailoring.BASE_DOCUMENTS, "MSL / Medical Affairs", base)
    monkeypatch.setattr(tailoring, "GENERATED_DIR", tmp_path / "generated")
    connection.commit()
    return job_id


def test_url_text_file_and_tracking_normalization(tmp_path: Path) -> None:
    with patch("jobbot.jobs.intake.fetch_job_url", return_value=POSTING):
        intake = intake_url("https://example.test/job?id=2&utm_source=email")
    assert intake.complete is True
    assert intake.normalized_url == "https://example.test/job?id=2"
    text_file = tmp_path / "posting.md"
    text_file.write_text(POSTING)
    assert intake_file(text_file).complete is True
    assert normalize_posting(POSTING, source_url=None, method="text").complete is True


def test_javascript_rendered_posting_and_incomplete_detection() -> None:
    fixture = Path("tests/fixtures/javascript_job_posting.html").resolve().as_uri()
    rendered = fetch_rendered_posting(fixture)
    assert "Medical Science Liaison" in rendered
    incomplete = normalize_posting(
        '<html><body><div id="root"></div><script>window.workday={}</script></body></html>',
        source_url="https://example.test/apply",
        method="https",
    )
    assert incomplete.complete is False
    assert any("shell" in warning.casefold() for warning in incomplete.warnings)


def test_routing_remains_track_specific() -> None:
    assert select_resume_track("Oncology MSL", "field medical").selected_track == (
        "MSL / Medical Affairs"
    )
    assert select_resume_track("Field Application Scientist", "flow cytometry").selected_track == (
        "FAS / Technical Applications"
    )
    assert select_resume_track("Biology Instructor", "curriculum teaching").selected_track == (
        "Biology Teaching / Academic"
    )


def test_analysis_links_verified_facts_and_does_not_infer_absence(tmp_path: Path) -> None:
    connection = database()
    job_id = save_posting(
        connection,
        normalize_posting(POSTING, source_url=None, method="text"),
    )
    fact_id = add_verified(connection, "education", "phd", "Ph.D., Biomedical Sciences")
    connection.commit()
    analysis = analyze_job(connection, job_id)
    assert analysis.selected_track == "MSL / Medical Affairs"
    assert any(fact_id in match.supporting_fact_ids for match in analysis.matches)
    assert all(
        match.status in {"verified_match", "unverified_or_not_found"} for match in analysis.matches
    )


def test_claim_provenance_versions_immutability_and_approval(tmp_path: Path, monkeypatch) -> None:
    connection = database()
    job_id = prepare_tailoring(connection, tmp_path, monkeypatch)
    source = tailoring.BASE_DOCUMENTS["MSL / Medical Affairs"]
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    first = tailor_resume(connection, job_id)
    first_hash = hashlib.sha256(Path(first.docx_path).read_bytes()).hexdigest()
    second = tailor_resume(connection, job_id)

    assert first.version == 1
    assert second.version == 2
    assert first.docx_path != second.docx_path
    assert hashlib.sha256(Path(first.docx_path).read_bytes()).hexdigest() == first_hash
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    assert all(claim.supporting_fact_ids for claim in first.claims)
    assert all(claim.validation_status == "valid" for claim in first.claims)
    text = Path(first.text_path).read_text()
    assert "accepted; epub ahead of print" not in text
    assert "10.1158/2326-6066.CIR-25-0609" in text
    assert approved_resume_for_job(connection, job_id) is None
    approved = set_version_status(connection, job_id, first.id, "approved")
    assert approved.status == "approved"
    assert approved_resume_for_job(connection, job_id).id == first.id


def test_unsupported_claim_and_placeholder_are_rejected() -> None:
    claim = tailoring.ResumeClaim(
        claim_text="Managed KOL relationships [insert metric]",
        claim_type="summary",
        supporting_fact_ids=[],
        source_provenance=[],
        transformation_type="terminology-aligned",
        confidence=0.2,
    )
    warnings = tailoring.validate_claims([claim])
    assert claim.validation_status == "rejected"
    assert warnings
    assert contains_placeholder("[insert URL]") is True


def test_incomplete_posting_blocks_tailoring(tmp_path: Path, monkeypatch) -> None:
    connection = database()
    job_id = save_posting(
        connection,
        normalize_posting("Short shell", source_url=None, method="text"),
    )
    with pytest.raises(ValueError, match="complete normalized posting"):
        tailor_resume(connection, job_id)
