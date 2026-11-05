# OmniRush Handover Document — Jobyn AI V2

## Project purpose and product vision

Jobyn AI is a career intelligence platform that transforms a candidate's CV into a job-specific tailored application. The product vision is: a candidate uploads their CV, provides a job description, receives an evidence-based analysis, and obtains an actual tailored CV they can edit conversationally and download as DOCX or PDF.

## Current implementation status

### Completed phases

**Phase 1 — Document upload, extraction, and persistence**
- CV upload (PDF/DOCX) with text extraction via `pypdf` and `python-docx`
- JD paste (conversational chat composer) and JD upload (PDF/DOCX)
- Original file bytes stored for re-processing
- Soft-delete, ownership checks, pagination
- 19 tests

**Phase 2 — JD analysis, CV matching, and tailoring recommendations**
- JD analysis: extracts job title, employer, responsibilities, qualifications, skills, keywords
- CV-to-JD matching: evidence-based findings with matched skills, missing requirements, weak sections
- Tailoring recommendations: section actions (retain/rewrite/reorganise/highlight/add), achievements to highlight, information to request, keyword optimisation, priority actions
- Cascade error handling: failed stages persist partial results with error messages
- 17 tests

**Phase 3 — CV rewriting, conversational editing, document generation, and export**
- CV rewriting engine: produces a complete tailored CV as structured JSON (sections with heading, type, content, items, source)
- Conversational editing: natural-language edit requests create new versions (previous versions preserved)
- Version management: accept/reject/revert with full revision history
- DOCX export via `python-docx` (A4, Calibri, professional formatting)
- PDF export via `reportlab` (A4, Helvetica, ATS-friendly)
- Frontend: CV preview, version badge, status badge, download buttons, version history
- 27 tests + 5 E2E tests = 32 tests

**Total: 68 tests, all passing.**

### Incomplete features

- Phase 4 (conversational editing quality improvements) — not started
- Visual CV editor — not implemented
- Original CV formatting preservation — not implemented
- Real-provider AI quality testing — all tests use mocked AI responses
- Browser-level (Playwright/Selenium) testing — not implemented
- Pilot deployment hardening — not done

## Repository and branch information

- **Repository**: `https://github.com/mwansamatimba/jobyn-ai-v2.git`
- **Local directory**: `C:\Users\Mwansa\jobyn-ai-v2.0`
- **Active branch**: `feature/cv-tailoring-studio`
- **Latest committed commit**: `f552f57 feat(admin): add first-administrator provisioning script and tests`
- **All Phase 1/2/3 work is UNCOMMITTED** — it exists only in the working tree as modified tracked files and new untracked files

### Uncommitted changes

**Modified tracked files (8, before this handover update):**
- `backend/api/router.py` — registered tailoring router
- `backend/demo.html` — tailoring tab, chat composer, CV preview, version controls, downloads
- `backend/models/__init__.py` — registered `TailoringDocument`, `TailoringAnalysis`, `TailoredCV`
- `backend/models/enums.py` — added `DocumentType`, `ExtractionStatus` enums
- `backend/models/user.py` — added `tailoring_documents` relationship
- `requirements.txt` — added `reportlab>=4.0,<5.0`
- `pyproject.toml` — added `reportlab>=4.0,<5.0`
- `uv.lock` — synchronized; resolves ReportLab 4.5.1

**Git inventory at the last pre-commit review (before this handover update): 8 modified tracked files and 26 untracked files, 34 paths total.** The 26 untracked files comprise 24 Phase 1–3 implementation, migration, and test files plus `.agents/security-audit-first-admin.md` and this handover document. The handover document is itself untracked.

