from pathlib import Path

from jobbot.browser.playwright_mvp import dry_run_autofill


def test_captcha_requires_manual_intervention() -> None:
    url = Path("tests/fixtures/captcha_form.html").resolve().as_uri()
    result = dry_run_autofill(url, visible=False)
    assert result["captcha_detected"] is True
    assert result["status"] == "manual_intervention"
    assert result["actions"] == []


def test_only_verified_values_are_candidates_for_autofill() -> None:
    url = Path("tests/fixtures/generic_form.html").resolve().as_uri()
    result = dry_run_autofill(
        url,
        {"first_name": "Verified", "email": "unverified@example.test"},
        verified_fields={"first_name"},
        visible=False,
    )
    statuses = {action["field"]: action["status"] for action in result["actions"]}
    assert statuses["first_name"] == "would_fill"
    assert statuses["email"] == "needs_review"
