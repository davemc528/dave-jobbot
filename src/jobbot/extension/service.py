from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel, Field

from jobbot.browser.preflight import domain_is_allowed, normalize_hostname
from jobbot.jobs.analysis import JobAnalysis, analyze_job
from jobbot.jobs.intake import normalize_posting, save_posting, save_posting_for_job
from jobbot.jobs.update import normalize_application_url, update_application_url
from jobbot.profile.application_answers import QuestionContext, answer_map, match_question
from jobbot.security import redact_url_tracking_parameters

PAIRING_TTL_MINUTES = 5
MAX_PAIR_FAILURES = 5


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class CapturePayload(BaseModel):
    tab_key: str = Field(min_length=1, max_length=128)
    current_url: str
    canonical_url: str | None = None
    application_url: str | None = None
    hostname: str
    employer: str | None = None
    job_title: str | None = None
    location: str | None = None
    workplace_arrangement: str | None = None
    requisition_id: str | None = None
    posting_date: str | None = None
    compensation: str | None = None
    responsibilities: list[str] = Field(default_factory=list)
    required_qualifications: list[str] = Field(default_factory=list)
    preferred_qualifications: list[str] = Field(default_factory=list)
    full_text: str = Field(min_length=80, max_length=500_000)
    ats_type: str
    extraction_method: str
    extraction_confidence: float = Field(ge=0, le=1)
    page_title: str
    json_ld: dict[str, object] | None = None
    captured_at: str


class FieldInventory(BaseModel):
    identifier: str = Field(min_length=1, max_length=300)
    element_type: str
    input_type: str
    name: str | None = None
    element_id: str | None = None
    label: str = ""
    aria_label: str | None = None
    placeholder: str | None = None
    nearby_text: str | None = None
    required: bool = False
    has_value: bool = False
    options: list[str] = Field(default_factory=list)
    section_heading: str | None = None
    page_url: str
    frame_identifier: str = "top"
    terminal: bool = False
    sensitive: bool = False


class ApplicationPlanPayload(BaseModel):
    job_id: int
    tab_key: str
    hostname: str
    fields: list[FieldInventory] = Field(max_length=500)


def create_pairing_code(connection: sqlite3.Connection) -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = utc_now()
    connection.execute(
        """
        INSERT INTO extension_pairing_codes (code_hash, expires_at, created_at)
        VALUES (?, ?, ?)
        """,
        (digest(code), (now + timedelta(minutes=PAIRING_TTL_MINUTES)).isoformat(), now.isoformat()),
    )
    connection.commit()
    return code


def exchange_pairing_code(connection: sqlite3.Connection, code: str) -> str:
    code_hash = digest(code.strip())
    row = connection.execute(
        "SELECT * FROM extension_pairing_codes WHERE code_hash=?", (code_hash,)
    ).fetchone()
    if row is None:
        connection.execute(
            """
            UPDATE extension_pairing_codes SET failed_attempts=failed_attempts+1
            WHERE used_at IS NULL AND expires_at>?
            """,
            (utc_now().isoformat(),),
        )
        connection.commit()
        raise ValueError("Pairing code is invalid, expired, or already used")
    if (
        row["used_at"]
        or datetime.fromisoformat(row["expires_at"]) <= utc_now()
        or row["failed_attempts"] >= MAX_PAIR_FAILURES
    ):
        connection.execute(
            "UPDATE extension_pairing_codes SET failed_attempts=failed_attempts+1 WHERE id=?",
            (row["id"],),
        )
        connection.commit()
        raise ValueError("Pairing code is invalid, expired, or already used")
    token = secrets.token_urlsafe(32)
    now = utc_now().isoformat()
    connection.execute(
        "INSERT INTO extension_tokens (token_hash, created_at) VALUES (?, ?)",
        (digest(token), now),
    )
    connection.execute("UPDATE extension_pairing_codes SET used_at=? WHERE id=?", (now, row["id"]))
    connection.commit()
    return token


def authenticate_token(connection: sqlite3.Connection, token: str) -> int:
    row = connection.execute(
        """
        SELECT id FROM extension_tokens
        WHERE token_hash=? AND revoked_at IS NULL
        """,
        (digest(token),),
    ).fetchone()
    if row is None:
        raise ValueError("Invalid or revoked extension token")
    connection.execute(
        "UPDATE extension_tokens SET last_used_at=? WHERE id=?",
        (utc_now().isoformat(), row["id"]),
    )
    connection.commit()
    return int(row["id"])