The 24 Phase 1–3 files are:
- AI services: `backend/ai/jd_analyzer.py`, `cv_jd_matcher.py`, `tailoring_advisor.py`, `cv_rewriter.py`, `cv_editor.py`
- Models: `backend/models/tailoring.py`, `tailoring_analysis.py`, `tailored_cv.py`
- Repositories: `backend/repositories/tailoring.py`, `tailoring_analysis.py`, `tailored_cv.py`
- Services: `backend/services/tailoring_service.py`, `tailoring_analysis_service.py`, `tailoring_generation_service.py`, `document_export.py`
- Schemas: `backend/schemas/tailoring.py`
- Routes: `backend/api/routes/tailoring.py`
- Migrations: `alembic/versions/a1b2c3d4e5f7_add_tailoring_documents.py`, `b2c3d4e5f6a7_add_tailoring_analyses.py`, `c3d4e5f6a7b8_add_tailored_cvs.py`
- Tests: `tests/test_tailoring.py`, `test_tailoring_phase2.py`, `test_tailoring_phase3.py`, `test_tailoring_e2e.py`

## Important file paths

| Purpose | Path |
|---|---|
| Frontend (single-page app) | `backend/demo.html` — served at `/demo` |
| Admin UI | `backend/admin.html` — served at `/admin` |
| FastAPI entry point | `backend/main.py` |
| API router aggregator | `backend/api/router.py` |
| Tailoring routes | `backend/api/routes/tailoring.py` |
| Tailoring schemas | `backend/schemas/tailoring.py` |
| AI provider abstraction | `backend/ai/llm.py` |
| CV rewriter AI | `backend/ai/cv_rewriter.py` |
| CV editor AI | `backend/ai/cv_editor.py` |
| JD analyzer AI | `backend/ai/jd_analyzer.py` |
| CV-JD matcher AI | `backend/ai/cv_jd_matcher.py` |
| Tailoring advisor AI | `backend/ai/tailoring_advisor.py` |
| Tailoring service | `backend/services/tailoring_service.py` |
| Analysis service | `backend/services/tailoring_analysis_service.py` |
| Generation service | `backend/services/tailoring_generation_service.py` |
| Document export | `backend/services/document_export.py` |
| Tailoring document model | `backend/models/tailoring.py` |
| Analysis model | `backend/models/tailoring_analysis.py` |
| Tailored CV model | `backend/models/tailored_cv.py` |
| Enums | `backend/models/enums.py` |
| Model registration | `backend/models/__init__.py` |
| Auth (JWT) | `backend/auth/jwt.py`, `backend/api/deps.py` |
| Config | `backend/core/config.py` |
| Vercel entry point | `api/index.py` |
| Conftest | `tests/conftest.py` |
| Phase 1 tests | `tests/test_tailoring.py` |
| Phase 2 tests | `tests/test_tailoring_phase2.py` |
| Phase 3 tests | `tests/test_tailoring_phase3.py` |
| E2E tests | `tests/test_tailoring_e2e.py` |

## Architecture overview

### Frontend
- `backend/demo.html` — a single-page application (no build step, no framework)
- Served at `/demo` by FastAPI (`backend/main.py` returns `FileResponse`)
- Tabs: Landing → Profile → Discover → Match → Career → **Tailoring** → Copilot → Applications → Interview
- The Tailoring tab has: CV upload zone, AI chat composer (JD paste + follow-up), JD file upload card, CV preview, version controls, download buttons
- All API calls go through the `api()` helper which sends `Authorization: Bearer ${token}`
- File uploads use `fetch()` with `FormData` directly (not the `api()` helper)

### Backend API routes (all under `/api/v1/`)

**Tailoring routes** (`/tailoring/`):
- `POST /cv/upload` — CV upload (PDF/DOCX)
- `POST /job-description/upload` — JD upload (PDF/DOCX)
- `POST /job-description/paste` — JD paste (JSON `{"text": "..."}`)
- `GET /documents` — paginated list
- `GET /documents/{id}` — single doc (ownership-checked)
- `GET /cv/latest` — latest CV document
- `GET /job-description/latest` — latest JD document
- `DELETE /documents/{id}` — soft-delete
- `POST /analyze` — run full analysis pipeline (Phase 2)
- `GET /analysis/latest` — latest analysis
- `GET /analysis/{id}` — specific analysis (ownership-checked)
- `GET /analysis` — paginated list
- `POST /cv/generate` — generate tailored CV (Phase 3)
- `GET /cv/tailored/latest` — latest tailored CV version
- `POST /cv/edit` — conversational edit (creates new version)
- `GET /cv/versions` — version history
- `GET /cv/{cv_id}` — specific CV version (ownership-checked)
- `PATCH /cv/{cv_id}/status` — accept/reject
- `POST /cv/revert` — revert to earlier version
- `GET /cv/{cv_id}/export/docx` — DOCX download
- `GET /cv/{cv_id}/export/pdf` — PDF download

