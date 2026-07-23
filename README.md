# Dave Jobbot

A local, privacy-conscious AI job-application assistant designed for human-in-the-loop application preparation. Phase 1 provides deterministic resume routing, transparent job scoring, a review dashboard, and a safe browser automation MVP that never submits applications unattended.

## Quick start

```bash
uv sync
uv run playwright install chromium
cp .env.example .env
uv run jobbot init
uv run jobbot doctor
uv run jobbot import-documents
uv run jobbot profile review
uv run jobbot job add --file sample_job_description.txt
uv run jobbot jobs score
uv run jobbot jobs list
uv run jobbot application prepare 1
uv run jobbot dashboard
```

## Phase I.5 profile verification

Preview deterministic canonical groups before changing the database:

```bash
PYTHONPATH=src uv run python -m jobbot.cli profile canonicalize
```

Apply the displayed groups (this does not verify any fact):

```bash
PYTHONPATH=src uv run python -m jobbot.cli profile canonicalize --apply
PYTHONPATH=src uv run python -m jobbot.cli profile readiness
```

Launch the dashboard and select **Profile Verification**:

```bash
PYTHONPATH=src uv run streamlit run src/jobbot/ui/dashboard.py
```

Review Tier 1 before Tier 2 and Tier 3. Approval is always an explicit human action.
Duplicate source agreement never marks a fact verified. Patent facts stay restricted and cannot
be autofilled.

Sensitive-answer encryption at rest is not implemented in Phase I.5. Work authorization,
sponsorship, compensation, relocation, travel, noncompete, prior-employer, start-date, and EEO
values are therefore not persisted; mark them manual-only in the questionnaire. EEO supports
manual handling, including “Prefer not to answer.”

URL ingestion performs a plain fetch and always requires human review. Browser commands use the
stored job URL:

```bash
uv run jobbot browser inspect JOB_ID
uv run jobbot browser autofill JOB_ID --dry-run
```

Visible browser mode is the default. Use `--headless` only for local fixture testing. Phase 1
rejects browser autofill unless `--dry-run` is supplied and never activates a submit control.

## Verification

```bash
uv run pytest
uv run ruff check .
uv run mypy src
```

## Privacy and safety

Source extracts and facts begin unverified. The assistant never fabricates applicant information,
never submits applications unattended, and never treats sensitive data as autofillable without an
explicitly verified value. Source documents, extracted databases, screenshots, logs, generated
resumes, browser profiles, and `.env` files are ignored by version control. Remote LLM use is
disabled by default.

## Phase 1 limitations

- No full JobSpy integration yet
- No final-submit automation
- No email or calendar integrations
- No resume DOCX/PDF generation yet
- URL ingestion does not yet provide robust HTML-to-job normalization
- Browser Phase 1 is inspection and dry-run planning; it does not perform real-site submission
