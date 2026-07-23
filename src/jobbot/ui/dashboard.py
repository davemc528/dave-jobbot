from __future__ import annotations

from collections import Counter

import streamlit as st

from jobbot.db import get_connection
from jobbot.config import AUTOMATION
from jobbot.profile.application_answers import WORK_AUTH_WARNING, list_answers, utc_now
from jobbot.profile.canonical import (
    INTAKE_FIELDS,
    apply_canonical_proposals,
    batch_approve,
    list_canonical_facts,
    mark_sensitive_manual_only,
    merge_facts,
    missing_required_fields,
    save_intake_answer,
    seed_phase_15_proposals,
    split_fact,
    update_fact,
)

CATEGORY_LABELS = {
    "identity": "Identity and contact",
    "contact_information": "Identity and contact",
    "employment_history": "Employment",
    "education": "Education",
    "teaching_experience": "Teaching",
    "technical_skills": "Technical skills",
    "publications": "Publications",
    "patents_and_intellectual_property": "Patents and intellectual property",
    "geography": "Geographic preferences",
    "geographic_preferences": "Geographic preferences",
    "work_authorization": "Work authorization",
    "travel_and_relocation_preferences": "Travel and relocation",
    "compensation_preferences": "Compensation",
    "eeo_answers": "EEO and sensitive information",
}

TIER_LABELS = {
    1: "Tier 1 — Core factual profile",
    2: "Tier 2 — Skills and positioning",
    3: "Tier 3 — Application-specific and sensitive",
}


def _tier(category: str) -> int:
    if category in {
        "identity",
        "contact_information",
        "employment_history",
        "education",
        "publications",
        "document_track",
    }:
        return 1
    if category in {
        "technical_skills",
        "teaching_experience",
        "experience_claims",
        "experience_duration",
        "medical_affairs_capabilities",
        "field_applications_capabilities",
    }:
        return 2
    return 3