**Existing routes** (pre-tailoring):
- `/auth/register`, `/auth/login`, `/auth/me`
- `/resume/upload`
- `/jobs`, `/jobs/search`, `/jobs/deterministic-match`
- `/career/analyze`, `/career/history`
- `/application-copilot/generate`
- `/applications`, `/applications/{id}`
- `/admin/jobs` (admin-only CSV import)
- `/admin/users` (admin-only user management)

### Database models and migrations

**Migration chain** (9 revisions):
```
0001 → e77d77a9d923 → a119af425295 → b4c8e2f1a903 → c1a2b3d4e5f6 → d7e8f9a0b1c2 → a1b2c3d4e5f7 → b2c3d4e5f6a7 → c3d4e5f6a7b8
```

**Tailoring tables**:
- `tailoring_documents` — uploaded CVs and JDs with raw bytes, extracted text, extraction status
- `tailoring_analyses` — analysis results (JD analysis + match + recommendations as JSON columns)
- `tailored_cvs` — tailored CV versions with content (JSON), version_number, status, edit_type, edit_request, change_summary

**Other tables** (pre-existing): `users`, `resumes`, `generated_resumes`, `resume_templates`, `resume_drafts`, `jobs`, `applications`, `match_results`, `career_insights`, `user_profiles`, `user_skills`, `user_education`, `user_experience`, `user_references`, `user_settings`, `ingestion_sources`, `job_ingestion_sources`, `job_observations`, `compliance_events`

### AI services and provider abstraction

All AI services call `generate_json()` or `generate_text()` from `backend/ai/llm.py`. The active provider is selected by `LLM_PROVIDER` env var:
- `nvidia` (default) — NVIDIA NIM, uses `NVIDIA_API_KEY`, `NVIDIA_BASE_URL`, `NVIDIA_MODEL`
- `freellmapi` — self-hosted router, uses `FREELLMAPI_BASE_URL`, `FREELLMAPI_API_KEY`

Every AI service follows the same pattern:
1. Validate input
2. Build a prompt with a JSON schema and integrity rules
3. Call `generate_json(prompt)`
4. Catch `LLMError`, wrap in service-specific error
5. Normalise the response (ensure all required fields present)

**Integrity rules** (enforced via prompt, not code):
- Never fabricate qualifications, employers, dates, skills, or achievements
- Evidence must reference actual CV text
- Missing information goes to `information_to_request` or `missing_requirements`

### Authentication approach

- JWT HS256 via `backend/auth/jwt.py` (Jobyn native auth)
- Optional Firebase RS256 fallback in `backend/api/deps.py` (disabled when `FIREBASE_PROJECT_ID` is None)
- `get_current_user` dependency resolves token → User
- `require_admin` dependency checks user role
- All tailoring endpoints require authentication; cross-user access returns 404

### Document generation/export

- DOCX: `backend/services/document_export.py` → `generate_docx()` — uses `python-docx`, A4 page size, Calibri font, professional headings, bullet points
- PDF: `backend/services/document_export.py` → `generate_pdf()` — uses `reportlab`, A4 page size, Helvetica font, ATS-friendly single-column layout
- Both generate in-memory (no temp files), return bytes
- Export endpoints return `Response` with `Content-Disposition: attachment`
- No LibreOffice dependency

## Development environment setup

### Prerequisites
- Python 3.12+
- Windows (PowerShell) or Linux/macOS
- Virtual environment at `.venv312/`

