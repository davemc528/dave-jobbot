from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from jobbot.config import resolve_db_path
from jobbot.db import get_connection
from jobbot.extension.service import (
    ApplicationPlanPayload,
    CapturePayload,
    approve_hostname,
    audit_extension_event,
    authenticate_token,
    build_application_plan,
    capture_job,
    exchange_pairing_code,
)
from jobbot.jobs.analysis import analyze_job
from jobbot.profile.effective_profile import resolve_effective_profile
from jobbot.resumes.tailoring import (
    approved_resume_for_job,
    get_version,
    set_version_status,
    tailor_resume,
)

MAX_REQUEST_BYTES = 1_000_000


class PairRequest(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class HostRequest(BaseModel):
    hostname: str


class ApprovalRequest(BaseModel):
    confirmed: bool


class AuditRequest(BaseModel):
    action: str = Field(min_length=1, max_length=100)
    metadata: dict[str, object] = Field(default_factory=dict)


def db_connection() -> Iterator[sqlite3.Connection]:
    connection = get_connection()
    try:
        yield connection
    finally:
        connection.close()


def require_token(
    request: Request,
    connection: Annotated[sqlite3.Connection, Depends(db_connection)],
    authorization: Annotated[str | None, Header()] = None,
) -> int:
    origin = request.headers.get("origin")
    if origin and not origin.startswith("chrome-extension://"):
        raise HTTPException(status_code=403, detail={"code": "invalid_origin"})
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"code": "authentication_required"})
    try:
        return authenticate_token(connection, authorization.removeprefix("Bearer ").strip())
    except ValueError as exc:
        raise HTTPException(status_code=401, detail={"code": "invalid_token"}) from exc


TokenId = Annotated[int, Depends(require_token)]
Connection = Annotated[sqlite3.Connection, Depends(db_connection)]

app = FastAPI(title="Dave Jobbot Local Extension Bridge", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"chrome-extension://[a-z]{32}",
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def validate_request(request: Request, call_next):  # type: ignore[no-untyped-def]
    if request.method in {"POST", "PUT", "PATCH"}:
        content_type = request.headers.get("content-type", "")
        if not content_type.startswith("application/json"):
            return _error_response(415, "json_content_type_required")
        length = int(request.headers.get("content-length", "0") or "0")
        if length > MAX_REQUEST_BYTES:
            return _error_response(413, "request_too_large")
    return await call_next(request)


def _error_response(status: int, code: str):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status, content={"error": {"code": code}})


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError):  # type: ignore[no-untyped-def]
    return _error_response(400, str(exc))


@app.exception_handler(PermissionError)
async def permission_error_handler(  # type: ignore[no-untyped-def]
    _request: Request, exc: PermissionError
):
    return _error_response(403, str(exc))


@app.get("/api/v1/health")
def health(connection: Connection) -> dict[str, object]:
    profile = resolve_effective_profile(connection).readiness
    paired = connection.execute(
        "SELECT count(*) FROM extension_tokens WHERE revoked_at IS NULL"
    ).fetchone()[0]
    return {
        "status": "ok",
        "bind_host": "127.0.0.1",
        "database": str(resolve_db_path()),
        "profile_ready": profile.ready,
        "paired_sessions": paired,
        "final_submit_enabled": False,
    }


@app.post("/api/v1/pair")
def pair(payload: PairRequest, connection: Connection) -> dict[str, str]:
    try:
        token = exchange_pairing_code(connection, payload.code)
    except ValueError as exc:
        raise HTTPException(
            status_code=401, detail={"code": "invalid_or_expired_pairing_code"}
        ) from exc
    return {"token": token}


@app.get("/api/v1/hosts")
def hosts(_token: TokenId, connection: Connection) -> dict[str, object]:
    rows = connection.execute(
        "SELECT hostname, approved_at FROM extension_approved_hosts WHERE revoked_at IS NULL"
    ).fetchall()
    return {"hosts": [dict(row) for row in rows]}


@app.post("/api/v1/hosts/approve")
def host_approve(payload: HostRequest, token: TokenId, connection: Connection) -> dict[str, str]:
    return {"hostname": approve_hostname(connection, payload.hostname, token)}


@app.post("/api/v1/hosts/revoke")
def host_revoke(payload: HostRequest, _token: TokenId, connection: Connection) -> dict[str, str]:
    connection.execute(
        "UPDATE extension_approved_hosts SET revoked_at=datetime('now') WHERE hostname=?",
        (payload.hostname.rstrip(".").casefold(),),
    )
    connection.commit()
    return {"hostname": payload.hostname, "status": "revoked"}


