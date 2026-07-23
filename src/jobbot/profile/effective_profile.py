from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from pydantic import BaseModel, Field

from jobbot.profile.application_answers import (
    ApplicationAnswer,
    effective_answer_state,
    list_answers,
)
from jobbot.profile.canonical import CanonicalFact, list_canonical_facts


class EffectiveProfileField(BaseModel):
    logical_field: str
    effective_value: str | None = None
    verification_status: str = "missing"
    autofill_permission: bool = False
    source_table: str | None = None
    source_record_id: int | None = None
    verification_method: str | None = None
    active: bool = False
    superseded: bool = False
    conflict_status: str = "none"
    readiness_eligible: bool = False
    explanation: str


class ReadinessCondition(BaseModel):
    name: str
    passed: bool
    effective_values: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    verification_statuses: list[str] = Field(default_factory=list)
    reason: str
    review_hint: str


class EffectiveReadinessReport(BaseModel):
    ready: bool
    conditions: list[ReadinessCondition]

    @property
    def failures(self) -> list[str]:
        return [condition.reason for condition in self.conditions if not condition.passed]


class EffectiveProfile(BaseModel):
    fields: dict[str, EffectiveProfileField]
    readiness: EffectiveReadinessReport


ANSWER_ALIASES = {
    "sponsorship_required": "requires_sponsorship",
    "current_location": "current_location",
}

CANONICAL_ALIASES = {
    ("identity", "name"): "name",
    ("contact_information", "email"): "email",
    ("contact_information", "phone"): "phone",
    ("contact_information", "current_city"): "current_city",
    ("contact_information", "current_state"): "current_state",
    ("contact_information", "current_location"): "current_location",
}

INTAKE_ALIASES = {
    "current_city_state": "current_location",
    "work_authorization": "legally_authorized_to_work",
    "sponsorship_requirement": "requires_sponsorship",
}


def _answer_field(answer: ApplicationAnswer) -> EffectiveProfileField:
    state = effective_answer_state(answer)
    return EffectiveProfileField(
        logical_field=ANSWER_ALIASES.get(answer.field_name, answer.field_name),
        effective_value=answer.display_value or answer.canonical_value,
        verification_status=answer.verification_status,
        autofill_permission=answer.autofill_permission,
        source_table="application_answers",
        source_record_id=answer.id,
        verification_method=answer.verification_method,
        active=answer.active,
        superseded=not answer.active,
        conflict_status=(
            "unresolved"
            if answer.review_required or answer.verification_status in {"conflicted", "needs_edit"}
            else "none"
        ),
        readiness_eligible=answer.active and answer.verification_status == "verified",
        explanation=state.explanation,
    )


def _canonical_field(logical: str, fact: CanonicalFact) -> EffectiveProfileField:
    superseded = not fact.active or fact.superseded_by is not None
    verified = fact.verification_status in {"verified", "derived"}
    conflict = fact.verification_status in {"conflicted", "needs_edit"}
    return EffectiveProfileField(
        logical_field=logical,
        effective_value=fact.display_value or fact.canonical_value,
        verification_status=fact.verification_status,
        autofill_permission=fact.autofill_permission,
        source_table="canonical_facts",
        source_record_id=fact.id,
        verification_method=fact.verification_method,
        active=not superseded,
        superseded=superseded,
        conflict_status="unresolved" if conflict else "none",
        readiness_eligible=not superseded and verified and not conflict,
        explanation=(
            "Verified canonical fact"
            if verified and not conflict
            else "Canonical fact is not verified for readiness"
        ),
    )


def _priority(field: EffectiveProfileField) -> tuple[int, int]:
    if not field.active or field.superseded:
        return (0, field.source_record_id or 0)
    if field.source_table == "application_answers" and field.verification_status == "verified":
        return (5, field.source_record_id or 0)
    if field.source_table == "canonical_facts" and field.verification_status == "verified":
        return (4, field.source_record_id or 0)
    if field.source_table == "canonical_facts" and field.verification_status == "derived":
        return (3, field.source_record_id or 0)
    if field.source_table == "profile_intake" and field.verification_status in {
        "verified",
        "restricted",
    }:
        return (2, field.source_record_id or 0)
    return (1, field.source_record_id or 0)


def _select(candidates: Iterable[EffectiveProfileField]) -> EffectiveProfileField | None:
    values = [field for field in candidates if field.active and not field.superseded]
    return max(values, key=_priority) if values else None


def _condition(
    name: str,
    passed: bool,
    examined: list[EffectiveProfileField],
    reason: str,
    hint: str,
) -> ReadinessCondition:
    return ReadinessCondition(
        name=name,
        passed=passed,
        effective_values=[
            f"{field.logical_field}={field.effective_value or 'missing'}" for field in examined
        ],
        sources=[
            f"{field.source_table or 'none'}#{field.source_record_id or '-'}" for field in examined
        ],
        verification_statuses=[field.verification_status for field in examined],
        reason=reason,
        review_hint=hint,
    )


