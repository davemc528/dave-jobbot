import sqlite3
from pathlib import Path

import pytest

from jobbot.browser.automation import automatic_run_readiness, resume_run, run_automation
from jobbot.db import initialize_schema
from jobbot.profile.application_answers import (
    QuestionContext,
    answer_map,
    apply_approved_defaults,
    effective_answer_state,
    list_answers,
    match_question,
)


def database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    apply_approved_defaults(connection)
    return connection


def match(
    connection: sqlite3.Connection,
    question: str,
    *,
    input_type: str = "text",
    options: list[str] | None = None,
    context: QuestionContext | None = None,
):
    return match_question(
        question,
        input_type=input_type,
        options=options,
        context=context,
        answers=answer_map(connection),
    )


def test_approved_values_have_explicit_verification_and_audit() -> None:
    connection = database()
    assert connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version='002_phase_1_6_verified_application_answers'"
    ).fetchone()
    answers = answer_map(connection)
    for field in (
        "preferred_name",
        "email",
        "phone",
        "current_location",
        "linkedin_url",
        "legally_authorized_to_work",
        "sponsorship_required",
        "needs_employment_authorization_assistance",
        "noncompete_restriction",
    ):
        assert answers[field].verification_status == "verified"
        assert answers[field].verification_method == "explicit_user_instruction"
        assert answers[field].date_verified
        assert answers[field].autofill_permission is True
    assert answers["legally_authorized_to_work"].canonical_value == "Yes"
    assert answers["legally_authorized_to_work"].normalized_value == "true"
    assert answers["sponsorship_required"].canonical_value == "No"
    assert answers["needs_employment_authorization_assistance"].canonical_value == "No"
    assert (
        connection.execute(
            "SELECT count(*) FROM profile_audit_log "
            "WHERE action IN ('approved_default_inserted','approved_default_updated')"
        ).fetchone()[0]
        == 20
    )


def test_contact_formatting_and_identity_matching() -> None:
    connection = database()
    assert match(connection, "Preferred name").proposed_answer == "Dave"
    assert match(connection, "Email address").proposed_answer == "dmichaelcunningham@gmail.com"
    assert match(connection, "Telephone", input_type="tel").proposed_answer == "(860) 558-4902"
    assert match(connection, "Phone", input_type="number").proposed_answer == "8605584902"
    assert match(connection, "Current location").proposed_answer == "Houston, TX"
    assert match(connection, "LinkedIn profile").proposed_answer is not None


def test_three_employment_eligibility_semantics_remain_separate() -> None:
    connection = database()
    sponsorship = match(connection, "Will you now or in the future require visa sponsorship?")
    assert sponsorship.category == "requires_sponsorship"
    assert sponsorship.proposed_answer == "No"
    assert sponsorship.autofill_permitted is True
    authorization = match(connection, "Are you legally authorized to work in the United States?")
    assert authorization.category == "legally_authorized_to_work"
    assert authorization.proposed_answer == "Yes"
    assert authorization.autofill_permitted is True
    assistance = match(
        connection,
        "Will you require the employer to obtain employment authorization on your behalf?",
    )
    assert assistance.category == "needs_employment_authorization_assistance"
    assert assistance.proposed_answer == "No"
    assert assistance.autofill_permitted is True
    outside = match(
        connection,
        "Are you legally authorized to work in Canada?",
        context=QuestionContext(posting_country="Canada"),
    )
    assert outside.autofill_permitted is False
    assert outside.review_type == "authorization_outside_verified_scope"


def test_relocation_workplace_and_travel_rules() -> None:
    connection = database()
    context = QuestionContext(posting_locations=["San Diego, CA"])
    destination = match(connection, "Preferred relocation destinations", context=context)
    assert destination.proposed_answer == "San Diego, CA"
    assert (
        match(connection, "Are you willing to relocate?", context=context).proposed_answer == "Yes"
    )
    for workplace in ("On-site", "Remote", "Hybrid"):
        result = match(connection, f"{workplace} workplace preference", input_type="checkbox")
        assert result.proposed_answer == "Yes"
    assert match(connection, "Can you travel up to 50%?").proposed_answer == "Yes"
    over = match(connection, "This role requires 75% travel")
    assert over.autofill_permitted is False
    assert over.review_type == "travel_above_maximum"
    assert match(connection, "Travel percentage", input_type="number").proposed_answer == "60"


