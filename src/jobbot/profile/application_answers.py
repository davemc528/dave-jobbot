from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ApplicationAnswer(BaseModel):
    id: int | None = None
    field_name: str
    canonical_value: str | None
    display_value: str | None
    raw_value: str | None
    verification_status: str
    verification_method: str | None
    sensitivity: str
    autofill_permission: bool
    question_categories: list[str] = Field(default_factory=list)
    date_verified: str | None
    review_notes: str | None


class QuestionContext(BaseModel):
    posting_locations: list[str] = Field(default_factory=list)
    employer: str | None = None
    travel_percentage: int | None = None


class QuestionMatch(BaseModel):
    category: str
    proposed_answer: str | None
    confidence: float
    rationale: str
    source_fact: str | None
    autofill_permitted: bool
    review_required: bool
    sensitivity: str
    alternative_interpretations: list[str] = Field(default_factory=list)
    review_type: str | None = None
    recommended_action: str | None = None


APPROVED_DEFAULTS: tuple[dict[str, object], ...] = (
    {
        "field_name": "preferred_name",
        "canonical_value": "Dave",
        "display_value": "Dave",
        "sensitivity": "ordinary",
        "categories": ["preferred_name", "first_name"],
    },
    {
        "field_name": "email",
        "canonical_value": "dmichaelcunningham@gmail.com",
        "display_value": "dmichaelcunningham@gmail.com",
        "sensitivity": "sensitive",
        "categories": ["email"],
    },
    {
        "field_name": "phone",
        "canonical_value": "8605584902",
        "display_value": "(860) 558-4902",
        "raw_value": "8605584902",
        "sensitivity": "sensitive",
        "categories": ["phone"],
    },
    {
        "field_name": "current_city",
        "canonical_value": "Houston",
        "display_value": "Houston",
        "sensitivity": "ordinary",
        "categories": ["current_city"],
    },
    {
        "field_name": "current_state",
        "canonical_value": "TX",
        "display_value": "TX",
        "sensitivity": "ordinary",
        "categories": ["current_state"],
    },
    {
        "field_name": "current_location",
        "canonical_value": "Houston, TX",
        "display_value": "Houston, TX",
        "sensitivity": "ordinary",
        "categories": ["current_location", "city_state"],
    },
    {
        "field_name": "linkedin_url",
        "canonical_value": "https://www.linkedin.com/in/david-cunningham-1524ab49/",
        "display_value": "https://www.linkedin.com/in/david-cunningham-1524ab49/",
        "sensitivity": "ordinary",
        "categories": ["linkedin_url"],
    },
    {
        "field_name": "sponsorship_required",
        "canonical_value": "No",
        "display_value": "No",
        "sensitivity": "sensitive",
        "categories": ["requires_sponsorship", "needs_employment_authorization_assistance"],
    },
    {
        "field_name": "noncompete_restriction",
        "canonical_value": "No",
        "display_value": "No",
        "sensitivity": "sensitive",
        "categories": ["noncompete"],
    },
    {
        "field_name": "willing_to_relocate",
        "canonical_value": "Yes",
        "display_value": "Yes",
        "sensitivity": "ordinary",
        "categories": ["willing_to_relocate"],
    },
    {
        "field_name": "approved_relocation_destinations",
        "canonical_value": "Any location required by the posting",
        "display_value": "Any location required by the posting",
        "sensitivity": "ordinary",
        "categories": ["relocation_destinations"],
    },
    {
        "field_name": "maximum_travel_percentage",
        "canonical_value": "60",
        "display_value": "60%",
        "sensitivity": "ordinary",
        "categories": ["travel_acceptance", "travel_percentage"],
    },
    {
        "field_name": "workplace_preferences",
        "canonical_value": "On-site|Remote|Hybrid",
        "display_value": "On-site, Remote, Hybrid",
        "sensitivity": "ordinary",
        "categories": ["workplace_preferences"],
    },
    {
        "field_name": "minimum_compensation",
        "canonical_value": "Market competitive",
        "display_value": "Market competitive",
        "sensitivity": "sensitive",
        "categories": ["desired_compensation_text"],
    },
    {
        "field_name": "earliest_start",
        "canonical_value": "Immediate",
        "display_value": "Immediately",
        "sensitivity": "ordinary",
        "categories": ["start_availability"],
    },
    {
        "field_name": "notice_period",
        "canonical_value": "2 weeks",
        "display_value": "2 weeks",
        "sensitivity": "ordinary",
        "categories": ["notice_period"],
    },
    {
        "field_name": "previous_employer",
        "canonical_value": "Baylor College of Medicine",
        "display_value": "Baylor College of Medicine",
        "sensitivity": "ordinary",
        "categories": ["previous_employment"],
    },
    {
        "field_name": "eeo_decline",
        "canonical_value": "Prefer not to answer",
        "display_value": "Prefer not to answer",
        "sensitivity": "restricted",
        "categories": ["optional_eeo"],
    },
)

