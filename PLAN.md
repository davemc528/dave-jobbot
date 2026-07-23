# Dave Jobbot Phase 1 Plan

## Proposed architecture

Dave Jobbot is a local-first Python application for privacy-conscious job application assistance. Phase 1 centers on deterministic source-document ingestion, profile capture, job scoring, and a human-in-the-loop browser automation workflow.

The application is organized as a modular package under `src/jobbot/`:

- `profile/`: canonical candidate facts and reusable answer library
- `documents/`: extraction of PDF/DOCX metadata and candidate facts
- `jobs/`: job ingestion, normalization, deduplication, and fit scoring
- `llm/`: provider-neutral LLM interface with OpenAI-compatible and Ollama backends
- `tailoring/`: resume track routing and truthful tailoring suggestions
- `applications/`: application records, review queue, and status transitions
- `browser/`: Playwright inspection and dry-run autofill
- `adapters/`: adapter interfaces for generic, Greenhouse, Lever, and Workday
- `ui/`: Streamlit dashboard
- `security/`: redaction, secrets handling, and safe logging

Persistence is local SQLite, with a small YAML truth store for editable candidate preferences. A CLI orchestrates commands, and a Streamlit dashboard provides the local review surface.

## Data model

Core persisted entities:

- `candidate_facts`: canonical applicant facts, verification state, and pending confirmation items
- `documents`: imported source documents, metadata, and hash information
- `jobs`: normalized job postings
- `job_scores`: transparent scoring breakdowns by job
- `reusable_answers`: reusable screening answers and verification metadata
- `applications`: application records linked to jobs, track decisions, and statuses
- `application_events`: status history and audit trail
- `browser_runs`: browser automation runs, screenshot paths, and session metadata
- `review_items`: pending approvals or confirmations

The editable profile uses `data/profile.yaml` and `data/answers.yaml` to seed facts that are later verified by a human before they are treated as trusted autofill values.

## Security and privacy risks

Primary risks include:

- PII leakage through logs, screenshots, or exported files
- Untrusted external job postings or HTML content
- Browser automation accidentally entering sensitive information
- Sending resumes or application answers to a remote LLM without explicit configuration
- Overbroad autofill for fields that may involve protected categories or legal/visa data

The design mitigates these risks with local-first storage, redaction at log boundaries, environment-variable secrets, no passwords in source control, and a hard requirement that the browser never submits unaided.

## Dependencies

- Python 3.12
- `uv` for dependency management
- `pydantic` for domain models
- `typer` for the CLI
- `sqlite3` via Python stdlib for persistence
- `playwright` for browser inspection and dry-run automation
- `streamlit` for local dashboard
- `pytest` for tests
- `ruff` and `mypy` for quality checks

## Implementation sequence

1. Scaffold the package structure, configuration, and docs
2. Implement local persistence, schema initialization, and security helpers
3. Add profile and answer models plus seeded YAML state
4. Implement deterministic resume-route selection
5. Implement document ingestion with plaintext extraction placeholders for PDF/DOCX
6. Add job normalization, deduplication, and weighted scoring
7. Build the CLI for init/import/profile/job/application/browser/dashboard/doctor
8. Implement the local review dashboard and browser inspection/autofill MVP
9. Add HTML fixtures and Playwright integration tests
10. Run formatting, typing, and test verification

## Testing strategy

- Unit tests for extraction helpers, resume routing, and scoring
- Tests enforcing that sensitive unverified answers are blocked from autofill
- Regression tests ensuring no fabricated facts enter generated output
- Playwright tests against local HTML fixtures for generic, Greenhouse, Lever, and Workday-like forms
- Browser tests to prove stop-before-submit and CAPTCHA/manual intervention behavior
- Log redaction tests for email, phone, address, and API-key placeholders

## Assumptions and unresolved questions

Assumptions:

- The provided source documents are the authoritative truth source for Phase 1 extraction
- Existing repository scaffolding is retained and hardened where it meets the safety requirements
- Human approval is required before profile facts become verified

Unresolved questions:

- Whether the source documents should be converted to a secure internal extract cache during import
- Which exact resume and application formats are required for delivered outputs
- Whether a future service account or remote provider will be used for LLM-based drafting beyond the provider-neutral interface