def test_compensation_availability_and_previous_employment_rules() -> None:
    connection = database()
    assert match(connection, "Desired compensation").proposed_answer == "Market competitive"
    numeric = match(connection, "Minimum salary amount", input_type="number")
    assert numeric.proposed_answer is None
    assert numeric.review_type == "required_numeric_compensation"
    assert match(connection, "When can you start?").proposed_answer == "Immediately"
    date = match(connection, "Earliest start date", input_type="date")
    assert date.review_type == "required_calendar_start_date"
    assert match(connection, "Notice period").proposed_answer == "2 weeks"
    baylor = QuestionContext(employer="BCM")
    other = QuestionContext(employer="Acme Biotech")
    unknown = QuestionContext()
    question = "Have you previously worked for this employer?"
    assert match(connection, question, context=baylor).proposed_answer == "Yes"
    assert match(connection, question, context=other).proposed_answer == "No"
    assert match(connection, question, context=unknown).review_type == "unknown_employer_identity"
    assert match(connection, "Are you subject to a noncompete?").proposed_answer == "No"


@pytest.mark.parametrize(
    "category",
    [
        "Gender",
        "Race or ethnicity",
        "Veteran status",
        "Disability status",
        "Sexual orientation",
        "Gender identity",
    ],
)
def test_eeo_selects_decline_only_when_available(category: str) -> None:
    connection = database()
    result = match(
        connection,
        f"Optional {category}",
        options=["Yes", "No", "Decline to self-identify"],
    )
    assert result.proposed_answer == "Decline to self-identify"
    assert result.autofill_permitted is True
    missing = match(connection, f"Required {category}", options=["Yes", "No"])
    assert missing.proposed_answer is None
    assert missing.review_type == "missing_eeo_decline_option"


