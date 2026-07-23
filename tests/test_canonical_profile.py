import sqlite3
from pathlib import Path

import pytest

from jobbot.browser.playwright_mvp import verified_mock_autofill
from jobbot.db import initialize_schema
from jobbot.llm.provider import OpenAICompatibleProvider
from jobbot.profile.canonical import (
    CanonicalFact,
    _insert_fact,
    apply_canonical_proposals,
    approve_and_supersede,
    batch_approve,
    get_canonical_fact,
    list_canonical_facts,
    mark_sensitive_manual_only,
    merge_facts,
    profile_readiness,
    propose_canonical_groups,
    save_intake_answer,
    seed_phase_15_proposals,
    split_fact,
    update_fact,
)


def database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    return connection


def add_raw(
    connection: sqlite3.Connection,
    category: str,
    field: str,
    value: str,
    source: str,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO candidate_facts
          (category, field_name, value, verified, source, notes)
        VALUES (?, ?, ?, 0, ?, 'extracted')
        """,
        (category, field, value, source),
    )
    return int(cursor.lastrowid)


def test_deduplication_retains_provenance_and_requires_explicit_verification() -> None:
    connection = database()
    add_raw(connection, "education", "phd", "Ph.D., Biomedical Sciences", "academic.pdf")
    add_raw(connection, "education", "phd", "Ph.D., Biomedical Sciences", "msl.docx")
    proposals = propose_canonical_groups(connection)
    assert len(proposals) == 1
    assert proposals[0].source_documents == ["academic.pdf", "msl.docx"]
    ids = apply_canonical_proposals(connection)
    fact = get_canonical_fact(connection, ids[0])
    assert fact.verification_status == "unverified"
    sources = connection.execute(
        "SELECT count(*) FROM canonical_fact_sources WHERE canonical_fact_id = ?", (ids[0],)
    ).fetchone()[0]
    assert sources == 2
    approved = update_fact(connection, ids[0], action="approve", autofill_permission=True)
    assert approved.verification_status == "verified"
    assert approved.date_verified
    assert approved.autofill_permission is True


def test_merge_split_and_audit_logging() -> None:
    connection = database()
    first_raw = add_raw(connection, "technical_skills", "flow", "Flow cytometry", "a.pdf")
    add_raw(connection, "technical_skills", "flow", "flow cytometry", "b.docx")
    ids = apply_canonical_proposals(connection)
    assert len(ids) == 1
    split_id = split_fact(connection, ids[0], [first_raw])
    merged_id = merge_facts(connection, [ids[0], split_id])
    assert merged_id == ids[0]
    actions = {row["action"] for row in connection.execute("SELECT action FROM profile_audit_log")}
    assert {"merge_applied", "split", "merge"} <= actions


def test_duration_publication_superseding_and_restricted_patent() -> None:
    connection = database()
    add_raw(connection, "experience_claims", "seven_plus_years", "7+ years", "a.pdf")
    add_raw(
        connection,
        "publications",
        "alppl2_car_t_publication_status",
        "accepted",
        "a.pdf",
    )
    add_raw(
        connection,
        "patents_and_intellectual_property",
        "application",
        "Confidential internal patent reference",
        "a.docx",
    )
    apply_canonical_proposals(connection)
    proposal_ids = seed_phase_15_proposals(connection)
    duration = next(
        fact
        for fact in list_canonical_facts(connection)
        if fact.field_name == "postdoctoral_oncology_research_duration"
    )
    assert duration.canonical_value == "6 years"
    approved = approve_and_supersede(connection, int(duration.id))
    assert approved.verification_status == "verified"
    assert approved.supersedes
    patent_facts = [
        fact
        for fact in list_canonical_facts(connection)
        if fact.category == "patents_and_intellectual_property"
    ]
    assert proposal_ids
    assert all(fact.verification_status == "restricted" for fact in patent_facts)
    assert all(fact.autofill_permission is False for fact in patent_facts)
    with pytest.raises(ValueError):
        batch_approve(connection, [int(patent_facts[0].id)])


def test_sensitive_intake_is_not_stored_and_non_inference_is_enforced() -> None:
    connection = database()
    for field in (
        "work_authorization",
        "sponsorship_requirement",
        "minimum_compensation",
        "eeo_answers",
    ):
        with pytest.raises(RuntimeError):
            save_intake_answer(
                connection,
                field,
                "inferred value",
                explicitly_verified=True,
                autofill_permission=True,
            )
    assert connection.execute("SELECT count(*) FROM profile_intake").fetchone()[0] == 0
    mark_sensitive_manual_only(connection, "work_authorization")
    row = connection.execute(
        "SELECT value, manual_only FROM profile_intake WHERE field_name='work_authorization'"
    ).fetchone()
    assert row["value"] is None
    assert row["manual_only"] == 1
    mark_sensitive_manual_only(connection, "eeo_answers")
    eeo = connection.execute(
        "SELECT value, manual_only, autofill_permission FROM profile_intake "
        "WHERE field_name='eeo_answers'"
    ).fetchone()
    assert eeo["value"] is None
    assert eeo["manual_only"] == 1
    assert eeo["autofill_permission"] == 0


def test_external_llm_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBBOT_ALLOW_REMOTE_LLM", raising=False)
    provider = OpenAICompatibleProvider(api_key="not-a-real-key")
    with pytest.raises(RuntimeError, match="Remote LLM use is disabled"):
        provider.generate("Do not send this")


def test_phase_15_migration_upgrades_existing_database() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE candidate_facts "
        "(id INTEGER PRIMARY KEY, category TEXT, field_name TEXT, value TEXT, "
        "verified INTEGER, source TEXT, notes TEXT)"
    )
    initialize_schema(connection)
    version = connection.execute(
        "SELECT version FROM schema_migrations WHERE version='001_phase_1_5_profile_verification'"
    ).fetchone()
    assert version is not None
    for table in (
        "canonical_facts",
        "canonical_fact_sources",
        "profile_audit_log",
        "profile_intake",
    ):
        found = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        assert found is not None


def add_verified_fact(connection: sqlite3.Connection, category: str, field: str, value: str) -> int:
    fact_id = _insert_fact(
        connection,
        CanonicalFact(category=category, field_name=field, canonical_value=value),
    )
    update_fact(connection, fact_id, action="approve")
    return fact_id


def test_readiness_failures_and_success_after_required_approvals() -> None:
    connection = database()
    assert profile_readiness(connection).ready is False
    add_verified_fact(connection, "identity", "name", "Candidate Name")
    add_verified_fact(connection, "contact_information", "email", "verified@example.test")
    add_verified_fact(connection, "contact_information", "phone", "555-555-0100")
    add_verified_fact(connection, "employment_history", "current_role", "Verified role")
    add_verified_fact(connection, "education", "highest_degree", "Ph.D.")
    add_verified_fact(connection, "document_track", "candidate_track", "MSL")
    save_intake_answer(
        connection,
        "current_city_state",
        "Houston, TX",
        explicitly_verified=True,
    )
    mark_sensitive_manual_only(connection, "work_authorization")
    mark_sensitive_manual_only(connection, "sponsorship_requirement")
    assert profile_readiness(connection).ready is True


def test_verified_only_mock_autofill_and_submit_stop() -> None:
    fixture = Path("tests/fixtures/oncology_msl_application.html").resolve().as_uri()
    facts = {
        "first_name": {
            "value": "Verified",
            "verification_status": "verified",
            "autofill_permission": True,
            "sensitivity": "ordinary",
        },
        "email": {
            "value": "withheld@example.test",
            "verification_status": "unverified",
            "autofill_permission": True,
            "sensitivity": "ordinary",
        },
        "work_authorization": {
            "value": "Yes",
            "verification_status": "verified",
            "autofill_permission": True,
            "sensitivity": "sensitive",
        },
        "patent_summary": {
            "value": "Restricted patent text",
            "verification_status": "restricted",
            "autofill_permission": False,
            "sensitivity": "restricted",
        },
        "qualifications": {
            "value": "Verified oncology and teaching facts only.",
            "verification_status": "verified",
            "autofill_permission": True,
            "sensitivity": "ordinary",
        },
    }
    result = verified_mock_autofill(fixture, facts, visible=False)
    assert result["filled"]["first_name"] == "Verified"
    assert result["filled"]["work_authorization"] == "Yes"
    assert result["filled"]["qualifications"] == "Verified oncology and teaching facts only."
    assert "email" in result["withheld"]
    assert "patent_summary" in result["withheld"]
    assert "LinkedIn URL requires review" in result["review_items"]
    assert result["stopped_before_submit"] is True
