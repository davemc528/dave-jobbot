from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

VerificationStatus = Literal[
    "unverified",
    "verified",
    "rejected",
    "needs_edit",
    "conflicted",
    "derived",
    "restricted",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CanonicalFact(BaseModel):
    id: int | None = None
    category: str
    field_name: str
    canonical_value: str | None = None
    display_value: str | None = None
    normalized_value: str | None = None
    source_document: str | None = None
    source_documents: list[str] = Field(default_factory=list)
    source_excerpt: str | None = None
    verification_status: VerificationStatus = "unverified"
    verification_method: str | None = None
    sensitivity: str = "ordinary"
    autofill_permission: bool = False
    applicable_tracks: list[str] = Field(default_factory=list)
    date_verified: str | None = None
    review_notes: str | None = None
    conflicting_values: list[str] = Field(default_factory=list)
    derived_from: list[int] = Field(default_factory=list)
    supersedes: list[int] = Field(default_factory=list)
    active: bool = True
    superseded_by: int | None = None


class ReadinessReport(BaseModel):
    ready: bool
    failures: list[str] = Field(default_factory=list)


JSON_FIELDS = {
    "source_documents",
    "applicable_tracks",
    "conflicting_values",
    "derived_from",
    "supersedes",
}

STATUS_VALUES = {
    "unverified",
    "verified",
    "rejected",
    "needs_edit",
    "conflicted",
    "derived",
    "restricted",
}

NEVER_AUTO_MERGE = {
    ("experience_claims", "seven_plus_years"),
    ("publications", "alppl2_car_t_publication_status"),
}

RESTRICTED_CATEGORIES = {"patents_and_intellectual_property"}

TRACK_MAP = {
    "Dave Cunningham.CV.pdf": ["Biology Teaching / Academic"],
    "David_Cunningham_MSL_Resume.docx": ["MSL / Medical Affairs"],
    "David_Cunningham_FAS_Resume.docx": ["FAS / Technical Applications"],
}


def normalize_value(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def row_to_fact(row: sqlite3.Row) -> CanonicalFact:
    values = dict(row)
    for field in JSON_FIELDS:
        values[field] = json.loads(values.get(field) or "[]")
    values["autofill_permission"] = bool(values["autofill_permission"])
    values["active"] = bool(values["active"])
    for key in ("created_at", "updated_at"):
        values.pop(key, None)
    return CanonicalFact.model_validate(values)


def _insert_fact(connection: sqlite3.Connection, fact: CanonicalFact) -> int:
    now = utc_now()
    cursor = connection.execute(
        """
        INSERT INTO canonical_facts (
          category, field_name, canonical_value, display_value, normalized_value,
          source_document, source_documents, source_excerpt, verification_status,
          verification_method, sensitivity, autofill_permission, applicable_tracks,
          date_verified, review_notes, conflicting_values, derived_from, supersedes,
          active, superseded_by, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fact.category,
            fact.field_name,
            fact.canonical_value,
            fact.display_value,
            fact.normalized_value or normalize_value(fact.canonical_value),
            fact.source_document,
            json.dumps(fact.source_documents),
            fact.source_excerpt,
            fact.verification_status,
            fact.verification_method,
            fact.sensitivity,
            int(fact.autofill_permission),
            json.dumps(fact.applicable_tracks),
            fact.date_verified,
            fact.review_notes,
            json.dumps(fact.conflicting_values),
            json.dumps(fact.derived_from),
            json.dumps(fact.supersedes),
            int(fact.active),
            fact.superseded_by,
            now,
            now,
        ),
    )
    if cursor.lastrowid is None:
        raise RuntimeError("SQLite did not return a canonical fact id")
    return int(cursor.lastrowid)


def _audit(
    connection: sqlite3.Connection,
    fact_id: int | None,
    action: str,
    before: object = None,
    after: object = None,
    notes: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO profile_audit_log
          (canonical_fact_id, action, actor, before_json, after_json, notes, created_at)
        VALUES (?, ?, 'human', ?, ?, ?, ?)
        """,
        (
            fact_id,
            action,
            json.dumps(before, default=str) if before is not None else None,
            json.dumps(after, default=str) if after is not None else None,
            notes,
            utc_now(),
        ),
    )


def propose_canonical_groups(connection: sqlite3.Connection) -> list[CanonicalFact]:
    """Show deterministic merge proposals without changing persistence."""
    rows = connection.execute(
        "SELECT * FROM candidate_facts ORDER BY category, field_name, id"
    ).fetchall()
    grouped: dict[tuple[str, str, str], list[sqlite3.Row]] = {}
    for row in rows:
        category, field_name = row["category"], row["field_name"]
        discriminator = (
            str(row["id"])
            if (category, field_name) in NEVER_AUTO_MERGE
            else normalize_value(row["value"])
        )
        grouped.setdefault((category, field_name, discriminator), []).append(row)

    proposals: list[CanonicalFact] = []
    for (category, field_name, _), sources in grouped.items():
        values = [str(row["value"] or "") for row in sources]
        documents = sorted({str(row["source"]) for row in sources})
        restricted = category in RESTRICTED_CATEGORIES
        proposals.append(
            CanonicalFact(
                category=category,
                field_name=field_name,
                canonical_value=values[0],
                display_value=values[0],
                normalized_value=normalize_value(values[0]),
                source_document=documents[0],
                source_documents=documents,
                source_excerpt=values[0],
                verification_status="restricted" if restricted else "unverified",
                sensitivity="restricted" if restricted else "ordinary",
                autofill_permission=False,
                applicable_tracks=sorted(
                    {track for document in documents for track in TRACK_MAP.get(document, [])}
                ),
                conflicting_values=sorted(set(values[1:])),
                review_notes="Proposed deterministic duplicate group; requires human action",
            )
        )
    return proposals


def apply_canonical_proposals(connection: sqlite3.Connection) -> list[int]:
    if connection.execute("SELECT count(*) FROM canonical_facts").fetchone()[0]:
        return [row[0] for row in connection.execute("SELECT id FROM canonical_facts")]
    proposals = propose_canonical_groups(connection)
    raw_rows = connection.execute(
        "SELECT * FROM candidate_facts ORDER BY category, field_name, id"
    ).fetchall()
    assigned_raw_ids: set[int] = set()
    ids: list[int] = []
    for fact in proposals:
        fact_id = _insert_fact(connection, fact)
        ids.append(fact_id)
        for row in raw_rows:
            same_field = row["category"] == fact.category and row["field_name"] == fact.field_name
            same_value = normalize_value(row["value"]) == fact.normalized_value
            raw_id = int(row["id"])
            unique_only = (fact.category, fact.field_name) in NEVER_AUTO_MERGE
            if same_field and same_value and raw_id not in assigned_raw_ids:
                confidential = str(row["value"]) if fact.sensitivity == "restricted" else None
                public_excerpt = None if confidential else str(row["value"])
                connection.execute(
                    """
                    INSERT INTO canonical_fact_sources
                      (canonical_fact_id, raw_fact_id, source_document, source_excerpt,
                       confidential_excerpt)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (fact_id, row["id"], row["source"], public_excerpt, confidential),
                )
                assigned_raw_ids.add(raw_id)
                if unique_only:
                    break
        _audit(connection, fact_id, "merge_applied", after=fact.model_dump())
    connection.commit()
    return ids


def list_canonical_facts(connection: sqlite3.Connection) -> list[CanonicalFact]:
    return [
        row_to_fact(row)
        for row in connection.execute("SELECT * FROM canonical_facts ORDER BY category, field_name")
    ]


class SupersessionResult(BaseModel):
    verified_record_id: int
    changed_record_ids: list[int] = Field(default_factory=list)


ALPPL2_DOI = "10.1158/2326-6066.cir-25-0609"
ALPPL2_TITLE = (
    "coexpression of il15 promotes effector differentiation and sustained "
    "proliferative capacity in alppl2-specific human car t cells"
)
ALPPL2_SUPERSESSION_NOTE = (
    "Outdated publication-status reference superseded by verified published DOI record."
)


def supersede_outdated_alppl2_publications(
    connection: sqlite3.Connection,
) -> SupersessionResult:
    """Deactivate only source-derived ALPPL2 status claims superseded by the verified DOI."""
    publications = [
        fact for fact in list_canonical_facts(connection) if fact.category == "publications"
    ]
    verified = [
        fact
        for fact in publications
        if fact.active
        and fact.verification_status == "verified"
        and ALPPL2_DOI in normalize_value(fact.canonical_value)
        and ALPPL2_TITLE in normalize_value(fact.canonical_value)
    ]
    if len(verified) != 1 or verified[0].id is None:
        raise ValueError("Expected exactly one active verified ALPPL2 DOI publication record")
    target = verified[0]
    target_id = target.id
    assert target_id is not None
    outdated = [
        fact
        for fact in publications
        if fact.id != target.id
        and fact.active
        and fact.source_document is not None
        and fact.field_name == "alppl2_car_t_publication_status"
        and fact.verification_status in {"unverified", "conflicted", "needs_edit"}
    ]
    changed: list[int] = []
    for fact in outdated:
        if fact.id is None:
            continue
        before = fact.model_dump()
        connection.execute(
            """
            UPDATE canonical_facts
            SET active=0, autofill_permission=0, superseded_by=?, updated_at=?
            WHERE id=? AND active=1
            """,
            (target_id, utc_now(), fact.id),
        )
        _audit(
            connection,
            fact.id,
            "publication_status_superseded",
            before,
            {
                "active": False,
                "autofill_permission": False,
                "superseded_by": target_id,
            },
            ALPPL2_SUPERSESSION_NOTE,
        )
        changed.append(fact.id)
    connection.commit()
    return SupersessionResult(
        verified_record_id=target_id,
        changed_record_ids=changed,
    )


def get_canonical_fact(connection: sqlite3.Connection, fact_id: int) -> CanonicalFact:
    row = connection.execute("SELECT * FROM canonical_facts WHERE id = ?", (fact_id,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown canonical fact: {fact_id}")
    return row_to_fact(row)


def update_fact(
    connection: sqlite3.Connection,
    fact_id: int,
    *,
    action: Literal["approve", "edit", "reject", "defer"],
    value: str | None = None,
    autofill_permission: bool | None = None,
    notes: str | None = None,
) -> CanonicalFact:
    before = get_canonical_fact(connection, fact_id)
    status: VerificationStatus
    if action == "approve":
        status = "verified"
    elif action == "reject":
        status = "rejected"
    elif action == "edit":
        if value is None or not value.strip():
            raise ValueError("An explicit edited value is required")
        status = "verified"
    else:
        status = "needs_edit"

    if before.sensitivity == "restricted":
        status = "restricted"
        autofill_permission = False
    canonical_value = value.strip() if value is not None else before.canonical_value
    permission = (
        before.autofill_permission if autofill_permission is None else bool(autofill_permission)
    )
    if status not in {"verified"} or before.sensitivity == "restricted":
        permission = False
    verified_at = utc_now() if status == "verified" else None
    connection.execute(
        """
        UPDATE canonical_facts SET
          canonical_value = ?, display_value = ?, normalized_value = ?,
          verification_status = ?, verification_method = ?,
          autofill_permission = ?, date_verified = ?, review_notes = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            canonical_value,
            canonical_value,
            normalize_value(canonical_value),
            status,
            "explicit_human_action",
            int(permission),
            verified_at,
            notes,
            utc_now(),
            fact_id,
        ),
    )
    after = get_canonical_fact(connection, fact_id)
    _audit(connection, fact_id, action, before.model_dump(), after.model_dump(), notes)
    if status == "verified":
        for old_id in after.supersedes:
            old = get_canonical_fact(connection, old_id)
            connection.execute(
                """
                UPDATE canonical_facts SET review_notes = ?, autofill_permission = 0,
                  active = 0, superseded_by = ?, updated_at = ? WHERE id = ?
                """,
                (f"Superseded by canonical fact {fact_id}", fact_id, utc_now(), old_id),
            )
            _audit(
                connection,
                old_id,
                "superseded",
                old.model_dump(),
                {"superseded_by": fact_id},
            )
    connection.commit()
    return after


def batch_approve(
    connection: sqlite3.Connection, fact_ids: list[int], *, notes: str | None = None
) -> list[CanonicalFact]:
    facts = [get_canonical_fact(connection, fact_id) for fact_id in fact_ids]
    prohibited = [
        fact.id
        for fact in facts
        if fact.sensitivity != "ordinary"
        or fact.verification_status in {"restricted", "conflicted"}
    ]
    if prohibited:
        raise ValueError(f"Batch approval is prohibited for facts: {prohibited}")
    return [update_fact(connection, fact_id, action="approve", notes=notes) for fact_id in fact_ids]


def split_fact(connection: sqlite3.Connection, fact_id: int, raw_fact_ids: list[int]) -> int:
    before = get_canonical_fact(connection, fact_id)
    if not raw_fact_ids:
        raise ValueError("Select at least one source to split")
    placeholders = ",".join("?" for _ in raw_fact_ids)
    source_rows = connection.execute(
        f"""
        SELECT * FROM canonical_fact_sources
        WHERE canonical_fact_id = ? AND raw_fact_id IN ({placeholders})
        """,  # noqa: S608 - placeholders are generated, values remain bound
        (fact_id, *raw_fact_ids),
    ).fetchall()
    if len(source_rows) != len(raw_fact_ids):
        raise ValueError("A selected source does not belong to this canonical fact")
    raw = connection.execute(
        "SELECT * FROM candidate_facts WHERE id = ?", (raw_fact_ids[0],)
    ).fetchone()
    new_fact = before.model_copy(
        update={
            "id": None,
            "canonical_value": raw["value"],
            "display_value": raw["value"],
            "normalized_value": normalize_value(raw["value"]),
            "source_document": raw["source"],
            "source_documents": sorted({str(row["source_document"]) for row in source_rows}),
            "source_excerpt": raw["value"],
            "verification_status": "unverified",
            "verification_method": None,
            "autofill_permission": False,
            "date_verified": None,
            "review_notes": "Split from an incorrectly merged canonical group",
        }
    )
    new_id = _insert_fact(connection, new_fact)
    connection.execute(
        f"""
        UPDATE canonical_fact_sources SET canonical_fact_id = ?
        WHERE canonical_fact_id = ? AND raw_fact_id IN ({placeholders})
        """,  # noqa: S608
        (new_id, fact_id, *raw_fact_ids),
    )
    _audit(connection, fact_id, "split", before.model_dump(), {"new_fact_id": new_id})
    connection.commit()
    return new_id


def merge_facts(connection: sqlite3.Connection, fact_ids: list[int]) -> int:
    if len(fact_ids) < 2:
        raise ValueError("Select at least two canonical facts to merge")
    facts = [get_canonical_fact(connection, fact_id) for fact_id in fact_ids]
    if len({(fact.category, fact.field_name) for fact in facts}) != 1:
        raise ValueError("Only facts with the same category and field may be merged")
    if (facts[0].category, facts[0].field_name) in NEVER_AUTO_MERGE:
        raise ValueError("This field requires distinct records and cannot be merged")
    target = facts[0]
    if target.id is None:
        raise ValueError("Cannot merge an unpersisted canonical fact")
    source_documents = sorted({doc for fact in facts for doc in fact.source_documents})
    connection.execute(
        """
        UPDATE canonical_facts SET source_documents = ?, verification_status = 'unverified',
          autofill_permission = 0, updated_at = ? WHERE id = ?
        """,
        (json.dumps(source_documents), utc_now(), target.id),
    )
    for fact in facts[1:]:
        connection.execute(
            "UPDATE canonical_fact_sources SET canonical_fact_id = ? WHERE canonical_fact_id = ?",
            (target.id, fact.id),
        )
        connection.execute("DELETE FROM canonical_facts WHERE id = ?", (fact.id,))
    _audit(connection, target.id, "merge", [fact.model_dump() for fact in facts])
    connection.commit()
    return target.id


def _create_proposed_fact(
    connection: sqlite3.Connection,
    *,
    category: str,
    field_name: str,
    value: str,
    notes: str,
    status: VerificationStatus = "unverified",
    sensitivity: str = "ordinary",
    derived_from: list[int] | None = None,
    supersedes: list[int] | None = None,
) -> int:
    existing = connection.execute(
        "SELECT id FROM canonical_facts WHERE category = ? AND field_name = ?",
        (category, field_name),
    ).fetchone()
    if existing:
        return int(existing["id"])
    return _insert_fact(
        connection,
        CanonicalFact(
            category=category,
            field_name=field_name,
            canonical_value=value,
            display_value=value,
            normalized_value=normalize_value(value),
            verification_status=status,
            sensitivity=sensitivity,
            autofill_permission=False,
            applicable_tracks=[
                "Biology Teaching / Academic",
                "MSL / Medical Affairs",
                "FAS / Technical Applications",
            ],
            review_notes=notes,
            derived_from=derived_from or [],
            supersedes=supersedes or [],
        ),
    )


def seed_phase_15_proposals(connection: sqlite3.Connection) -> list[int]:
    duration_ids = [
        int(row["id"])
        for row in connection.execute(
            """
            SELECT id FROM canonical_facts
            WHERE category = 'experience_claims' AND field_name = 'seven_plus_years'
            """
        )
    ]
    publication_status_ids = [
        int(row["id"])
        for row in connection.execute(
            """
            SELECT id FROM canonical_facts
            WHERE category = 'publications'
              AND field_name = 'alppl2_car_t_publication_status'
            """
        )
    ]
    for fact_id in duration_ids + publication_status_ids:
        sibling_ids = duration_ids if fact_id in duration_ids else publication_status_ids
        sibling_values = [
            str(row["canonical_value"])
            for row in connection.execute(
                f"""
                SELECT canonical_value FROM canonical_facts
                WHERE id IN ({",".join("?" for _ in sibling_ids)}) AND id != ?
                """,  # noqa: S608 - placeholders only
                (*sibling_ids, fact_id),
            )
        ]
        connection.execute(
            """
            UPDATE canonical_facts
            SET verification_status = 'conflicted', autofill_permission = 0,
              conflicting_values = ?, updated_at = ?
            WHERE id = ? AND review_notes NOT LIKE 'Superseded by canonical fact%'
            """,
            (json.dumps(sibling_values), utc_now(), fact_id),
        )
    ids = [
        _create_proposed_fact(
            connection,
            category="experience_duration",
            field_name="postdoctoral_oncology_research_duration",
            value="6 years",
            notes="Proposed from October 2018 through October 2024; human approval required",
            derived_from=duration_ids,
            supersedes=duration_ids,
        ),
        _create_proposed_fact(
            connection,
            category="experience_duration",
            field_name="combined_biomedical_research_duration",
            value="10+ years",
            notes=(
                "Proposed from graduate research beginning August 2013 through "
                "postdoctoral work ending October 2024; not an oncology-duration claim"
            ),
            derived_from=duration_ids,
            supersedes=duration_ids,
        ),
        _create_proposed_fact(
            connection,
            category="publications",
            field_name="alppl2_car_t_publication",
            value=(
                "Coexpression of IL15 Promotes Effector Differentiation and Sustained "
                "Proliferative Capacity in ALPPL2-Specific Human CAR T Cells | "
                "Cancer Immunology Research | 2026 | 14 | 933–943 | "
                "10.1158/2326-6066.CIR-25-0609 | published"
            ),
            notes="Proposed normalized publication record; human approval required",
            derived_from=publication_status_ids,
            supersedes=publication_status_ids,
        ),
        _create_proposed_fact(
            connection,
            category="publications",
            field_name="nature_ai_benchmark_publication",
            value=(
                "A benchmark of expert-level academic questions to assess AI capabilities | "
                "Nature | 2026 | 649 | 1139–1146 | 10.1038/s41586-025-09962-4 | "
                "Contributor, HLE Contributors Consortium"
            ),
            notes=(
                "Authorship representation is proposed and unverified; do not represent "
                "the candidate as first author"
            ),
        ),
        _create_proposed_fact(
            connection,
            category="patents_and_intellectual_property",
            field_name="public_facing_summary",
            value=(
                "Inventor on a patent application related to an ALPPL2-targeted "
                "CAR T-cell platform."
            ),
            notes="Application-safe wording proposed; requires individual manual review",
            status="restricted",
            sensitivity="restricted",
        ),
    ]
    for fact_id, sources in (
        (ids[0], duration_ids),
        (ids[1], duration_ids),
        (ids[2], publication_status_ids),
    ):
        connection.execute(
            """
            UPDATE canonical_facts SET derived_from = ?, supersedes = ?, updated_at = ?
            WHERE id = ?
            """,
            (json.dumps(sources), json.dumps(sources), utc_now(), fact_id),
        )
    connection.commit()
    return ids


def approve_and_supersede(connection: sqlite3.Connection, fact_id: int) -> CanonicalFact:
    return update_fact(connection, fact_id, action="approve")


INTAKE_FIELDS: dict[str, tuple[str, bool]] = {
    "email": ("ordinary", False),
    "phone": ("ordinary", False),
    "linkedin_url": ("ordinary", False),
    "preferred_name": ("ordinary", False),
    "current_city_state": ("ordinary", False),
    "work_authorization": ("sensitive", True),
    "sponsorship_requirement": ("sensitive", True),
    "willing_to_relocate": ("sensitive", True),
    "approved_relocation_destinations": ("sensitive", True),
    "maximum_travel_percentage": ("sensitive", True),
    "workplace_preferences": ("ordinary", False),
    "minimum_compensation": ("sensitive", True),
    "compensation_may_autofill": ("sensitive", True),
    "earliest_start_date": ("sensitive", True),
    "notice_period": ("sensitive", True),
    "previously_worked_for_employer": ("sensitive", True),
    "noncompete": ("sensitive", True),
    "eeo_answers": ("restricted", True),
}


def save_intake_answer(
    connection: sqlite3.Connection,
    field_name: str,
    value: str,
    *,
    explicitly_verified: bool,
    autofill_permission: bool = False,
) -> None:
    if field_name not in INTAKE_FIELDS:
        raise ValueError(f"Unknown intake field: {field_name}")
    sensitivity, manual_default = INTAKE_FIELDS[field_name]
    if sensitivity in {"sensitive", "restricted"}:
        raise RuntimeError(
            "Sensitive-answer encryption is not implemented; this value was not stored. "
            "Keep it manual-only."
        )
    status = "verified" if explicitly_verified else "unverified"
    permission = explicitly_verified and autofill_permission and not manual_default
    connection.execute(
        """
        INSERT INTO profile_intake
          (field_name, value, verification_status, sensitivity, autofill_permission,
           manual_only, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(field_name) DO UPDATE SET
          value = excluded.value,
          verification_status = excluded.verification_status,
          sensitivity = excluded.sensitivity,
          autofill_permission = excluded.autofill_permission,
          manual_only = excluded.manual_only,
          updated_at = excluded.updated_at
        """,
        (
            field_name,
            value,
            status,
            sensitivity,
            int(permission),
            int(manual_default),
            utc_now(),
        ),
    )
    _audit(
        connection,
        None,
        "intake_answer",
        after={"field_name": field_name, "status": status, "autofill": permission},
    )
    connection.commit()


def mark_sensitive_manual_only(connection: sqlite3.Connection, field_name: str) -> None:
    if field_name not in INTAKE_FIELDS:
        raise ValueError(f"Unknown intake field: {field_name}")
    sensitivity, _ = INTAKE_FIELDS[field_name]
    connection.execute(
        """
        INSERT INTO profile_intake
          (field_name, value, verification_status, sensitivity, autofill_permission,
           manual_only, updated_at)
        VALUES (?, NULL, 'restricted', ?, 0, 1, ?)
        ON CONFLICT(field_name) DO UPDATE SET
          value = NULL, verification_status = 'restricted', autofill_permission = 0,
          manual_only = 1, updated_at = excluded.updated_at
        """,
        (field_name, sensitivity, utc_now()),
    )
    _audit(connection, None, "sensitive_manual_only", after={"field_name": field_name})
    connection.commit()


def profile_readiness(connection: sqlite3.Connection) -> ReadinessReport:
    from jobbot.profile.effective_profile import resolve_effective_profile

    report = resolve_effective_profile(connection).readiness
    return ReadinessReport(ready=report.ready, failures=report.failures)


def missing_required_fields(connection: sqlite3.Connection) -> list[str]:
    report = profile_readiness(connection)
    return report.failures