@app.post("/api/v1/session/revoke")
def session_revoke(
    token: TokenId, connection: Connection
) -> dict[str, str]:
    connection.execute(
        "UPDATE extension_tokens SET revoked_at=datetime('now') WHERE id=?", (token,)
    )
    connection.commit()
    return {"status": "revoked"}


@app.post("/api/v1/jobs/capture")
def jobs_capture(
    payload: CapturePayload, _token: TokenId, connection: Connection
) -> dict[str, object]:
    job_id, existing, analysis = capture_job(connection, payload)
    return {
        "job_id": job_id,
        "existing": existing,
        "message": (
            f"Existing job found: Job ID {job_id}"
            if existing
            else f"New job created: Job ID {job_id}"
        ),
        "analysis": analysis.model_dump(),
    }


@app.get("/api/v1/jobs/{job_id}")
def job_get(job_id: int, _token: TokenId, connection: Connection) -> dict[str, object]:
    row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "job_not_found"})
    return dict(row)


@app.post("/api/v1/jobs/{job_id}/analyze")
def job_analyze(job_id: int, _token: TokenId, connection: Connection) -> dict[str, object]:
    return analyze_job(connection, job_id).model_dump()


@app.post("/api/v1/jobs/{job_id}/tailor")
def job_tailor(job_id: int, _token: TokenId, connection: Connection) -> dict[str, object]:
    return tailor_resume(connection, job_id).model_dump(exclude={"claims"})


@app.get("/api/v1/jobs/{job_id}/resume-versions")
def resume_versions(job_id: int, _token: TokenId, connection: Connection) -> dict[str, object]:
    rows = connection.execute(
        """
        SELECT id, version, status, selected_track, validation_status, warnings, created_at
        FROM tailored_resumes WHERE job_id=? ORDER BY version DESC
        """,
        (job_id,),
    ).fetchall()
    return {"versions": [dict(row) for row in rows]}


@app.get("/api/v1/resumes/{resume_id}")
def resume_get(resume_id: int, _token: TokenId, connection: Connection) -> dict[str, object]:
    row = connection.execute(
        "SELECT job_id FROM tailored_resumes WHERE id=?", (resume_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "resume_not_found"})
    return get_version(connection, int(row["job_id"]), resume_id).model_dump()


@app.post("/api/v1/resumes/{resume_id}/approve")
def resume_approve(
    resume_id: int,
    payload: ApprovalRequest,
    _token: TokenId,
    connection: Connection,
) -> dict[str, object]:
    if not payload.confirmed:
        raise HTTPException(status_code=400, detail={"code": "confirmation_required"})
    row = connection.execute(
        "SELECT job_id FROM tailored_resumes WHERE id=?", (resume_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "resume_not_found"})
    return set_version_status(connection, int(row["job_id"]), resume_id, "approved").model_dump(
        exclude={"claims"}
    )


@app.post("/api/v1/applications/plan")
def applications_plan(
    payload: ApplicationPlanPayload, _token: TokenId, connection: Connection
) -> dict[str, object]:
    approved = approved_resume_for_job(connection, payload.job_id)
    plan = build_application_plan(connection, payload)
    plan["approved_resume"] = (
        {
            "id": approved.id,
            "version": approved.version,
            "track": approved.selected_track,
            "filename": approved.docx_path.rsplit("/", 1)[-1],
            "validation_status": approved.validation_status,
        }
        if approved
        else None
    )
    return plan


@app.post("/api/v1/applications/audit")
def applications_audit(
    payload: AuditRequest, _token: TokenId, connection: Connection
) -> dict[str, str]:
    audit_extension_event(connection, payload.action, payload.metadata)
    return {"status": "recorded"}


@app.get("/api/v1/reviews/{job_id}")
def reviews(job_id: int, _token: TokenId, connection: Connection) -> dict[str, object]:
    rows = connection.execute(
        """
        SELECT id, item_type, summary, status, recommended_action
        FROM review_items WHERE status='pending'
          AND (metadata IS NULL OR json_extract(metadata, '$.job_id')=?)
        ORDER BY id
        """,
        (job_id,),
    ).fetchall()
    return {"reviews": [dict(row) for row in rows]}
