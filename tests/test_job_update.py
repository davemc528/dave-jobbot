import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from jobbot.cli import app
from jobbot.db import initialize_schema
from jobbot.jobs.intake import intake_file
from jobbot.jobs.update import update_application_url


def database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    connection.execute(
        """
        INSERT INTO jobs (id, source, title, description)
        VALUES (6, 'file', 'Oncology role', 'Original posting text')
        """
    )
    connection.execute(
        """
        INSERT INTO job_postings
          (job_id, raw_text, normalized_text, retrieval_method, retrieved_at,
           complete, completeness_warnings, normalized_fields)
        VALUES (6, 'raw', 'Original posting text', 'file:.txt', 'now', 1, '[]', '{}')
        """
    )
    connection.commit()
    return connection


def test_successful_update_preserves_description_and_audits() -> None:
    connection = database()
    connection.execute(
        """
        INSERT INTO jobs (id, source, title, description, url)
        VALUES (5, 'url', 'Existing record', 'Other posting',
                'https://example.com/job/123')
        """
    )
    connection.commit()
    before_count = connection.execute("SELECT count(*) FROM jobs").fetchone()[0]
    result = update_application_url(
        connection,
        6,
        "https://example.com/job/123",
    )
    row = connection.execute("SELECT url, description FROM jobs WHERE id=6").fetchone()
    fields = json.loads(
        connection.execute("SELECT normalized_fields FROM job_postings WHERE job_id=6").fetchone()[
            0
        ]
    )
    assert result.old_url is None
    assert result.new_url == "https://example.com/job/123"
    assert row["url"] == result.new_url
    assert row["description"] == "Original posting text"
    assert fields["application_url"] == result.new_url
    assert connection.execute("SELECT count(*) FROM jobs").fetchone()[0] == before_count
    audit = connection.execute(
        """
        SELECT before_json, after_json FROM profile_audit_log
        WHERE action='job_application_url_updated'
        """
    ).fetchone()
    assert json.loads(audit["before_json"])["application_url"] is None
    assert json.loads(audit["after_json"])["application_url"] == result.new_url


def test_missing_job_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown job id"):
        update_application_url(database(), 999, "https://example.com/job/123")


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/job/123",
        "not-a-url",
        "ftp://example.com/job/123",
        "https://user:password@example.com/job/123",
    ],
)
def test_invalid_application_url_is_rejected(url: str) -> None:
    with pytest.raises(ValueError, match="HTTPS URL|embedded credentials"):
        update_application_url(database(), 6, url)


def test_tracking_parameters_are_removed() -> None:
    result = update_application_url(
        database(),
        6,
        "https://example.com/job/123?id=7&utm_source=email&gclid=tracking",
    )
    assert result.new_url == "https://example.com/job/123?id=7"


def test_file_metadata_and_explicit_url_precedence(tmp_path: Path) -> None:
    path = tmp_path / "posting.txt"
    path.write_text(
        "Application URL: https://example.com/job/from-file?utm_source=email\n"
        "Oncology Medical Science Liaison\n"
        + ("Responsibilities and qualifications for oncology research.\n" * 30)
    )
    intake = intake_file(path)
    assert intake.normalized_url == "https://example.com/job/from-file"
    assert intake.fields["title"] == "Oncology Medical Science Liaison"

    explicit = "https://example.com/job/explicit?utm_campaign=test"
    database_path = tmp_path / "cli.db"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "job",
            "add",
            "--file",
            str(path),
            "--application-url",
            explicit,
        ],
        env={"JOBBOT_DB_PATH": str(database_path)},
    )
    assert result.exit_code == 0, result.output
    connection = sqlite3.connect(database_path)
    stored = connection.execute("SELECT url FROM jobs").fetchone()[0]
    assert stored == "https://example.com/job/explicit"