@pytest.mark.parametrize(
    ("fixture", "provider"),
    [
        ("recaptcha_form.html", "recaptcha"),
        ("hcaptcha_form.html", "hcaptcha"),
        ("turnstile_form.html", "cloudflare_turnstile"),
    ],
)
def test_captcha_variants_block_and_create_resumable_run(fixture: str, provider: str) -> None:
    connection = database()
    url = Path(f"tests/fixtures/{fixture}").resolve().as_uri()
    result = run_automation(connection, url, mode="automatic_dry_run", visible=False)
    assert result.status == "blocked_by_captcha"
    assert result.captcha.provider == provider
    assert result.filled == []
    assert result.resume_command
    assert result.screenshot_path
    review = connection.execute(
        "SELECT item_type, recommended_action FROM review_items ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert review["item_type"] == "human_intervention_required"
    assert "manually" in review["recommended_action"]
    with pytest.raises(AssertionError):
        # The fixture still contains the challenge, so resuming cannot bypass it.
        assert (
            resume_run(connection, int(result.run_id), visible=False).status != "blocked_by_captcha"
        )


def test_automatic_dry_run_fills_only_verified_high_confidence_and_stops() -> None:
    connection = database()
    url = Path("tests/fixtures/oncology_msl_application.html").resolve().as_uri()
    result = run_automation(
        connection,
        url,
        mode="automatic_dry_run",
        visible=False,
        context=QuestionContext(
            posting_locations=["San Diego, CA"],
            employer="Baylor College of Medicine",
        ),
    )
    filled = {item["field"] for item in result.filled}
    assert {
        "preferred_name",
        "email",
        "phone",
        "linkedin_url",
        "requires_sponsorship",
        "legal_authorization",
        "authorization_assistance",
    } <= filled
    assert "numeric_compensation" not in filled
    assert "start_date" not in filled
    assert result.status == "stopped_before_submit"
    assert result.stopped_before_submit is True
    assert connection.execute("SELECT count(*) FROM review_items").fetchone()[0] > 0
    assert (
        connection.execute(
            "SELECT count(*) FROM profile_audit_log WHERE action='automation_field_filled'"
        ).fetchone()[0]
        > 0
    )
    serialized = connection.execute(
        "SELECT state_json FROM automation_runs WHERE id=?", (result.run_id,)
    ).fetchone()["state_json"]
    assert "dmichaelcunningham@gmail.com" not in serialized
    assert "8605584902" not in serialized


def test_automatic_submit_is_unimplemented() -> None:
    connection = database()
    url = Path("tests/fixtures/generic_form.html").resolve().as_uri()
    with pytest.raises(NotImplementedError, match="disabled and unimplemented"):
        run_automation(connection, url, mode="automatic_submit", visible=False)


def test_edge_fixture_non_baylor_and_excess_travel() -> None:
    connection = database()
    url = Path("tests/fixtures/phase16_edge_cases.html").resolve().as_uri()
    result = run_automation(
        connection,
        url,
        mode="automatic_dry_run",
        visible=False,
        context=QuestionContext(employer="Acme Biotech"),
    )
    filled = {item["field"]: item["value"] for item in result.filled}
    assert filled["previous_acme"] == "No"
    assert "travel_75" not in filled
    assert any(item["reason"] == "travel_above_maximum" for item in result.withheld)
    assert result.stopped_before_submit is True


def test_required_eeo_fixture_without_decline_creates_review() -> None:
    connection = database()
    url = Path("tests/fixtures/required_eeo_form.html").resolve().as_uri()
    result = run_automation(connection, url, mode="automatic_dry_run", visible=False)
    assert "disability" not in {item["field"] for item in result.filled}
    assert any(item["reason"] == "missing_eeo_decline_option" for item in result.withheld)
    assert result.stopped_before_submit is True


def test_automatic_readiness_accepts_verified_us_authorization_and_sponsorship() -> None:
    connection = database()
    sponsorship = match(connection, "Will you now or in the future require visa sponsorship?")
    resume = Path("tests/fixtures/generic_form.html").resolve()
    ready = automatic_run_readiness(
        required_matches=[sponsorship],
        selected_resume=str(resume),
    )
    assert ready.ready is True
    legal = match(connection, "Are you legally authorized to work in the United States?")
    legal_ready = automatic_run_readiness(
        required_matches=[legal],
        selected_resume=str(resume),
    )
    assert legal_ready.ready is True


def test_defaults_are_idempotent_and_effective_status_is_shared() -> None:
    connection = database()
    before = connection.execute(
        "SELECT count(*) FROM application_answers WHERE active=1"
    ).fetchone()[0]
    result = apply_approved_defaults(connection)
    after = connection.execute(
        "SELECT count(*) FROM application_answers WHERE active=1"
    ).fetchone()[0]
    assert before == after == 20
    assert result.inserted == 0
    assert result.updated == 0
    assert result.unchanged == 20
    assert not connection.execute(
        """
        SELECT field_name FROM application_answers
        WHERE active=1 GROUP BY field_name HAVING count(*) > 1
        """
    ).fetchall()
    for answer in list_answers(connection):
        state = effective_answer_state(answer)
        assert state.status == "Autofill enabled", (
            f"{answer.field_name} incorrectly displayed {state.status}: {state.explanation}"
        )
    email = answer_map(connection)["email"]
    assert effective_answer_state(email).eligible is True
    assert match(connection, "Email address").autofill_permitted is True
    assert email.sensitivity == "sensitive"


def test_stale_ambiguous_authorization_is_superseded_not_selected() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    connection.execute(
        """
        INSERT INTO application_answers
          (field_name, canonical_value, display_value, raw_value, verification_status,
           verification_method, sensitivity, autofill_permission, question_categories,
           date_verified, review_notes, updated_at)
        VALUES ('work_authorization_user_response', 'No', 'No', 'No', 'needs_edit',
                'explicit_user_instruction', 'sensitive', 0,
                '["legally_authorized_to_work"]', NULL, 'old ambiguity', 'old')
        """
    )
    result = apply_approved_defaults(connection)
    stale = connection.execute(
        """
        SELECT active, superseded_by FROM application_answers
        WHERE field_name='work_authorization_user_response'
        """
    ).fetchone()
    assert result.superseded == 1
    assert stale["active"] == 0
    assert stale["superseded_by"] == "legally_authorized_to_work"
    assert "work_authorization_user_response" not in answer_map(connection)