### Local development commands (PowerShell)

```powershell
# Activate venv (implicit — commands use .\.venv312\Scripts\python.exe directly)

# Run the dev server (local SQLite)
$env:DATABASE_URL='sqlite+aiosqlite:///./jobyn.db'
.\.venv312\Scripts\uvicorn.exe backend.main:app --host 127.0.0.1 --port 8000

# Run all tailoring tests
.\.venv312\Scripts\python.exe -m pytest tests/test_tailoring.py tests/test_tailoring_phase2.py tests/test_tailoring_phase3.py tests/test_tailoring_e2e.py --tb=short -q

# Run full regression (all tests)
.\.venv312\Scripts\python.exe -m pytest --tb=short -q

# Apply migrations to local SQLite
$env:DATABASE_URL='sqlite+aiosqlite:///./jobyn.db'
.\.venv312\Scripts\alembic.exe upgrade head

# Check current migration version
$env:DATABASE_URL='sqlite+aiosqlite:///./jobyn.db'
.\.venv312\Scripts\alembic.exe current
```

### Important environment notes
- `.env` points to Supabase (production PostgreSQL). For local dev, override `DATABASE_URL` to `sqlite+aiosqlite:///./jobyn.db`
- `tests/conftest.py` sets `DATABASE_URL` to an isolated temporary SQLite database before application imports; it creates and recreates the test schema there. Test commands above do not target the local `jobyn.db`.
- Migration verification was run against a fresh temporary SQLite database outside the project. The full chain reached head `c3d4e5f6a7b8`; downgrade to base and re-upgrade succeeded. No existing project database was used for that verification.
- There is a stray `C:\Users\Mwansa\jwt.py` file that can shadow PyJWT — if import fails, remove or rename it
- ReportLab is declared as `reportlab>=4.0,<5.0` in both `requirements.txt` and `pyproject.toml`; `uv.lock` has been synchronized and resolves ReportLab 4.5.1. Docker installs from `requirements.txt`; CI and editable installs use `pyproject.toml`. `requirements-lock.txt` remains unchanged because its generation process and intended role are unconfirmed. Keep that legacy lock's provenance and synchronization as an outstanding dependency-maintenance question.

## Important safety restrictions

- **Never commit the Firebase service account JSON**: `jobyn-ai-firebase-adminsdk-fbsvc-48a6efa417.json` (it is in `.gitignore`)
- **Never inspect or log secrets**: `.env`, API keys, JWT secrets, CV content, or PII
- **Never push directly to `main` or `vercel-demo`** — always use feature branches
- **Never modify the production database** — the `.env` DATABASE_URL points to Supabase; always override to SQLite for local work
- **Never delete existing files** unless explicitly asked
- **The original uploaded CV must never be modified** — only the tailored versions are editable
- **The AI prompts contain integrity rules** — never remove the "NEVER invent" instructions
- **All tailoring endpoints require authentication** — never remove the `Depends(get_current_user)` from routes
- **Cross-user access must return 404** — never change the ownership checks in repositories

## Test commands and results

### Latest independently verified test results

| Suite | Tests | Result |
|---|---|---|
| Full backend suite | 484 collected | 483 passed, 1 skipped, 24 warnings |
| Phase 1 (`test_tailoring.py`) | 19 | Included in full-suite pass |
| Phase 2 (`test_tailoring_phase2.py`) | 17 | Included in full-suite pass |
| Phase 3 (`test_tailoring_phase3.py`) | 27 | Included in full-suite pass |
| E2E (`test_tailoring_e2e.py`) | 5 | Included in full-suite pass |
| **Phase 1–3 tailoring files** | **68 tests** | **Included in full-suite pass** |

The full backend suite was independently run with `python -m pytest --tb=short`: 483 passed, 1 skipped, 24 warnings in 477.02 seconds. The 68 Phase 1–3 tests are declared across the four tailoring files above. The skip is the PostgreSQL-only Supabase identity test under SQLite. Warnings were Starlette/httpx and HTTP status deprecations, plus SQLAlchemy cartesian-product warnings.

