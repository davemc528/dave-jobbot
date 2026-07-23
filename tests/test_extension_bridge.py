import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from jobbot.db import initialize_schema
from jobbot.extension.api import app, db_connection
from jobbot.extension.service import (
    approve_hostname,
    create_pairing_code,
    digest,
    exchange_pairing_code,
)
from jobbot.profile.application_answers import apply_approved_defaults
from jobbot.tailoring.routing import select_resume_track

ORIGIN = {"Origin": f"chrome-extension://{'a' * 32}"}


@pytest.fixture
def bridge() -> Iterator[tuple[TestClient, sqlite3.Connection, str]]:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    apply_approved_defaults(connection)

    def override() -> Iterator[sqlite3.Connection]:
        yield connection

    app.dependency_overrides[db_connection] = override
    code = create_pairing_code(connection)
    client = TestClient(app)
    response = client.post("/api/v1/pair", json={"code": code}, headers=ORIGIN)
    token = response.json()["token"]
    yield client, connection, token
    app.dependency_overrides.clear()
    connection.close()


def auth(token: str) -> dict[str, str]:
    return {**ORIGIN, "Authorization": f"Bearer {token}"}


def capture_payload(hostname: str = "jobs.example.test") -> dict[str, object]:
    text = (
        "Discovery Scientist\nResponsibilities\nConduct translational cancer immunology "
        "research and in vivo studies.\nRequired Qualifications\nPhD in biomedical sciences.\n"
    ) * 8
    return {
        "tab_key": "tab-1",
        "current_url": f"https://{hostname}/job/ABC?utm_source=email",
        "canonical_url": f"https://{hostname}/job/ABC?utm_source=email",
        "application_url": f"https://{hostname}/job/ABC/apply?ref=campaign",
        "hostname": hostname,
        "employer": "Example Bio",
        "job_title": "Discovery Scientist, Cancer Immunology",
        "location": "Houston, TX",
        "requisition_id": "ABC",
        "responsibilities": ["Conduct translational cancer immunology research"],
        "required_qualifications": ["PhD in biomedical sciences"],
        "preferred_qualifications": [],
        "full_text": text,
        "ats_type": "generic",
        "extraction_method": "generic-visible-content",
        "extraction_confidence": 0.9,
        "page_title": "Discovery Scientist",
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


def test_health_is_public_but_privileged_routes_require_authentication(
    bridge: tuple[TestClient, sqlite3.Connection, str],
) -> None:
    client, _connection, token = bridge
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/hosts").status_code == 401
    assert client.get("/api/v1/hosts", headers=auth(token)).status_code == 200
    invalid_origin = {
        "Origin": "https://attacker.example",
        "Authorization": f"Bearer {token}",
    }
    assert client.get("/api/v1/hosts", headers=invalid_origin).status_code == 403


def test_pairing_code_expires_and_cannot_be_reused() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    code = create_pairing_code(connection)
    assert exchange_pairing_code(connection, code)
    with pytest.raises(ValueError):
        exchange_pairing_code(connection, code)
    expired = create_pairing_code(connection)
    connection.execute(
        "UPDATE extension_pairing_codes SET expires_at=? WHERE code_hash=?",
        ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), digest(expired)),
    )
    connection.commit()
    with pytest.raises(ValueError):
        exchange_pairing_code(connection, expired)


