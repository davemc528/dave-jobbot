from __future__ import annotations

from collections import Counter

import streamlit as st

from jobbot.db import get_connection
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

    for tier in (1, 2, 3):
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
    if st.button("Merge selected facts"):
        try:
            merge_facts(connection, [merge_options[label] for label in selected_merge])
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

    st.header("Application-answer intake")
    st.caption(
        "Sensitive answers are not stored because encryption at rest is not yet implemented. "
        "They can only be marked manual-only."
    )
    for field_name, (sensitivity, _) in INTAKE_FIELDS.items():
        label = field_name.replace("_", " ").title()
        if sensitivity in {"sensitive", "restricted"}:
            if st.button(f"Mark {label} manual-only", key=f"manual-{field_name}"):
                mark_sensitive_manual_only(connection, field_name)
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


def launch_dashboard() -> None:
    page = st.sidebar.radio("Page", ["Profile Verification", "Jobs and applications"])
    if page == "Profile Verification":
        _verification_page()
    else:
        _jobs_page()


launch_dashboard()
