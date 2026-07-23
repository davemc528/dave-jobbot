from pathlib import Path

from streamlit.testing.v1 import AppTest

from jobbot.config import resolve_db_path
from jobbot.db import get_connection
from jobbot.profile.application_answers import apply_approved_defaults


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
    assert len(app.expander) == 20
    assert all("Autofill enabled" in expander.label for expander in app.expander)
    refresh = next(button for button in app.button if button.label == "Refresh database state")
    refresh.click().run(timeout=30)
    assert not app.exception
    assert len(app.expander) == 20