WORK_AUTH_WARNING = (
    'Potential conflict: answering No to "Are you legally authorized to work in the '
    'United States?" may be disqualifying, while sponsorship_required is also No.'
)


def _audit_answer(
    connection: sqlite3.Connection, action: str, field_name: str, metadata: dict[str, object]
) -> None:
    connection.execute(
        """
        INSERT INTO profile_audit_log
          (canonical_fact_id, action, actor, before_json, after_json, notes, created_at)
        VALUES (NULL, ?, 'human', NULL, ?, ?, ?)
        """,
        (
            action,
            json.dumps({"field_name": field_name, **metadata}),
            "Explicitly supplied in Phase I.6 implementation request",
            utc_now(),
        ),
    )


def apply_approved_defaults(connection: sqlite3.Connection) -> int:
    now = utc_now()
    for item in APPROVED_DEFAULTS:
        connection.execute(
            """
            INSERT INTO application_answers
              (field_name, canonical_value, display_value, raw_value, verification_status,
               verification_method, sensitivity, autofill_permission, question_categories,
               date_verified, review_notes, updated_at)
            VALUES (?, ?, ?, ?, 'verified', 'explicit_user_instruction', ?, 1, ?, ?, ?, ?)
            ON CONFLICT(field_name) DO UPDATE SET
              canonical_value=excluded.canonical_value, display_value=excluded.display_value,
              raw_value=excluded.raw_value, verification_status=excluded.verification_status,
              verification_method=excluded.verification_method,
              sensitivity=excluded.sensitivity, autofill_permission=excluded.autofill_permission,
              question_categories=excluded.question_categories,
              date_verified=excluded.date_verified, review_notes=excluded.review_notes,
              updated_at=excluded.updated_at
            """,
            (
                item["field_name"],
                item["canonical_value"],
                item["display_value"],
                item.get("raw_value"),
                item["sensitivity"],
                json.dumps(item["categories"]),
                now,
                "Explicitly approved by the user",
                now,
            ),
        )
        _audit_answer(
            connection,
            "approved_default_applied",
            str(item["field_name"]),
            {"verification_method": "explicit_user_instruction", "autofill": True},
        )
    connection.execute(
        """
        INSERT INTO application_answers
          (field_name, canonical_value, display_value, raw_value, verification_status,
           verification_method, sensitivity, autofill_permission, question_categories,
           date_verified, review_notes, updated_at)
        VALUES ('work_authorization_user_response', 'No', 'No', 'No', 'needs_edit',
                'explicit_user_instruction', 'sensitive', 0, ?, NULL, ?, ?)
        ON CONFLICT(field_name) DO UPDATE SET
          canonical_value='No', display_value='No', raw_value='No',
          verification_status='needs_edit',
          verification_method='explicit_user_instruction', sensitivity='sensitive',
          autofill_permission=0, question_categories=excluded.question_categories,
          date_verified=NULL, review_notes=excluded.review_notes, updated_at=excluded.updated_at
        """,
        (json.dumps(["legally_authorized_to_work"]), WORK_AUTH_WARNING, now),
    )
    _audit_answer(
        connection,
        "ambiguous_default_recorded",
        "work_authorization_user_response",
        {"verification_status": "needs_edit", "autofill": False},
    )
    connection.commit()
    return len(APPROVED_DEFAULTS) + 1


def list_answers(connection: sqlite3.Connection) -> list[ApplicationAnswer]:
    rows = connection.execute("SELECT * FROM application_answers ORDER BY field_name").fetchall()
    answers: list[ApplicationAnswer] = []
    for row in rows:
        values = dict(row)
        values["autofill_permission"] = bool(values["autofill_permission"])
        values["question_categories"] = json.loads(values["question_categories"])
        values.pop("updated_at")
        answers.append(ApplicationAnswer.model_validate(values))
    return answers


def answer_map(connection: sqlite3.Connection) -> dict[str, ApplicationAnswer]:
    return {answer.field_name: answer for answer in list_answers(connection)}


