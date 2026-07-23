# AGENTS.md

## Project conventions

- Use Python 3.12.
- Keep business logic modular under `src/jobbot/`.
- Prefer deterministic, transparent logic over opaque LLM-only decisions.
- Treat all profile facts as unverified until a human explicitly confirms them.
- No fabricated applicant claims may be introduced in any output or generated artifact.

## Test commands

Run the following from the repository root:

- `uv run pytest`
- `uv run ruff check .`
- `uv run mypy src`

## Privacy rules

- Never commit `.env`, browser profiles, generated resumes, application answers, PII logs, or source documents.
- Use environment variables for secrets.
- Redact email, phone, address, and API-key-like values before logging.
- Never send confidential files to an LLM unless the user explicitly enables and configures that behavior.
- Default to visible Playwright mode; never run unattended application submission.

## Prohibited behavior

- Do not invent employment, education, publications, technical skills, dates, metrics, certifications, clinical experience, management experience, or accomplishments.
- Do not autofill sensitive fields without a verified value.
- Do not click final Submit in Phase 1.
- Do not bypass CAPTCHA or anti-bot controls.
- Do not submit applications automatically.