def _verification_page() -> None:
    st.title("Profile Verification")
    connection = get_connection()
    apply_canonical_proposals(connection)
    seed_phase_15_proposals(connection)
    facts = list_canonical_facts(connection)
    counts = Counter(fact.verification_status for fact in facts)
    raw_count = connection.execute("SELECT count(*) FROM candidate_facts").fetchone()[0]
    missing = missing_required_fields(connection)
    columns = st.columns(7)
    values = (
        ("Raw", raw_count),
        ("Canonical", len(facts)),
        ("Verified", counts["verified"]),
        ("Unverified", counts["unverified"] + counts["needs_edit"]),
        ("Conflicted", counts["conflicted"]),
        ("Restricted", counts["restricted"]),
        ("Rejected", counts["rejected"]),
    )
    for column, (label, value) in zip(columns, values, strict=True):
        column.metric(label, value)
    st.metric("Missing readiness conditions", len(missing))
    for failure in missing:
        st.warning(failure)

    selected_tier_label = st.radio(
        "Verification tier",
        [TIER_LABELS[tier] for tier in (1, 2, 3)],
        horizontal=True,
    )
    selected_tier = next(
        tier for tier, label in TIER_LABELS.items() if label == selected_tier_label
    )
    for tier in (selected_tier,):
        st.header(TIER_LABELS[tier])
        tier_facts = [fact for fact in facts if _tier(fact.category) == tier]
        batch_options = {
            f"{fact.category}.{fact.field_name}: {fact.display_value}": int(fact.id)
            for fact in tier_facts
            if fact.id is not None
            and fact.sensitivity == "ordinary"
            and fact.verification_status == "unverified"
        }
        selected_batch = st.multiselect(
            "Clearly displayed non-sensitive facts to batch approve",
            list(batch_options),
            key=f"batch-tier-{tier}",
        )
        if st.button("Batch approve selected", key=f"batch-button-{tier}"):
            batch_approve(connection, [batch_options[label] for label in selected_batch])
            st.rerun()
        for section in dict.fromkeys(
            CATEGORY_LABELS.get(fact.category, fact.category.replace("_", " ").title())
            for fact in tier_facts
        ):
            st.subheader(section)
            for fact in [
                item
                for item in tier_facts
                if CATEGORY_LABELS.get(item.category, item.category.replace("_", " ").title())
                == section
            ]:
                if fact.id is None:
                    continue
                fact_id = fact.id
                with st.expander(
                    f"{fact.field_name}: {fact.display_value} [{fact.verification_status}]"
                ):
                    st.write("Sources:", fact.source_documents or ["Proposed canonical fact"])
                    st.write("Excerpt:", fact.source_excerpt or "No public excerpt")
                    st.write("Conflicting values:", fact.conflicting_values or "None")
                    st.write("Sensitivity:", fact.sensitivity)
                    st.write("Autofill:", fact.autofill_permission)
                    edited = st.text_input(
                        "Canonical value", fact.canonical_value or "", key=f"value-{fact.id}"
                    )
                    allow = st.checkbox(
                        "Allow autofill after verification",
                        value=fact.autofill_permission,
                        disabled=fact.sensitivity == "restricted",
                        key=f"allow-{fact.id}",
                    )
                    notes = st.text_input("Review notes", key=f"notes-{fact.id}")
                    actions = st.columns(4)
                    if actions[0].button("Approve", key=f"approve-{fact.id}"):
                        update_fact(
                            connection,
                            fact_id,
                            action="approve",
                            autofill_permission=allow,
                            notes=notes,
                        )
                        st.rerun()
                    if actions[1].button("Save edit", key=f"edit-{fact.id}"):
                        update_fact(
                            connection,
                            fact_id,
                            action="edit",
                            value=edited,
                            autofill_permission=allow,
                            notes=notes,
                        )
                        st.rerun()
                    if actions[2].button("Reject", key=f"reject-{fact.id}"):
                        update_fact(connection, fact_id, action="reject", notes=notes)
                        st.rerun()
                    if actions[3].button("Defer", key=f"defer-{fact.id}"):
                        update_fact(connection, fact_id, action="defer", notes=notes)
                        st.rerun()
                    source_rows = connection.execute(
                        """
                        SELECT raw_fact_id, source_document FROM canonical_fact_sources
                        WHERE canonical_fact_id = ? ORDER BY source_document
                        """,
                        (fact_id,),
                    ).fetchall()
                    source_options = {
                        f"{row['source_document']} (raw {row['raw_fact_id']})": int(
                            row["raw_fact_id"]
                        )
                        for row in source_rows
                        if row["raw_fact_id"] is not None
                    }
                    selected_sources = st.multiselect(
                        "Sources to split into a separate fact",
                        list(source_options),
                        key=f"split-sources-{fact_id}",
                    )
                    if st.button("Split selected sources", key=f"split-{fact_id}"):
                        split_fact(
                            connection,
                            fact_id,
                            [source_options[label] for label in selected_sources],
                        )
                        st.rerun()

    st.header("Manual canonical merge")
    merge_options = {
        f"{fact.id}: {fact.category}.{fact.field_name} — {fact.display_value}": int(fact.id)
        for fact in facts
        if fact.id is not None
    }
    selected_merge = st.multiselect(
        "Select related canonical facts to merge after reviewing their values",
        list(merge_options),
        key="manual-merge",
    )
    if selected_merge:
        st.write("Merge preview:")
        for label in selected_merge:
            selected_fact = next(fact for fact in facts if fact.id == merge_options[label])
            st.write(
                {
                    "field": f"{selected_fact.category}.{selected_fact.field_name}",
                    "value": selected_fact.display_value,
                    "sources": selected_fact.source_documents,
                    "status": selected_fact.verification_status,
                }
            )
    if st.button("Merge selected facts"):
        try:
            merge_facts(connection, [merge_options[label] for label in selected_merge])
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

    st.header("Audit history")
    audit_rows = connection.execute(
        """
        SELECT created_at, action, canonical_fact_id, actor, notes
        FROM profile_audit_log ORDER BY id DESC LIMIT 100
        """
    ).fetchall()
    if audit_rows:
        st.dataframe([dict(row) for row in audit_rows], width="stretch")
    else:
        st.info("No profile changes have been recorded.")

    st.header("Application-answer intake")
    st.caption(
        "Sensitive answers are not stored because encryption at rest is not yet implemented. "
        "They can only be marked manual-only."
    )
    for field_name, (sensitivity, _) in INTAKE_FIELDS.items():
        label = field_name.replace("_", " ").title()
        if sensitivity in {"sensitive", "restricted"}:
            manual_choice = st.selectbox(
                label,
                ["Manual entry required", "Prefer not to answer"],
                key=f"manual-choice-{field_name}",
            )
            if st.button(f"Mark {label} manual-only", key=f"manual-{field_name}"):
                mark_sensitive_manual_only(connection, field_name)
                st.info(
                    f"{label} marked manual-only. The selected handling "
                    f"({manual_choice}) was not persisted."
                )
                st.rerun()
            continue
        value = st.text_input(label, key=f"intake-{field_name}")
        verified = st.checkbox(f"Explicitly verify {label}", key=f"verify-intake-{field_name}")
        allow = st.checkbox(f"Allow {label} autofill", key=f"allow-intake-{field_name}")
        if st.button(f"Save {label}", key=f"save-intake-{field_name}"):
            save_intake_answer(
                connection,
                field_name,
                value,
                explicitly_verified=verified,
                autofill_permission=allow,
            )
            st.rerun()
    connection.close()


