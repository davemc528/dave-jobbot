from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(slots=True)
class AnswerRecord:
    question_pattern: str
    answer: str
    source: str | None = None
    verification_status: str = "unverified"
    last_reviewed_date: str | None = None
    sensitivity_level: str = "low"
    may_autofill: bool = False


@dataclass(slots=True)
class PreparedAnswer:
    question_pattern: str
    answer: str
    source: str | None
    verification_status: str
    last_reviewed_date: str | None
    sensitivity_level: str
    may_autofill: bool


def prepare_screening_answer(answer: AnswerRecord) -> PreparedAnswer:
    if (
        answer.sensitivity_level in {"high", "restricted"}
        and answer.verification_status != "verified"
    ):
        return PreparedAnswer(
            question_pattern=answer.question_pattern,
            answer=answer.answer,
            source=answer.source,
            verification_status=answer.verification_status,
            last_reviewed_date=answer.last_reviewed_date or datetime.now(timezone.utc).isoformat(),
            sensitivity_level=answer.sensitivity_level,
            may_autofill=False,
        )
    return PreparedAnswer(
        question_pattern=answer.question_pattern,
        answer=answer.answer,
        source=answer.source,
        verification_status=answer.verification_status,
        last_reviewed_date=answer.last_reviewed_date or datetime.now(timezone.utc).isoformat(),
        sensitivity_level=answer.sensitivity_level,
        may_autofill=bool(answer.may_autofill),
    )
