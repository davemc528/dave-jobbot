from __future__ import annotations

import mimetypes
from pathlib import Path


def extract_text(file_path: Path | str) -> str:
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix == ".txt":
        return path.read_text(encoding="utf-8")

    if suffix == ".docx":
        return _extract_docx_text(path)

    if suffix == ".pdf":
        return _extract_pdf_text(path)

    raise ValueError(f"Unsupported document type: {suffix}")


def _extract_docx_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(path)
    import zipfile
    from xml.etree import ElementTree as ET

    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ET.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for para in root.findall(".//w:p", ns):
        text = "".join(node.text or "" for node in para.findall(".//w:t", ns))
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _extract_pdf_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(path)
    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages).strip()


def infer_document_type(path: Path | str) -> str:
    file_path = Path(path)
    guess = mimetypes.guess_type(str(file_path))[0] or ""
    if guess.startswith("application/pdf"):
        return "pdf"
    if guess.startswith("application/vnd.openxmlformats-officedocument.wordprocessingml.document"):
        return "docx"
    return file_path.suffix.lower().lstrip(".")