The migration chain was independently upgraded from base on a fresh temporary SQLite database to `c3d4e5f6a7b8`, downgraded to base, and upgraded again successfully. This was an isolated migration verification, not a migration or inspection of the project's local `jobyn.db`.

Earlier Kiro-reported results are historical reports, not a substitute for these independent checks. Browser-level UI testing and real-provider AI smoke/quality testing have not been performed and remain outstanding.

### Test approach
- All AI calls are patched with `unittest.mock.AsyncMock` — no real LLM key needed
- `tests/conftest.py` sets `DATABASE_URL` to an isolated temporary SQLite database and creates the schema there; tests use the FastAPI `TestClient` against that test database, not the local `jobyn.db`
- File bytes are generated in-memory (minimal DOCX via `python-docx`, minimal PDF via raw bytes)
- The E2E test exercises the complete workflow: register → upload CV → paste JD → analyze → generate → edit × 2 → version history → revert → accept → download DOCX → download PDF → verify content

## Outstanding work

### Phase 4: Conversational Editing Quality and Candidate Experience (recommended next task)

**Better conversational editing:**
- The current edit routing uses keyword matching (regex). Consider a more intelligent intent classifier that distinguishes between analysis requests, CV edits, and general questions.
- Support multi-turn editing context (e.g., "also shorten the skills section" as a follow-up to "rewrite my summary").

**Section-specific editing:**
- Allow candidates to target specific sections ("rewrite my experience at TechCorp" rather than "rewrite my CV").
- The backend already supports this (the `CVEditorService` prompt receives the edit request), but the frontend could offer section-level edit buttons.

**Candidate confirmation of missing information:**
- When the analysis identifies `information_to_request`, the chat should proactively ask the candidate.
- Candidate-provided answers should be marked as `source: "candidate_confirmed"` in the tailored CV (the schema supports this).

**Before-and-after comparisons:**
- Show a diff view when a new version is created, highlighting what changed.
- Currently, the candidate sees only the new version and a text summary of changes.

**Clear explanations of changes:**
- The `edit_summary` field exists but could be more prominent in the UI.

**Improved version review and control:**
- The version history is functional but could show diffs between versions.
- Accept/reject could be more prominent.

**Better editing error handling:**
- When an edit fails, the chat shows the error. Consider retrying with fallback prompts or suggesting alternative phrasing.

### Future work

- **Visual CV editor**: Replace the read-only HTML preview with an inline rich-text editor (e.g., ProseMirror, TipTap). Candidates should be able to click and edit text directly.
- **Original CV formatting preservation**: Currently, the tailored CV uses a clean default template. Preserving the original document's formatting (fonts, layout, colours) would require parsing the original DOCX/PDF structure and applying changes in-place.
- **Real-provider AI quality testing**: All tests use mocked AI responses. Real LLM quality needs to be tested with actual NVIDIA NIM or FreeLLMAPI keys to verify prompt effectiveness and output quality.
- **Browser-level testing**: Playwright or Selenium tests for the frontend workflow (currently only API-level tests exist).
- **Pilot readiness and deployment hardening**: Review Vercel/serverless timeout limits, add rate limiting, implement proper error pages, add logging/monitoring for production.
- **Dependency lock provenance**: `requirements-lock.txt` remains unchanged because its generation process and intended role are unconfirmed. Establish whether it is maintained and how it should be generated before changing it.

## Known limitations and risks

1. **Mocked AI tests vs real-provider behaviour**: All 68 tests patch the AI calls with deterministic mocks. Real LLM responses may not conform to the expected JSON schema, may hallucinate, or may produce lower-quality output. The `_normalize_response` functions provide some resilience, but real-provider testing is essential before production.

2. **Original CV design preservation**: The tailored CV uses a default professional template (Calibri/Helvetica, A4, simple sections). The original document's design, fonts, layout, and colours are not preserved. This is a known limitation — preserving original formatting is a complex feature requiring DOCX/PDF structure parsing.

