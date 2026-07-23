from jobbot.tailoring.routing import select_track


def test_msl_resume_routing_prefers_msl_track() -> None:
    recommendation = select_track(
        job_title="Oncology Medical Science Liaison",
        description="Scientific communications and field medical engagement across oncology.",
    )

    assert recommendation.selected_track == "MSL / Medical Affairs"
    assert recommendation.confidence_score >= 0.8
    assert recommendation.human_review_required is True


def test_fas_resume_routing_prefers_fas_track() -> None:
    recommendation = select_track(
        job_title="Field Application Scientist",
        description="Flow cytometry instrumentation customer training support.",
    )

    assert recommendation.selected_track == "FAS / Technical Applications"
    assert recommendation.confidence_score >= 0.8
    assert recommendation.human_review_required is True


def test_teaching_route_prefers_academic_cv() -> None:
    recommendation = select_track(
        job_title="Adjunct Biology Instructor",
        description="Curriculum delivery and undergraduate teaching.",
    )

    assert recommendation.selected_track == "Biology Teaching / Academic"
    assert recommendation.confidence_score >= 0.8
