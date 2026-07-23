from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import resolve_db_path
from .migrations import (
    record_canonical_supersession_migration,
    record_phase_15_migration,
    record_phase_16_migration,
    record_phase_16_state_fix,
)


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    target = resolve_db_path(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target)
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    return connection


def initialize_schema(connection: sqlite3.Connection) -> None:
    cursor = connection.cursor()
    cursor.executescript(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            external_id TEXT,
            company TEXT,
            title TEXT,
            location TEXT,
            remote_status TEXT,
            salary TEXT,
            url TEXT,
            ats_type TEXT,
            description TEXT,
            required_qualifications TEXT,
            preferred_qualifications TEXT,
            responsibilities TEXT,
            travel_requirement TEXT,
            date_discovered TEXT,
            status TEXT DEFAULT 'new'
        );

        CREATE TABLE IF NOT EXISTS job_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL REFERENCES jobs(id),
            overall_score REAL NOT NULL,
            scientific_technical REAL,
            seniority REAL,
            transferable REAL,
            communication REAL,
            industry_feasibility REAL,
            geographic REAL,
            degree_match REAL,
            missing_qualifications REAL,
            dealbreakers REAL,
            reasons TEXT,
            conflicts TEXT,
            selected_track TEXT,
            confidence REAL,
            human_review_required INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS candidate_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            field_name TEXT NOT NULL,
            value TEXT,
            verified INTEGER DEFAULT 0,
            source TEXT,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS reusable_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_pattern TEXT NOT NULL,
            answer TEXT NOT NULL,
            source TEXT,
            verification_status TEXT DEFAULT 'unverified',
            last_reviewed TEXT,
            sensitivity_level TEXT DEFAULT 'low',
            may_autofill INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            document_type TEXT,
            source_path TEXT,
            extracted_text TEXT,
            metadata TEXT,
            checksum TEXT
        );

        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL REFERENCES jobs(id),
            status TEXT DEFAULT 'draft',
            selected_track TEXT,
            resume_path TEXT,
            answers_json TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS application_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id INTEGER NOT NULL REFERENCES applications(id),
            event_type TEXT NOT NULL,
            message TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS browser_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id INTEGER NOT NULL REFERENCES applications(id),
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            screenshot_path TEXT,
            audit_log TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS review_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_type TEXT NOT NULL,
            item_id INTEGER,
            summary TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS canonical_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            field_name TEXT NOT NULL,
            canonical_value TEXT,
            display_value TEXT,
            normalized_value TEXT,
            source_document TEXT,
            source_documents TEXT NOT NULL DEFAULT '[]',
            source_excerpt TEXT,
            verification_status TEXT NOT NULL DEFAULT 'unverified'
                CHECK (verification_status IN (
                    'unverified', 'verified', 'rejected', 'needs_edit',
                    'conflicted', 'derived', 'restricted'
                )),
            verification_method TEXT,
            sensitivity TEXT NOT NULL DEFAULT 'ordinary',
            autofill_permission INTEGER NOT NULL DEFAULT 0,
            applicable_tracks TEXT NOT NULL DEFAULT '[]',
            date_verified TEXT,
            review_notes TEXT,
            conflicting_values TEXT NOT NULL DEFAULT '[]',
            derived_from TEXT NOT NULL DEFAULT '[]',
            supersedes TEXT NOT NULL DEFAULT '[]',
            active INTEGER NOT NULL DEFAULT 1,
            superseded_by INTEGER REFERENCES canonical_facts(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS canonical_fact_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_fact_id INTEGER NOT NULL REFERENCES canonical_facts(id),
            raw_fact_id INTEGER REFERENCES candidate_facts(id),
            source_document TEXT NOT NULL,
            source_excerpt TEXT,
            confidential_excerpt TEXT
        );

        CREATE TABLE IF NOT EXISTS profile_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_fact_id INTEGER REFERENCES canonical_facts(id),
            action TEXT NOT NULL,
            actor TEXT NOT NULL DEFAULT 'human',
            before_json TEXT,
            after_json TEXT,
            notes TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS profile_intake (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            field_name TEXT NOT NULL UNIQUE,
            value TEXT,
            verification_status TEXT NOT NULL DEFAULT 'unverified',
            sensitivity TEXT NOT NULL DEFAULT 'ordinary',
            autofill_permission INTEGER NOT NULL DEFAULT 0,
            manual_only INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_canonical_fact_field
            ON canonical_facts(category, field_name);
        CREATE INDEX IF NOT EXISTS idx_canonical_source_fact
            ON canonical_fact_sources(canonical_fact_id);
        CREATE INDEX IF NOT EXISTS idx_profile_audit_fact
            ON profile_audit_log(canonical_fact_id);

        CREATE TABLE IF NOT EXISTS application_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            field_name TEXT NOT NULL UNIQUE,
            canonical_value TEXT,
            display_value TEXT,
            raw_value TEXT,
            verification_status TEXT NOT NULL,
            verification_method TEXT,
            sensitivity TEXT NOT NULL,
            autofill_permission INTEGER NOT NULL DEFAULT 0,
            question_categories TEXT NOT NULL DEFAULT '[]',
            date_verified TEXT,
            review_notes TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS automation_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            url TEXT NOT NULL,
            screenshot_path TEXT,
            state_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_automation_runs_job
            ON automation_runs(job_id, created_at);

        """
    )
    record_phase_15_migration(connection)
    record_phase_16_migration(connection)
    review_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(review_items)").fetchall()
    }
    if "recommended_action" not in review_columns:
        connection.execute("ALTER TABLE review_items ADD COLUMN recommended_action TEXT")
    if "metadata" not in review_columns:
        connection.execute("ALTER TABLE review_items ADD COLUMN metadata TEXT")
    answer_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(application_answers)").fetchall()
    }
    answer_migrations = {
        "normalized_value": "TEXT",
        "review_required": "INTEGER NOT NULL DEFAULT 0",
        "active": "INTEGER NOT NULL DEFAULT 1",
        "superseded_by": "TEXT",
        "provenance": "TEXT",
    }
    for column, definition in answer_migrations.items():
        if column not in answer_columns:
            connection.execute(f"ALTER TABLE application_answers ADD COLUMN {column} {definition}")
    record_phase_16_state_fix(connection)
    canonical_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(canonical_facts)").fetchall()
    }
    canonical_migrations = {
        "active": "INTEGER NOT NULL DEFAULT 1",
        "superseded_by": "INTEGER REFERENCES canonical_facts(id)",
    }
    for column, definition in canonical_migrations.items():
        if column not in canonical_columns:
            connection.execute(f"ALTER TABLE canonical_facts ADD COLUMN {column} {definition}")
    record_canonical_supersession_migration(connection)
    connection.commit()
