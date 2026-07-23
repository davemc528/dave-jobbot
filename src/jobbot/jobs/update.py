from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

from jobbot.security import redact_url_tracking_parameters


@dataclass(frozen=True)
class JobURLUpdate:
    job_id: int
    old_url: str | None
    new_url: str


def normalize_application_url(url: str) -> str:
    value = url.strip()
    parts = urlsplit(value)
    if parts.scheme.casefold() != "https" or not parts.hostname:
        raise ValueError("Application URL must be a valid HTTPS URL")
    if parts.username or parts.password:
        raise ValueError("Application URL must not contain embedded credentials")
    return redact_url_tracking_parameters(value)


def update_application_url(
    connection: sqlite3.Connection,
    job_id: int,
    application_url: str,
) -> JobURLUpdate:
    row = connection.execute("SELECT id, url FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown job id: {job_id}")
    cleaned = normalize_application_url(application_url)
    old_url = str(row["url"]) if row["url"] else None
    connection.execute("UPDATE jobs SET url=? WHERE id=?", (cleaned, job_id))
    posting = connection.execute(
        "SELECT normalized_fields FROM job_postings WHERE job_id=?", (job_id,)
    ).fetchone()
    if posting:
        fields = json.loads(posting["normalized_fields"] or "{}")
        fields["application_url"] = cleaned
        connection.execute(
            "UPDATE job_postings SET normalized_fields=? WHERE job_id=?",
            (json.dumps(fields), job_id),
        )
    connection.execute(
        """
        INSERT INTO profile_audit_log
          (action, actor, before_json, after_json, notes, created_at)
        VALUES ('job_application_url_updated', 'human', ?, ?, ?, ?)
        """,
        (
            json.dumps({"job_id": job_id, "application_url": old_url}),
            json.dumps({"job_id": job_id, "application_url": cleaned}),
            "Existing job application URL attached or changed; posting text preserved",
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    connection.commit()
    return JobURLUpdate(job_id=job_id, old_url=old_url, new_url=cleaned)
