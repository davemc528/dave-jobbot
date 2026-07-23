from jobbot.documents.facts import (
    ACADEMIC_FILENAME,
    FAS_FILENAME,
    MSL_FILENAME,
    assign_document_track,
    compare_analyses,
    extract_candidate_facts,
)


def test_required_documents_route_to_expected_tracks() -> None:
    assert assign_document_track(ACADEMIC_FILENAME) == "Biology Teaching / Academic"
    assert assign_document_track(MSL_FILENAME) == "MSL / Medical Affairs"
    assert assign_document_track(FAS_FILENAME) == "FAS / Technical Applications"


def test_extracted_document_facts_are_unverified() -> None:
    text = (
        "DAVID CUNNINGHAM, PhD Houston, TX "
        "Ph.D., Biomedical Sciences | Tulane University School of Medicine "
        "Flow cytometry and RNA-seq"
    )
    analysis = extract_candidate_facts(MSL_FILENAME, text)
    assert analysis.facts
    assert all(fact.verified is False for fact in analysis.facts)
    assert all(fact.source == MSL_FILENAME for fact in analysis.facts)


def test_comparison_identifies_duplicates_and_conflicts() -> None:
    first = extract_candidate_facts(MSL_FILENAME, "DAVID CUNNINGHAM 7+ years of oncology")
    second = extract_candidate_facts(FAS_FILENAME, "DAVID CUNNINGHAM 7+ years in cancer biology")
    duplicates, conflicts, ambiguous = compare_analyses([first, second])
    assert any("identity.name" in item for item in duplicates)
    assert any("experience_claims.seven_plus_years" in item for item in conflicts)
    assert ambiguous