def _result(
    category: str,
    answer: ApplicationAnswer | None,
    confidence: float,
    rationale: str,
    *,
    proposed: str | None = None,
    review_type: str | None = None,
    recommended_action: str | None = None,
    alternatives: list[str] | None = None,
) -> QuestionMatch:
    allowed = bool(
        answer
        and answer.verification_status == "verified"
        and answer.autofill_permission
        and review_type is None
    )
    resolved_answer = (
        None
        if review_type is not None and proposed is None
        else proposed
        if proposed is not None
        else (answer.display_value if answer else None)
    )
    return QuestionMatch(
        category=category,
        proposed_answer=resolved_answer,
        confidence=confidence,
        rationale=rationale,
        source_fact=answer.field_name if answer else None,
        autofill_permitted=allowed,
        review_required=not allowed,
        sensitivity=answer.sensitivity if answer else "ordinary",
        alternative_interpretations=alternatives or [],
        review_type=review_type,
        recommended_action=recommended_action,
    )


def match_question(
    question: str,
    *,
    input_type: str = "text",
    options: list[str] | None = None,
    context: QuestionContext | None = None,
    answers: dict[str, ApplicationAnswer],
) -> QuestionMatch:
    text = re.sub(r"\s+", " ", question).strip().casefold()
    options = options or []
    context = context or QuestionContext()

    if input_type == "file" or re.search(r"\b(resume|cv)\b", text):
        return _result(
            "resume_upload",
            answers.get("resume_path"),
            0.99,
            "Resume upload requires an explicitly approved existing file",
            review_type="upload_failure",
            recommended_action="Select and approve a resume file for this application",
        )

    simple: tuple[tuple[str, str, str], ...] = (
        (r"\b(preferred|chosen) name\b", "preferred_name", "preferred_name"),
        (r"\b(first name|given name)\b", "preferred_name", "first_name"),
        (r"\be-?mail\b", "email", "email"),
        (r"\b(phone|telephone|mobile)\b", "phone", "phone"),
        (r"\blinkedin\b", "linkedin_url", "linkedin_url"),
        (r"\b(city and state|current location)\b", "current_location", "current_location"),
        (r"\bcurrent city\b|\bcity\b", "current_city", "current_city"),
        (r"\bcurrent state\b|\bstate\b", "current_state", "current_state"),
        (r"\bnon-?compete\b", "noncompete_restriction", "noncompete"),
        (r"\bnotice period\b", "notice_period", "notice_period"),
    )
    for pattern, field_name, category in simple:
        if re.search(pattern, text):
            answer = answers.get(field_name)
            proposed = answer.display_value if answer else None
            if field_name == "phone" and input_type in {"number", "tel_raw"} and answer:
                proposed = answer.raw_value
            return _result(
                category,
                answer,
                0.99,
                f"Matched deterministic {category} pattern",
                proposed=proposed,
            )

    legal_auth = bool(
        re.search(r"\blegally authorized\b.*\b(work|employment)\b", text)
        or re.search(r"\bauthorized to work\b", text)
    )
    if legal_auth:
        return _result(
            "legally_authorized_to_work",
            answers.get("work_authorization_user_response"),
            0.99,
            "Legal-authorization wording is distinct from sponsorship",
            review_type="ambiguous_work_authorization",
            recommended_action="Confirm the intended legal-authorization answer",
            alternatives=["Yes may indicate legal authorization", "No may be disqualifying"],
        )
    if re.search(r"\b(now or in the future|future)\b.*\bsponsor", text) or re.search(
        r"\brequire\b.*\b(visa )?sponsorship\b", text
    ):
        return _result(
            "requires_sponsorship",
            answers.get("sponsorship_required"),
            0.99,
            "Matched full sponsorship semantics, not a substring alone",
        )
    if re.search(r"\b(employer|company)\b.*\b(obtain|provide)\b.*\bwork authorization\b", text):
        return _result(
            "needs_employment_authorization_assistance",
            answers.get("sponsorship_required"),
            0.96,
            "Matched employer-assistance authorization semantics",
        )

    if "relocat" in text:
        if re.search(r"\b(where|destinations?|locations?)\b", text):
            locations = context.posting_locations
            proposed = ", ".join(locations) if locations else "Open to relocation"
            return _result(
                "relocation_destinations",
                answers.get("approved_relocation_destinations"),
                0.97,
                "Uses only locations stated in the posting",
                proposed=proposed,
            )
        return _result(
            "willing_to_relocate",
            answers.get("willing_to_relocate"),
            0.98,
            "Matched willingness-to-relocate semantics",
        )

    if "travel" in text:
        percent_match = re.search(r"(\d{1,3})\s*%", text)
        required = int(percent_match.group(1)) if percent_match else context.travel_percentage
        answer = answers.get("maximum_travel_percentage")
        if required is not None and required > 60:
            return _result(
                "travel_acceptance",
                answer,
                0.99,
                f"Required travel {required}% exceeds approved maximum 60%",
                review_type="travel_above_maximum",
                recommended_action="Ask the user whether to accept travel above 60%",
            )
        proposed = "60" if input_type == "number" else ("60%" if "percent" in text else "Yes")
        return _result(
            "travel_percentage" if input_type == "number" else "travel_acceptance",
            answer,
            0.97,
            "Travel requirement is at or below the approved 60% maximum",
            proposed=proposed,
        )

    if re.search(r"\b(compensation|salary|pay)\b", text):
        answer = answers.get("minimum_compensation")
        if input_type in {"number", "range"} or re.search(
            r"\b(numeric|amount|minimum salary)\b", text
        ):
            return _result(
                "numeric_compensation",
                answer,
                0.99,
                "Numeric compensation requires a number that has not been approved",
                proposed=None,
                review_type="required_numeric_compensation",
                recommended_action="Provide an explicit numeric amount or complete manually",
            )
        if options:
            equivalents = ("market competitive", "negotiable", "competitive")
            chosen = next(
                (option for option in options if option.casefold() in equivalents),
                None,
            )
            if not chosen:
                return _result(
                    "desired_compensation_text",
                    answer,
                    0.85,
                    "No semantically equivalent compensation option was found",
                    review_type="low_confidence_question_match",
                    recommended_action="Choose a compensation option manually",
                )
            return _result(
                "desired_compensation_text",
                answer,
                0.96,
                "Selected an approved semantic equivalent",
                proposed=chosen,
            )
        return _result(
            "desired_compensation_text",
            answer,
            0.98,
            "Matched free-text compensation question",
        )

    if re.search(r"\b(when can you start|availability|earliest start)\b", text):
        answer = answers.get("earliest_start")
        if input_type == "date":
            return _result(
                "start_date",
                answer,
                0.99,
                "A calendar date was required but no explicit date is configured",
                proposed=None,
                review_type="required_calendar_start_date",
                recommended_action="Choose a calendar date manually",
            )
        return _result(
            "start_availability",
            answer,
            0.98,
            "Matched free-text start availability",
        )

    if re.search(r"\b(previously|ever)\b.*\b(worked|employed)\b", text):
        answer = answers.get("previous_employer")
        employer = context.employer
        if not employer:
            return _result(
                "previous_employment",
                answer,
                0.7,
                "Employer identity is unknown",
                proposed=None,
                review_type="unknown_employer_identity",
                recommended_action="Confirm the employer named by the form",
            )
        normalized = employer.casefold().replace(".", "")
        proposed = "Yes" if normalized in {"baylor college of medicine", "bcm"} else "No"
        return _result(
            "previous_employment",
            answer,
            0.98,
            f"Employer identity was confidently resolved as {employer}",
            proposed=proposed,
        )

    eeo_terms = (
        "eeo",
        "gender",
        "race",
        "ethnicity",
        "veteran",
        "disability",
        "sexual orientation",
        "gender identity",
        "self-identification",
    )
    if any(term in text for term in eeo_terms):
        answer = answers.get("eeo_decline")
        decline_terms = ("prefer not", "do not wish", "decline", "not disclose")
        decline = next(
            (
                option
                for option in options
                if any(term in option.casefold() for term in decline_terms)
            ),
            None,
        )
        if decline:
            return _result(
                "optional_eeo",
                answer,
                0.99,
                "Optional EEO question provides a nondisclosure option",
                proposed=decline,
            )
        return _result(
            "optional_eeo",
            answer,
            0.99,
            "No nondisclosure option is available",
            proposed=None,
            review_type="missing_eeo_decline_option",
            recommended_action="Leave blank if permitted; otherwise complete manually",
        )

    if "workplace" in text or any(
        term in text for term in ("on-site", "onsite", "remote", "hybrid")
    ):
        proposed = (
            "Yes"
            if input_type in {"checkbox", "radio"}
            and any(term in text for term in ("on-site", "onsite", "remote", "hybrid"))
            else "On-site|Remote|Hybrid"
        )
        return _result(
            "workplace_preferences",
            answers.get("workplace_preferences"),
            0.96,
            "Matched workplace preference choices",
            proposed=proposed,
        )

    return _result(
        "unknown",
        None,
        0.0,
        "No deterministic semantic category matched",
        review_type="low_confidence_question_match",
        recommended_action="Classify and answer manually",
    )
