from pathlib import Path

from streamlit.testing.v1 import AppTest

from jobbot.config import resolve_db_path
from jobbot.db import get_connection
from jobbot.profile.application_answers import apply_approved_defaults
from jobbot.profile.effective_profile import resolve_effective_profile


def test_database_resolver_and_streamlit_refresh_share_current_state(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "dashboard-state.db"
    monkeypatch.setenv("JOBBOT_DB_PATH", str(database_path))
    assert resolve_db_path() == database_path.resolve()
    with get_connection() as connection:
        apply_approved_defaults(connection)
        sqlite_path = connection.execute("PRAGMA database_list").fetchone()["file"]
    assert Path(sqlite_path).resolve() == resolve_db_path()

    app = AppTest.from_file("src/jobbot/ui/dashboard.py", default_timeout=30).run(timeout=30)
    app.sidebar.radio[0].set_value("Application Answers").run(timeout=30)
    assert not app.exception
    answer_expanders = [
        expander for expander in app.expander if "Autofill enabled" in expander.label
    ]
    assert len(answer_expanders) == 20
    refresh = next(button for button in app.button if button.label == "Recalculate readiness")
    refresh.click().run(timeout=30)
    assert not app.exception
    assert (
        len([expander for expander in app.expander if "Autofill enabled" in expander.label]) == 20
    )


def test_verified_answers_override_unverified_legacy_profile_values(tmp_path: Path) -> None:
    with get_connection(tmp_path / "effective-profile.db") as connection:
        apply_approved_defaults(connection)
        connection.execute(
            """
            INSERT INTO canonical_facts
              (category, field_name, canonical_value, source_documents,
               verification_status, sensitivity, autofill_permission,
               applicable_tracks, conflicting_values, derived_from, supersedes,
               created_at, updated_at)
            VALUES
              ('contact_information', 'email', 'old@example.test', '[]',
               'unverified', 'ordinary', 0, '[]', '[]', '[]', '[]', 'now', 'now'),
              ('contact_information', 'phone', '000', '[]',
               'conflicted', 'ordinary', 0, '[]', '[]', '[]', '[]', 'now', 'now')
            """
        )
        profile = resolve_effective_profile(connection)

    assert profile.fields["email"].source_table == "application_answers"
    assert profile.fields["email"].effective_value == "dmichaelcunningham@gmail.com"
    assert profile.fields["phone"].source_table == "application_answers"
    conditions = {condition.name: condition for condition in profile.readiness.conditions}
    assert conditions["Email and phone"].passed is True
    assert conditions["Current city/state"].passed is True
    assert conditions["Work authorization and sponsorship"].passed is True


def test_publication_readiness_remains_canonical_only(tmp_path: Path) -> None:
    with get_connection(tmp_path / "publication-readiness.db") as connection:
        apply_approved_defaults(connection)
        connection.execute(
            """
            INSERT INTO canonical_facts
              (category, field_name, canonical_value, source_documents,
               verification_status, sensitivity, autofill_permission,
               applicable_tracks, conflicting_values, derived_from, supersedes,
               created_at, updated_at)
            VALUES ('publications', 'citation', 'Unresolved citation', '[]',
                    'conflicted', 'ordinary', 0, '[]', '[]', '[]', '[]', 'now', 'now')
            """
        )
        profile = resolve_effective_profile(connection)

    publication = next(
        condition for condition in profile.readiness.conditions if condition.name == "Publications"
    )
    assert publication.passed is False
    assert publication.reason == "Publication conflicts or proposals remain unresolved"
