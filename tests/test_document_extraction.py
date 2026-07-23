from pathlib import Path

from jobbot.documents.extract import extract_text


def test_extract_text_from_text_fixture(tmp_path: Path) -> None:
    sample = tmp_path / "sample.txt"
    sample.write_text("Medical Science Liaison\nOncology\n", encoding="utf-8")

    result = extract_text(sample)

    assert "Medical Science Liaison" in result
    assert "Oncology" in result