def _jobs_page() -> None:
    st.title("Dave Jobbot Dashboard")
    st.subheader("Newly discovered jobs")
    with get_connection() as connection:
        jobs = connection.execute("SELECT * FROM jobs ORDER BY id DESC").fetchall()
    if not jobs:
        st.info("No jobs yet. Add one with the CLI.")
    for job in jobs:
        st.write(dict(job))
    st.subheader("Recent application activity")
    st.write("Manual review is mandatory; final submission is not implemented.")


def _application_answers_page() -> None:
    st.title("Application Answers")
    connection = get_connection()
    answers = list_answers(connection)
    st.error(f"Unresolved work-authorization warning: {WORK_AUTH_WARNING}")
    for answer in answers:
        with st.expander(
            f"{answer.field_name}: {answer.display_value} [{answer.verification_status}]"
        ):
            st.write("Question categories:", answer.question_categories)
            st.write("Autofill:", answer.autofill_permission)
            st.write("Sensitivity:", answer.sensitivity)
            st.write("Last verified:", answer.date_verified)
            edited = st.text_input(
                "Verified value",
                answer.canonical_value or "",
                key=f"answer-value-{answer.id}",
            )
            disable = st.checkbox(
                "Disable autofill",
                value=not answer.autofill_permission,
                key=f"answer-disable-{answer.id}",
            )
            if st.button("Save answer", key=f"answer-save-{answer.id}"):
                connection.execute(
                    """
                    UPDATE application_answers SET canonical_value=?, display_value=?,
                      autofill_permission=?, updated_at=? WHERE id=?
                    """,
                    (edited, edited, int(not disable), utc_now(), answer.id),
                )
                connection.execute(
                    """
                    INSERT INTO profile_audit_log
                      (action, actor, after_json, notes, created_at)
                    VALUES ('application_answer_edited', 'human', ?, ?, ?)
                    """,
                    (
                        f'{{"field_name": "{answer.field_name}", "autofill": {str(not disable).lower()}}}',
                        "Value edited in local verification dashboard",
                        utc_now(),
                    ),
                )
                connection.commit()
                st.rerun()
    st.header("Answer audit history")
    audit = connection.execute(
        """
        SELECT created_at, action, after_json, notes FROM profile_audit_log
        WHERE action IN ('approved_default_applied', 'ambiguous_default_recorded',
                         'application_answer_edited')
        ORDER BY id DESC LIMIT 100
        """
    ).fetchall()
    st.dataframe([dict(row) for row in audit], width="stretch")

    st.header("Automation Readiness")
    st.write(
        {
            "automatic_dry_run_enabled": AUTOMATION.enabled,
            "configured_mode": AUTOMATION.mode,
            "final_submit_enabled": AUTOMATION.final_submit_enabled,
            "captcha_policy": AUTOMATION.captcha_policy,
            "visible_browser": AUTOMATION.visible_browser,
            "confidence_threshold": AUTOMATION.minimum_autofill_confidence,
            "stop_before_submit": AUTOMATION.stop_before_submit,
        }
    )
    blockers = connection.execute(
        "SELECT count(*) FROM review_items WHERE status='pending'"
    ).fetchone()[0]
    st.metric("Unresolved automation blockers", blockers)
    st.warning(
        "Automatic real-site execution is disabled. Local automatic dry-run is ready "
        "only when the selected fixture/job has no unresolved required-field blocker."
    )
    connection.close()


def launch_dashboard() -> None:
    page = st.sidebar.radio(
        "Page",
        ["Profile Verification", "Application Answers", "Jobs and applications"],
    )
    if page == "Profile Verification":
        _verification_page()
    elif page == "Application Answers":
        _application_answers_page()
    else:
        _jobs_page()


launch_dashboard()
