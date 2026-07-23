from jobbot.jobs.scoring import score_job


def test_scoring_produces_weighted_breakdown() -> None:
    result = score_job(
        title="Oncology Medical Science Liaison",
        description="Scientific communications and medical affairs engagement across oncology.",
        location="San Diego, CA",
    )

    assert 0 <= result.score <= 100
    assert result.score_breakdown
    assert result.reasons