def revoke_tokens(connection: sqlite3.Connection) -> int:
    cursor = connection.execute(
        "UPDATE extension_tokens SET revoked_at=? WHERE revoked_at IS NULL",
        (utc_now().isoformat(),),
    )
    connection.commit()
    return cursor.rowcount


def approve_hostname(connection: sqlite3.Connection, hostname: str, token_id: int) -> str:
    normalized = normalize_hostname(f"https://{hostname}")
    if not normalized or normalized != hostname.rstrip(".").casefold():
        raise ValueError("Hostname must be an exact normalized hostname")
    connection.execute(
        """
        INSERT INTO extension_approved_hosts (hostname, approved_at, token_id, revoked_at)
        VALUES (?, ?, ?, NULL)
        ON CONFLICT(hostname) DO UPDATE SET approved_at=excluded.approved_at,
          token_id=excluded.token_id, revoked_at=NULL
        """,
        (normalized, utc_now().isoformat(), token_id),
    )
    connection.commit()
    return normalized


def hostname_is_approved(connection: sqlite3.Connection, hostname: str) -> bool:
    normalized = normalize_hostname(f"https://{hostname}")
    rows = connection.execute(
        "SELECT hostname FROM extension_approved_hosts WHERE revoked_at IS NULL"
    ).fetchall()
    return domain_is_allowed(normalized, [str(row["hostname"]) for row in rows])


def _find_existing_job(connection: sqlite3.Connection, payload: CapturePayload) -> int | None:
    posting_url = redact_url_tracking_parameters(payload.canonical_url or payload.current_url)
    application_url = (
        normalize_application_url(payload.application_url) if payload.application_url else None
    )
    for url in (application_url, posting_url):
        row = connection.execute(
            """
            SELECT jobs.id FROM jobs
            LEFT JOIN job_postings ON job_postings.job_id=jobs.id
            WHERE jobs.url=? OR job_postings.normalized_url=?
            LIMIT 1
            """,
            (url, url),
        ).fetchone()
        if row:
            return int(row["id"])
    if payload.requisition_id:
        rows = connection.execute(
            """
            SELECT job_id, normalized_fields FROM job_postings
            WHERE json_extract(normalized_fields, '$.ats_platform')=?
            """,
            (payload.ats_type,),
        ).fetchall()
        for row in rows:
            fields = json.loads(row["normalized_fields"] or "{}")
            if fields.get("requisition_id") == payload.requisition_id:
                return int(row["job_id"])
    row = connection.execute(
        """
        SELECT id FROM jobs
        WHERE lower(coalesce(company,''))=lower(?)
          AND lower(trim(coalesce(title,'')))=lower(trim(?))
          AND lower(coalesce(location,''))=lower(?)
        LIMIT 1
        """,
        (payload.employer or "", payload.job_title or "", payload.location or ""),
    ).fetchone()
    return int(row["id"]) if row else None