3. **Read-only preview**: The CV preview in the frontend is rendered HTML. Candidates cannot click and edit text inline — they must use the chat composer to request changes. A visual editor is a Phase 4+ feature.

4. **Serverless deployment considerations**: The export endpoints generate DOCX/PDF in-memory, which is serverless-compatible (no temp files, no LibreOffice). However:
   - `reportlab` and `python-docx` must be in the deployment's dependencies
   - Large CVs may hit memory/timeout limits on Vercel (default 10s for hobby, 60s for pro)
   - The `vercel.json` is minimal (`{"version": 2}`) — it may need explicit function configuration for longer timeouts

5. **Migration verification**: The complete chain was independently tested against a fresh temporary SQLite database: upgrade to `c3d4e5f6a7b8`, downgrade to base, then re-upgrade to head all succeeded. This does not verify PostgreSQL behavior or prove the state of any existing local database.

6. **Firebase auth**: A dual-path JWT decoder exists in `backend/api/deps.py` (Jobyn HS256 first, Firebase RS256 fallback). It is disabled when `FIREBASE_PROJECT_ID` is not set. The Firebase service account JSON exists in the project root but is in `.gitignore`.

7. **Stray files**: `C:\Users\Mwansa\jwt.py` shadows the `PyJWT` package when scripts are run from the home directory. This is not a project issue but can cause confusing import errors. The fix is to set `PYTHONPATH` to the project root or remove the stray file.

8. **Uncommitted work**: Phase 1/2/3 implementation remains uncommitted in the working tree. The dependency correction is also uncommitted. The last commit (`f552f57`) is the prep commit (`.gitignore` + demo.html fix + provisioning tests). The branch `feature/cv-tailoring-studio` is pushed to origin but does NOT contain the Phase 1/2/3 changes — those need to be committed.

## Recommended next task for OmniRush

**First task: Commit all Phase 1/2/3 work to the `feature/cv-tailoring-studio` branch.**

Before starting Phase 4, the uncommitted work should be committed in logical commits:
1. Phase 1: models, schemas, routes, service, migration, tests
2. Phase 2: AI services, analysis model, analysis service, migration, tests
3. Phase 3: CV rewriter/editor, tailored CV model, generation service, export, migration, tests, frontend changes
4. Dependency correction: `pyproject.toml`, `requirements.txt`, and `uv.lock` (ReportLab is aligned; `requirements-lock.txt` was left unchanged pending confirmation of its role)

After committing, run the full test suite to confirm everything passes on a clean checkout.

Then proceed to Phase 4: Conversational Editing Quality and Candidate Experience.

## Ready-to-use opening prompt for OmniRush

```
I am continuing development of Jobyn AI V2, a FastAPI/SQLAlchemy backend with a single-page frontend (backend/demo.html).

The project is at C:\Users\Mwansa\jobyn-ai-v2.0 on branch feature/cv-tailoring-studio.

Phase 1 (CV/JD upload and extraction), Phase 2 (JD analysis, CV matching, tailoring recommendations), and Phase 3 (CV rewriting, conversational editing, DOCX/PDF export, version management) are implemented but UNCOMMITTED in the working tree.

Please start by:
1. Reading docs/OMNIRUSH_HANDOFF.md for the full project context.
2. The latest full backend test run passed (483 passed, 1 skipped, 24 warnings); rerun if changes are made before commit.
3. Committing all Phase 1/2/3 work to the feature/cv-tailoring-studio branch in logical commits (Phase 1, Phase 2, Phase 3 separately), including the approved dependency correction.
4. Then we will begin Phase 4: Conversational Editing Quality and Candidate Experience.

Important restrictions:
- Do not modify production configuration or production data.
- Do not inspect or expose secrets (.env, API keys).
- Do not push to main or vercel-demo branches.
- Preserve the original uploaded CV at all times.
- All tailoring endpoints must remain authenticated.
- AI prompts must never instruct the model to fabricate candidate information.
```