def resolve_effective_profile(connection: sqlite3.Connection) -> EffectiveProfile:
    candidates: dict[str, list[EffectiveProfileField]] = {}
    for answer in list_answers(connection, include_inactive=True):
        field = _answer_field(answer)
        candidates.setdefault(field.logical_field, []).append(field)
    facts = list_canonical_facts(connection)
    for fact in facts:
        logical = CANONICAL_ALIASES.get((fact.category, fact.field_name))
        if logical:
            candidates.setdefault(logical, []).append(_canonical_field(logical, fact))
    for row in connection.execute("SELECT * FROM profile_intake"):
        logical = INTAKE_ALIASES.get(row["field_name"], row["field_name"])
        manual = bool(row["manual_only"])
        candidates.setdefault(logical, []).append(
            EffectiveProfileField(
                logical_field=logical,
                effective_value=row["value"],
                verification_status=row["verification_status"],
                autofill_permission=bool(row["autofill_permission"]),
                source_table="profile_intake",
                source_record_id=row["id"],
                active=True,
                readiness_eligible=row["verification_status"] == "verified" or manual,
                explanation=(
                    "Verified manual-only intake answer"
                    if manual
                    else "Legacy profile intake answer"
                ),
            )
        )
    fields = {
        logical: selected
        for logical, values in candidates.items()
        if (selected := _select(values)) is not None
    }

    def get(name: str) -> EffectiveProfileField:
        return fields.get(
            name,
            EffectiveProfileField(logical_field=name, explanation="No value found"),
        )

    conditions: list[ReadinessCondition] = []
    name = get("name")
    conditions.append(
        _condition(
            "Name",
            name.readiness_eligible,
            [name],
            "Name is not verified",
            "Review Tier 1 identity facts.",
        )
    )
    email, phone = get("email"), get("phone")
    conditions.append(
        _condition(
            "Email and phone",
            email.readiness_eligible and phone.readiness_eligible,
            [email, phone],
            "Email and phone are not verified",
            "Review Application Answers or Tier 1 contact facts.",
        )
    )
    city, state, location = get("current_city"), get("current_state"), get("current_location")
    normalized_location = (location.effective_value or "").replace(" ", "")
    location_ready = location.readiness_eligible and "," in normalized_location
    conditions.append(
        _condition(
            "Current city/state",
            (city.readiness_eligible and state.readiness_eligible) or location_ready,
            [city, state, location],
            "Current city/state is not verified",
            "Review Application Answers contact fields.",
        )
    )
    employment_ready = any(
        fact.category == "employment_history" and fact.verification_status == "verified"
        for fact in facts
    )
    conditions.append(
        _condition(
            "Employment history",
            employment_ready,
            [],
            "Employment organizations, titles, and dates are not verified",
            "Review Tier 1 employment facts.",
        )
    )
    education_ready = any(
        fact.category == "education" and fact.verification_status == "verified" for fact in facts
    )
    conditions.append(
        _condition(
            "Education",
            education_ready,
            [],
            "Education is not verified",
            "Review Tier 1 education facts.",
        )
    )
    track_ready = any(
        fact.category == "document_track" and fact.verification_status == "verified"
        for fact in facts
    )
    conditions.append(
        _condition(
            "Resume track",
            track_ready,
            [],
            "No resume track is approved",
            "Review Tier 1 document-track facts.",
        )
    )
    unresolved_publications = [
        fact
        for fact in facts
        if fact.category == "publications"
        and fact.active
        and fact.verification_status in {"unverified", "conflicted", "needs_edit"}
        and not (fact.review_notes or "").startswith("Superseded by canonical fact")
    ]
    conditions.append(
        _condition(
            "Publications",
            not unresolved_publications,
            [],
            "Publication conflicts or proposals remain unresolved",
            "Review Tier 1 publication proposals and conflicts.",
        )
    )
    authorization, sponsorship = (
        get("legally_authorized_to_work"),
        get("requires_sponsorship"),
    )
    auth_value = (authorization.effective_value or "").casefold()
    sponsorship_value = (sponsorship.effective_value or "").casefold()
    eligibility_ready = (
        authorization.readiness_eligible
        and sponsorship.readiness_eligible
        and (
            (auth_value == "yes" and sponsorship_value == "no")
            or (
                authorization.source_table == "profile_intake"
                and sponsorship.source_table == "profile_intake"
                and authorization.verification_status == "restricted"
                and sponsorship.verification_status == "restricted"
            )
        )
    )
    conditions.append(
        _condition(
            "Work authorization and sponsorship",
            eligibility_ready,
            [authorization, sponsorship],
            "Work authorization and sponsorship must be verified or manual-only",
            "Review Application Answers employment eligibility fields.",
        )
    )
    conflict_ready = not any(
        fact.verification_status == "conflicted" and fact.autofill_permission
        for fact in facts
        if fact.active
    )
    conditions.append(
        _condition(
            "Autofill conflicts",
            conflict_ready,
            [],
            "An autofill-enabled field has an unresolved conflict",
            "Resolve the conflicted canonical fact.",
        )
    )
    patents_ready = not any(
        fact.category == "patents_and_intellectual_property"
        and (fact.sensitivity != "restricted" or fact.autofill_permission)
        for fact in facts
    )
    conditions.append(
        _condition(
            "Patent restrictions",
            patents_ready,
            [],
            "Patent data is not fully restricted",
            "Review Tier 3 restricted facts.",
        )
    )
    safety_ready = bool(facts)
    conditions.append(
        _condition(
            "Stop-before-submit safety",
            safety_ready,
            [],
            "Stop-before-submit safety has not been initialized",
            "Initialize the canonical profile; final submission remains disabled.",
        )
    )
    readiness = EffectiveReadinessReport(
        ready=all(condition.passed for condition in conditions),
        conditions=conditions,
    )
    return EffectiveProfile(fields=fields, readiness=readiness)


def effective_application_answer_map(
    connection: sqlite3.Connection,
) -> dict[str, ApplicationAnswer]:
    answers = {answer.id: answer for answer in list_answers(connection, include_inactive=True)}
    profile = resolve_effective_profile(connection)
    result: dict[str, ApplicationAnswer] = {}
    for logical, field in profile.fields.items():
        if field.source_table == "application_answers" and field.source_record_id in answers:
            result[logical] = answers[field.source_record_id]
    # Preserve application-specific aliases/categories expected by the matcher.
    for answer in answers.values():
        if answer.active:
            result.setdefault(answer.field_name, answer)
    return result