def test_capture_deduplicates_and_exact_hostname_is_independently_checked(
    bridge: tuple[TestClient, sqlite3.Connection, str],
) -> None:
    client, connection, token = bridge
    approve = client.post(
        "/api/v1/hosts/approve",
        json={"hostname": "jobs.example.test"},
        headers=auth(token),
    )
    assert approve.status_code == 200
    first = client.post("/api/v1/jobs/capture", json=capture_payload(), headers=auth(token))
    second = client.post("/api/v1/jobs/capture", json=capture_payload(), headers=auth(token))
    assert first.status_code == 200, first.text
    assert first.json()["existing"] is False
    assert second.json()["existing"] is True
    assert second.json()["job_id"] == first.json()["job_id"]
    assert connection.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    assert connection.execute("SELECT count(*) FROM extension_job_snapshots").fetchone()[0] == 2
    urls = connection.execute(
        "SELECT jobs.url, job_postings.normalized_url FROM jobs JOIN job_postings"
    ).fetchone()
    assert "ref=" not in urls["url"]
    assert "utm_" not in urls["normalized_url"]

    malicious = capture_payload("jobs.example.test.attacker.example")
    rejected = client.post("/api/v1/jobs/capture", json=malicious, headers=auth(token))
    assert rejected.status_code == 403


def test_low_confidence_refresh_versions_existing_job_and_marks_review(
    bridge: tuple[TestClient, sqlite3.Connection, str],
) -> None:
    client, connection, token = bridge
    approve_hostname(connection, "jobs.example.test", 1)
    first_payload = capture_payload()
    first_payload["capture_id"] = "capture-first-version"
    first = client.post("/api/v1/jobs/capture", json=first_payload, headers=auth(token)).json()
    refreshed_payload = capture_payload()
    refreshed_payload.update(
        {
            "capture_id": "capture-second-version",
            "requires_human_review": True,
            "extraction_confidence": 0.2,
        }
    )
    refreshed = client.post(
        "/api/v1/jobs/capture", json=refreshed_payload, headers=auth(token)
    ).json()
    assert refreshed["existing"] is True
    assert refreshed["job_id"] == first["job_id"]
    assert connection.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    assert (
        connection.execute(
            "SELECT count(*) FROM extension_job_snapshots WHERE job_id=?",
            (first["job_id"],),
        ).fetchone()[0]
        == 2
    )
    assert (
        connection.execute("SELECT status FROM jobs WHERE id=?", (first["job_id"],)).fetchone()[
            "status"
        ]
        == "needs_review"
    )


def test_application_plan_withholds_password_and_terminal_fields(
    bridge: tuple[TestClient, sqlite3.Connection, str],
) -> None:
    client, connection, token = bridge
    approve_hostname(connection, "jobs.example.test", 1)
    created = client.post(
        "/api/v1/jobs/capture", json=capture_payload(), headers=auth(token)
    ).json()
    result = client.post(
        "/api/v1/applications/plan",
        headers=auth(token),
        json={
            "job_id": created["job_id"],
            "tab_key": "tab-1",
            "hostname": "jobs.example.test",
            "fields": [
                {
                    "identifier": "#email",
                    "element_type": "input",
                    "input_type": "email",
                    "label": "Email",
                    "page_url": "https://jobs.example.test/apply",
                },
                {
                    "identifier": "#password",
                    "element_type": "input",
                    "input_type": "password",
                    "label": "Password",
                    "page_url": "https://jobs.example.test/apply",
                    "sensitive": True,
                },
                {
                    "identifier": "#submit",
                    "element_type": "button",
                    "input_type": "submit",
                    "label": "Submit Application",
                    "page_url": "https://jobs.example.test/apply",
                    "terminal": True,
                },
            ],
        },
    )
    assert result.status_code == 200
    body = result.json()
    assert body["status"] == "stopped_before_submit"
    assert body["final_submission_allowed"] is False
    decisions = {item["field_identifier"]: item for item in body["fields"]}
    assert decisions["#email"]["decision"] == "fill"
    assert decisions["#password"]["decision"] == "withhold"
    assert decisions["#submit"]["decision"] == "withhold"
    assert body["approved_resume"] is None


def test_discovery_scientist_routing_beats_fas_method_signals() -> None:
    route = select_resume_track(
        "Discovery Scientist, Cancer Immunology",
        "Translational CAR-T research using flow cytometry, in vivo studies, and RNA-seq",
    )
    assert route.selected_track == "Discovery / Translational Scientist"
    assert any("discovery" in reason.casefold() for reason in route.reasons)