def capture_job(
    connection: sqlite3.Connection, payload: CapturePayload
) -> tuple[int, bool, JobAnalysis]:
    actual_host = normalize_hostname(payload.current_url)
    if actual_host != payload.hostname.rstrip(".").casefold():
        raise ValueError("Payload hostname does not match the captured URL")
    if not hostname_is_approved(connection, actual_host):
        raise PermissionError(f"Hostname is not approved: {actual_host}")
    posting_url = redact_url_tracking_parameters(payload.canonical_url or payload.current_url)
    intake = normalize_posting(
        payload.full_text,
        source_url=posting_url,
        method=f"extension:{payload.extraction_method}",
    )
    intake.fields.update(
        {
            "employer": payload.employer,
            "title": payload.job_title,
            "location": payload.location,
            "remote_status": payload.workplace_arrangement,
            "compensation": payload.compensation,
            "responsibilities": payload.responsibilities,
            "mandatory_qualifications": payload.required_qualifications,
            "preferred_qualifications": payload.preferred_qualifications,
            "ats_platform": payload.ats_type,
            "requisition_id": payload.requisition_id,
            "posting_date": payload.posting_date,
            "application_url": payload.application_url,
        }
    )
    existing_id = _find_existing_job(connection, payload)
    if existing_id is None:
        job_id = save_posting(connection, intake)
        existing = False
    else:
        job_id = existing_id
        save_posting_for_job(connection, job_id, intake, preserve_existing_url=True)
        existing = True
    if payload.application_url:
        update_application_url(connection, job_id, payload.application_url)
    connection.execute(
        """
        INSERT INTO extension_job_snapshots
          (job_id, normalized_url, extraction_method, extraction_confidence,
           payload_json, captured_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            posting_url,
            payload.extraction_method,
            payload.extraction_confidence,
            payload.model_dump_json(),
            payload.captured_at,
        ),
    )
    connection.execute(
        """
        INSERT INTO extension_tab_associations
          (tab_key, job_id, hostname, posting_url, application_url,
           requisition_id, stage, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'analysis_ready', ?)
        ON CONFLICT(tab_key) DO UPDATE SET job_id=excluded.job_id,
          hostname=excluded.hostname, posting_url=excluded.posting_url,
          application_url=excluded.application_url, requisition_id=excluded.requisition_id,
          stage=excluded.stage, updated_at=excluded.updated_at
        """,
        (
            payload.tab_key,
            job_id,
            actual_host,
            posting_url,
            payload.application_url,
            payload.requisition_id,
            utc_now().isoformat(),
        ),
    )
    connection.commit()
    return job_id, existing, analyze_job(connection, job_id)


def build_application_plan(
    connection: sqlite3.Connection, payload: ApplicationPlanPayload
) -> dict[str, object]:
    if not hostname_is_approved(connection, payload.hostname):
        raise PermissionError(f"Hostname is not approved: {payload.hostname}")
    job = connection.execute("SELECT * FROM jobs WHERE id=?", (payload.job_id,)).fetchone()
    if job is None:
        raise ValueError("Unknown job ID")
    answers = answer_map(connection)
    plan: list[dict[str, object]] = []
    terminal_detected = False
    human_intervention = False
    for field in payload.fields:
        label = " ".join(
            value
            for value in (field.label, field.aria_label, field.placeholder, field.nearby_text)
            if value
        )
        lowered = label.casefold()
        if field.terminal:
            terminal_detected = True
            plan.append(
                {
                    "field_identifier": field.identifier,
                    "decision": "withhold",
                    "withhold_reason": "terminal_submit_control",
                    "human_review_required": True,
                }
            )
            continue
        if field.input_type in {"password"} or any(
            term in lowered
            for term in (
                "password",
                "one-time code",
                "verification code",
                "security question",
                "mfa",
            )
        ):
            human_intervention = True
            plan.append(
                {
                    "field_identifier": field.identifier,
                    "decision": "withhold",
                    "withhold_reason": "authentication_or_secret",
                    "human_review_required": True,
                }
            )
            continue
        match = match_question(
            label,
            input_type=field.input_type,
            options=field.options,
            context=QuestionContext(
                posting_locations=[str(job["location"] or "")],
                employer=str(job["company"] or ""),
            ),
            answers=answers,
        )
        answer = answers.get(match.source_fact or "")
        plan.append(
            {
                "field_identifier": field.identifier,
                "proposed_value": match.proposed_answer if match.autofill_permitted else None,
                "verified_application_answer_id": answer.id if answer else None,
                "supporting_profile_fact_ids": [],
                "confidence": match.confidence,
                "transformation": "exact",
                "decision": "fill" if match.autofill_permitted else "withhold",
                "withhold_reason": None
                if match.autofill_permitted
                else match.review_type or "not_verified_or_not_permitted",
                "human_review_required": not match.autofill_permitted,
            }
        )
    status = (
        "stopped_before_submit"
        if terminal_detected
        else "human_intervention_required"
        if human_intervention
        else "ready_to_fill"
    )
    return {"status": status, "fields": plan, "final_submission_allowed": False}


def audit_extension_event(
    connection: sqlite3.Connection, action: str, metadata: dict[str, object]
) -> None:
    safe_metadata = {
        key: value
        for key, value in metadata.items()
        if key.casefold() not in {"token", "password", "value", "resume_bytes"}
    }
    connection.execute(
        """
        INSERT INTO profile_audit_log (action, actor, after_json, notes, created_at)
        VALUES (?, 'human', ?, 'Chrome extension supervised action', ?)
        """,
        (action, json.dumps(safe_metadata), utc_now().isoformat()),
    )
    connection.commit()
