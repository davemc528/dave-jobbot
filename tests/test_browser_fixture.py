from pathlib import Path

from jobbot.browser.playwright_mvp import inspect_form, dry_run_autofill


def test_fixture_inspect_and_dry_run(tmp_path: Path) -> None:
    fixture = Path("tests/fixtures/generic_form.html").resolve()
    url = fixture.as_uri()

    inspected = inspect_form(url, visible=False)
    assert any(field["name"] == "first_name" for field in inspected["fields"])

    dry_run = dry_run_autofill(url, visible=False)
    assert dry_run["mode"] == "dry-run"
    assert dry_run["actions"]
    assert dry_run["stopped_before_submit"] is True


def test_all_ats_fixtures_stop_before_submit() -> None:
    for name in ("generic", "greenhouse", "lever", "workday"):
        url = Path(f"tests/fixtures/{name}_form.html").resolve().as_uri()
        result = dry_run_autofill(url, visible=False)
        assert result["status"] == "stopped_before_submit"
        assert result["submit_controls"]
