from jobbot.profile.profile import build_fact_snapshot


def test_no_fabricated_facts_enter_generated_profile() -> None:
    snapshot = build_fact_snapshot(
        contact={"email": "candidate@example.com"},
        employment_history=[],
        education=[],
        technical_skills=[],
        facts_requiring_confirmation=["Medical Affairs capability"],
    )

    assert snapshot["facts_requiring_confirmation"] == ["Medical Affairs capability"]
    assert "medical_affairs" not in snapshot
