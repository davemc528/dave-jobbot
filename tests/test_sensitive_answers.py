from jobbot.profile.answers import AnswerRecord, prepare_screening_answer


def test_sensitive_unverified_answers_are_not_autofilled() -> None:
    answer = AnswerRecord(
        question_pattern="Do you require sponsorship?",
        answer="Unknown",
        source="Candidate profile",
        verification_status="unverified",
        last_reviewed_date="2026-07-23",
        sensitivity_level="high",
        may_autofill=False,
    )

    prepared = prepare_screening_answer(answer)

    assert prepared.may_autofill is False
    assert prepared.answer == "Unknown"
