# CV Tailoring Studio — Inspection Report

**Generated:** Investigation of `feature/cv-tailoring-studio` branch
**Scope:** Backend `C:\Users\Mwansa\jobyn-ai-v2.0` · Frontend `C:\Users\Mwansa\jobyn-landing-page`

---

## Summary

The CV Tailoring Studio backend is **substantially complete and well-structured**. The pipeline covering document upload, JD analysis, CV-to-JD matching, tailoring recommendations, structured CV generation, conversational editing, version management, and DOCX/PDF export is all implemented end-to-end. Test suites (`test_tailoring_phase3.py`, `test_tailoring_e2e.py`) cover all major paths with mocked LLM calls. The critical **missing piece** is a factual validation layer: no independent post-generation validation service exists anywhere in the codebase. The frontend CV Tailoring Studio (`cv-tailoring.tsx`) is a lightweight stub that calls the old-pattern analysis/generation helpers — it does not use the Phase 3 structured API endpoints (`/tailoring/cv/generate`, `/tailoring/cv/edit`, export routes) and renders the CV as a plain text `<pre>` block with only a text-file download. The DOCX and PDF export infrastructure is implemented and functional, but the template quality is minimal.

---

## ALREADY IMPLEMENTED

### Backend AI Layer
| File | What it does |
|------|-------------|
| `backend/ai/jd_analyzer.py` · `JDAnalyzerService.analyze()` | Calls LLM via `generate_json`, extracts 13-field structured JD breakdown. Normalises output — missing fields default to `[]` or `""`. |
| `backend/ai/cv_jd_matcher.py` · `CVJDMatcherService.match()` | Evidence-based CV-to-JD comparison. Returns matching skills/experience/achievements, missing requirements, missing keywords, weak sections. |
| `backend/ai/tailoring_advisor.py` · `TailoringAdvisorService.advise()` | Produces structured tailoring recommendations: section actions, keyword optimisation, priority actions, information to request. |
| `backend/ai/cv_rewriter.py` · `CVRewriterService.rewrite()` | Full CV rewrite using Phase 2 analysis. Returns `sections` list with typed, sourced entries plus `generation_notes`. Token budget: 8192. |
| `backend/ai/cv_editor.py` · `CVEditorService.apply_edit()` | Conversational editing. Detects targeted section, enforces section-scoped edits, handles clarification requests, returns comparison data. |

### Backend Services
| File | What it does |
|------|-------------|
| `backend/services/tailoring_service.py` | Document upload, extraction (PDF/DOCX via `resume_parser`), pasted-text JD storage, latest CV/JD retrieval. |
| `backend/services/tailoring_analysis_service.py` | Orchestrates 3-stage pipeline: JD analysis → matching → recommendations. Persists to `tailoring_analyses` with partial-failure tolerance. |
| `backend/services/tailoring_generation_service.py` | Orchestrates generation, editing, visual edits, version management, accept/reject, revert. Implements `build_comparison()` for section-level diff. |
| `backend/services/document_export.py` | `generate_docx()` and `generate_pdf()` from structured `sections` content. Also `generate_cover_letter_pdf()`. |

### Database Models & Migrations
| Component | Detail |
|-----------|--------|
| `backend/models/tailoring.py` · `TailoringDocument` | Stores CV and JD documents with raw bytes, extracted text, file metadata, extraction lifecycle. |
| `backend/models/tailoring_analysis.py` · `TailoringAnalysis` | Three JSON columns (`jd_analysis`, `match_result`, `recommendations`) + `error` text. |
| `backend/models/tailored_cv.py` · `TailoredCV` | Immutable version records with `content` JSON, `version_number`, `status`, `edit_type`, `edit_request`, `change_summary`. |
| Migrations | `a1b2c3d4e5f7_add_tailoring_documents.py`, `b2c3d4e5f6a7_add_tailoring_analyses.py`, `c3d4e5f6a7b8_add_tailored_cvs.py`, `be801b690825_add_tailored_cvs_timestamp_defaults.py`, `a7b8c9d0e1f2_add_tailoring_analysis_timestamp_defaults.py` — all 5 tailoring migrations are present. |

### API Routes (`backend/api/routes/tailoring.py`)
All routes under `/api/v1/tailoring/`:

| Phase | Endpoints |
|-------|-----------|
| Phase 1 — Documents | `POST /cv/upload`, `POST /job-description/upload`, `POST /job-description/paste`, `GET /documents`, `GET /documents/{id}`, `GET /cv/latest`, `GET /job-description/latest`, `DELETE /documents/{id}` |
| Phase 2 — Analysis | `POST /analyze`, `GET /analysis/latest`, `GET /analysis/{id}`, `GET /analysis` |
| Phase 3 — Generation | `POST /cv/generate`, `GET /cv/tailored/latest`, `POST /cv/edit`, `GET /cv/versions`, `GET /cv/{id}`, `POST /cv/{id}/visual-edit`, `PATCH /cv/{id}/status`, `POST /cv/revert`, `GET /cv/{id}/export/docx`, `GET /cv/{id}/export/pdf`, `POST /application-package/export` |

### Schemas (`backend/schemas/tailoring.py`)
Full Pydantic v2 coverage for all document, analysis, and CV endpoints including `TailoredCVSection`, `EditCVClarification`, `SaveCVContentRequest`, `RevertCVRequest`.

### Tests
| File | Coverage |
|------|----------|
| `tests/test_tailoring_phase3.py` | Generation, editing, section-scoped edits, clarification flow, accept/reject, revert, DOCX/PDF export, content verification, integrity preservation, cross-user blocking — ~20 test cases. |
| `tests/test_tailoring_e2e.py` | Full end-to-end: upload → analysis → generate → 2 edits → revert → accept → DOCX download → PDF download; plus error resilience and cross-user isolation — 5 test cases. |
| `tests/test_tailoring_phase2.py` | Phase 2 pipeline (not read in full, but present). |
| `tests/test_tailoring.py` | Phase 1 upload and document management (present). |

---

## WORKS

Based on code review (tests not executed in this investigation):

1. **Document upload and extraction pipeline** — `TailoringService.upload_document()` calls `resume_parser`, stores raw bytes, sets `extraction_status`. Fully wired.
2. **Analysis pipeline** — Three-stage: JD analysis → matching → recommendations. Partial-failure tolerant (each stage commits independently before stopping on error). `TailoringAnalysisService.run_analysis()` is complete.
3. **CV generation and version management** — `TailoringGenerationService.generate_tailored_cv()` loads latest analysis, calls `CVRewriterService.rewrite()`, persists with incrementing version. Edit, visual edit, revert all create new versions.
4. **Section-scoped conversational editing** — `CVEditorService._detect_target_section()` uses regex and section aliases. When exactly one section matches the request, the route enforces scope via `target_instruction` in the prompt, and post-processes the response to restore unchanged sections.
5. **Comparison/diff generation** — `build_comparison()` identity-matches sections by `(heading, type)`, handles moves and renames, records `before`/`after` per section.
6. **DOCX export** — `generate_docx()` uses `python-docx` 1.2.0. Sets A4 page size, Calibri 11pt, handles `contact`, `summary`, list-based sections with dict items. Returns bytes.
7. **PDF export** — `generate_pdf()` uses ReportLab ≥4.0. A4, Helvetica, single-column `SimpleDocTemplate`, custom styles per section type.
8. **Authentication and ownership** — All routes use `get_current_user`. Repository methods filter by `user_id`. Cross-user access returns 404.
9. **Route contracts** — `POST /cv/edit` returns `EditCVClarification` (HTTP 200) when clarification needed, `TailoredCVRead` (HTTP 201) when version created.

---

## INCOMPLETE

### 1. Factual Validation Layer (Phase 3 requirement — **entirely absent**)
No file named `cv_validator.py`, `validation_service.py`, or similar exists anywhere under `backend/`. The user story requires:
- Per-bullet claim classification (`supported`, `reasonable_rewording`, `needs_confirmation`, `unsupported`, `contradicted`).
- Deterministic checks for employers, job titles, dates, qualifications, certifications, software, and quantified achievements against the original CV text.
- A flag that prevents the document being marked `validated` when a material fabricated claim is detected.
- Validation findings exposed to the frontend.

Currently none of this exists. The `TailoredCV.status` field supports `pending | accepted | rejected`, but there is no `validated` status or any validation metadata column.

### 2. Frontend — Phase 3 API Integration (entirely absent)
`cv-tailoring.tsx` does not call any Phase 3 endpoint:
- It calls `generateTailoredCv()` from `lib/applications.ts`, which POSTs to `/api/v1/tailoring/cv/generate` but then tries to read `result.tailored_cv ?? result.content ?? result.text` from the response — fields that don't exist in `TailoredCVRead`. The backend returns `{ id, content: { sections: [...] }, version_number, ... }`.
- No DOCX or PDF download buttons calling `/export/docx` or `/export/pdf`.
- No section-based CV editor — the CV renders as plain `<pre>` text.
- No analysis results display using Phase 2 structured data (`jd_analysis`, `match_result`, `recommendations`).
- The `analyzeCvAgainstJobDescription()` call POSTs to `/api/v1/tailoring/analyze`, which returns a `TailoringAnalysisRead` — but the frontend tries to read `result.summary`, `result.match_score`, `result.strengths`, `result.gaps`, none of which are top-level fields in `TailoringAnalysisRead`.
- No versioning UI, no conversational edit interface, no accept/reject controls.

### 3. Document QA Service (Phase 4 requirement — absent)
No post-generation document QA exists:
- No check that DOCX can be parsed after generation.
- No check that PDF contains selectable text.
- No detection of blank pages or clipped content.
The `generate_docx()` and `generate_pdf()` functions do not call any QA routine after generating bytes.

### 4. `section_explanations` in `_MOCK_EDITED_CV` (test data gap)
`_MOCK_EDITED_CV` in `test_tailoring_phase3.py` does not include `section_explanations`. The test `test_edit_creates_new_version` patches `CVEditorService.apply_edit` directly, so this is fine, but `test_unscoped_request_keeps_whole_cv_edit_compatibility` patches `generate_json` and manually adds `section_explanations` — this inconsistency is a maintenance hazard, not a blocking bug.

---

## BROKEN

### 1. Frontend `generateTailoredCv()` response mismatch
**File:** `src/lib/applications.ts` · `generateTailoredCv()` (line ~175)
**File:** `src/routes/cv-tailoring.tsx` · `handleGenerate()` (line ~100)

`generateTailoredCv()` reads `result.tailored_cv ?? result.content ?? result.text` and stores the first non-empty string into `generatedCv` (a `string` state variable). The backend returns `TailoredCVRead`:
```json
{ "id": "...", "content": { "sections": [...], "generation_notes": "..." }, "version_number": 1, ... }
```
`result.tailored_cv` → `undefined`. `result.content` → an `object` (the structured CV dict), not a string. `normalizeTextPayload()` on this object will reach into the JSON and may return some nested string, but it will not produce a readable formatted CV. The displayed result in the `<pre>` block will be malformed or empty.

**Impact:** The "Tailor CV" button silently produces no readable output despite a successful backend response.

### 2. Frontend `analyzeCvAgainstJobDescription()` response mismatch
**File:** `src/lib/applications.ts` · `analyzeCvAgainstJobDescription()` (line ~148)

The backend `POST /api/v1/tailoring/analyze` returns `TailoringAnalysisRead`:
```json
{ "id": "...", "jd_analysis": {...}, "match_result": {...}, "recommendations": {...}, ... }
```
The frontend reads `result.summary`, `result.match_score`, `result.strengths`, `result.gaps`. None of these are in `TailoringAnalysisRead`. The function falls back to setting `summary` from `normalizeTextPayload(result)`, which may extract a string from the nested JSON — but `match_score` defaults to 0, `strengths` are split from whatever string is extracted, and `gaps` are always empty.

**Impact:** The analysis panel shows "80%" match score only if `result.summary` truthy (always false) — it shows 0 otherwise. No real gap or strength data is displayed.

### 3. No `pypdf` in `requirements.txt`
**File:** `requirements.txt`
`pypdf>=5.0,<7.0` **is present** — this is correct.
Both `test_tailoring_e2e.py` and `test_tailoring_phase3.py` import `from pypdf import PdfReader` for PDF content verification. This is covered.

### 4. `generate_pdf()` uses `textColor` as a hex string (potential ReportLab issue)
**File:** `backend/services/document_export.py` · `heading_style` (line ~125)

```python
textColor="#171717",
```
ReportLab's `ParagraphStyle` expects `textColor` to be a `Color` object (e.g., `HexColor("#171717")`) not a bare string. Depending on the ReportLab version (≥4.0), this may raise a `TypeError` at runtime when the heading paragraph is built.

**Impact:** `generate_pdf()` may raise `DocumentExportError` for CVs that have headings (all CVs do). This would cause HTTP 500 on `GET /cv/{id}/export/pdf`.

### 5. DOCX heading font size setting uses incorrect attribute access
**File:** `backend/services/document_export.py` · `generate_docx()` (line ~65)

```python
h = doc.add_heading(heading, level=1)
h.style.font.size = Pt(13)
```
In `python-docx`, `h` is a `Paragraph` object. `h.style` is the style, and `h.style.font.size` modifies the style globally (affecting all paragraphs using `Heading 1`). This is a side-effect of sharing the style object. It won't raise an exception but will produce unexpected styling if more than one heading exists. The correct approach is `h.runs[0].font.size = Pt(13)` or applying a custom style.

**Impact:** Cosmetic — heading sizes may be inconsistent. Not a crash.

### 6. `tailoring_generation_service.py` — `TailoringDocumentRepository` import
**File:** `backend/services/tailoring_generation_service.py` (line 17)

```python
from backend.repositories.tailoring import TailoringDocumentRepository
```
This is imported but never used in `TailoringGenerationService`. It is used in the route's dependency injection factory `_get_generation_service()`, not in the service itself. This is a harmless unused import.

---

## DOCUMENT EXPORT QUALITY

### DOCX (`generate_docx()`)
- **Layout:** A4, 2 cm top/bottom, 2.5 cm left/right. Calibri 11pt. Appropriate for ATS.
- **Sections:** Contact and summary render as plain paragraphs. List sections render as `List Bullet` style. Dict items (experience entries) render as bold title + company + bullet details.
- **Gaps:**
  - No date formatting — dates come from whatever string is in the item's `company` field (e.g., `"TechCorp (2020-2024)"`).
  - No horizontal rule or visual separator between sections.
  - Heading font modification via `h.style.font.size` is a style mutation side-effect (see BROKEN §5).
  - No candidate name as a large document title — the contact section renders in body style.
  - No consistent date column or right-aligned date.
- **Assessment:** Functional for ATS parsing. Visually plain. Not polished enough for direct human review without further formatting.

### PDF (`generate_pdf()`)
- **Layout:** A4, `SimpleDocTemplate`, Helvetica body, Helvetica-Bold headings. Single column. Appropriate for ATS.
- **Sections:** Similar structure to DOCX. `ListFlowable` with `•` bullets.
- **Gaps:**
  - `textColor="#171717"` on `heading_style` is a string, not a `HexColor` — likely causes runtime error (see BROKEN §4).
  - No candidate name as large header.
  - No line separators between sections.
  - `ListFlowable` nesting (dict items with sub-bullets) — the experience entry title and its detail bullets are all in the same `ListFlowable`, which will render at the same indentation level, losing hierarchy.
- **Assessment:** If the `textColor` bug is fixed, the PDF will be parseable and ATS-friendly but visually flat. The experience section hierarchy will be lost.

---

## VALIDATION LAYER

**No factual validation layer exists.**

The closest thing is the integrity rules embedded in the LLM prompts for `CVRewriterService` and `CVEditorService`:
- Both prompts instruct the LLM never to invent qualifications, employers, dates, or achievements.
- The `source` field on each section (`original | rewritten | candidate_confirmed`) provides a manual signal.
- `CVEditorService._detect_target_section()` enforces scope constraints deterministically.

None of this constitutes an independent validation layer. There is no code that:
- Reads the generated sections.
- Compares claimed employers, job titles, and dates against the original CV text.
- Classifies bullets as `supported`, `needs_confirmation`, or `unsupported`.
- Blocks marking a CV `validated` when a fabricated claim is detected.

The requirement from Phase 3 of the user story — "An independent validation layer" with deterministic checks for dates, employers, certifications, and quantified achievements — is not implemented.

---

## FRONTEND STATUS

**Single file:** `src/routes/cv-tailoring.tsx`

| Feature | Status |
|---------|--------|
| CV file upload to `/api/v1/tailoring/cv/upload` | ✅ Implemented via `uploadMultipartDocument` |
| Extracted text stored in state | ✅ via `recursiveText(result)` |
| JD paste to `/api/v1/tailoring/job-description/paste` | ✅ POSTed in `handleAnalyze()` |
| Analysis trigger to `/api/v1/tailoring/analyze` | ✅ Called but response handling broken (see BROKEN §2) |
| Structured analysis display (match score, strengths, gaps) | ⚠️ Present in UI but fed from wrong fields — always shows 0% score, empty gaps |
| CV generation to `/api/v1/tailoring/cv/generate` | ✅ Called but response handling broken (see BROKEN §1) |
| Structured CV section preview | ❌ Not implemented — renders `<pre>` plain text |
| Section-based CV editor | ❌ Not implemented |
| Conversational edit (`/cv/edit`) | ❌ Not implemented |
| DOCX download (`/export/docx`) | ❌ Not implemented — only plain `.txt` download via `downloadGeneratedText()` |
| PDF download (`/export/pdf`) | ❌ Not implemented |
| Version history / revert | ❌ Not implemented |
| Accept/reject CV version | ❌ Not implemented |
| Validation warnings | ❌ Not implemented (validation layer absent) |
| Authentication | ✅ Via `apiRequest` which includes auth headers |

The frontend has the structural scaffolding (a two-panel layout with inputs on the left, preview on the right) but the entire interaction beyond the initial upload is broken or absent.

---

## SMALLEST IMPLEMENTATION PLAN

Minimum changes to reach a working end-to-end MVP: CV upload → JD analysis → CV generation → validation → DOCX/PDF download.

### 1. Fix `generate_pdf()` `textColor` bug
**File:** `backend/services/document_export.py`
Change `textColor="#171717"` to `textColor=HexColor("#171717")` and add `from reportlab.lib.colors import HexColor` to the PDF section's local imports. Also fix experience entry hierarchy in `generate_pdf()`: render the title/company line as a separate `Paragraph` above a `ListFlowable` for the detail bullets, rather than nesting both inside the same `ListFlowable`.
**Verify:** `pytest tests/test_tailoring_phase3.py::TestExportContentVerification -v` — both export content tests pass.

### 2. Implement deterministic factual validation service
**File to create:** `backend/services/cv_validator.py`
**Approach:** Pure-Python, no LLM call. The validator receives the generated `sections` list and the original CV text. It performs deterministic checks:
- Extract all employer names and job titles from the original CV text (regex on the experience section).
- For each experience item in generated sections, check that the employer name and job title appear in the original text (case-insensitive substring).
- Extract years/dates from the original text; check that generated date strings are not outside the original range.
- Extract certifications and qualifications from the original text; flag any generated certification not present.
- Extract quantified figures (numbers + % or currency) from the original text; flag any figure in the generated CV not traceable to the original.
- Classify each section item as `supported`, `reasonable_rewording`, `needs_confirmation`, or `unsupported`.
- Return a `ValidationResult` with `is_valid: bool`, `unsupported_claims: list[dict]`, and `validation_summary: str`.

Expose validation findings as a new field on `TailoredCV.content` (no migration needed — `content` is a JSON column). Call the validator from `TailoringGenerationService.generate_tailored_cv()` and store the result under `content["validation"]`.
**Verify:** New unit tests `pytest tests/test_cv_validator.py -v`.

### 3. Add validation summary to generation route response
**File:** `backend/api/routes/tailoring.py`
The `POST /cv/generate` route already returns `TailoredCVRead`. Since `content` is `dict[str, Any]`, the `validation` key added in step 2 will be returned automatically. No schema change needed.
**Verify:** `pytest tests/test_tailoring_phase3.py -v` — existing tests continue to pass.

### 4. Fix frontend `generateTailoredCv()` response handling
**File:** `src/lib/applications.ts`
The function currently tries to extract a string from the structured response. Add a handler that detects a `sections`-based response and serialises it to readable text for display, **or** (preferred) return the raw `TailoredCVRead` object so the page component can render sections properly.

**File:** `src/routes/cv-tailoring.tsx`
Change `generatedCv: string` state to `generatedCv: TailoredCVRead | null`. Render the CV as a structured section list instead of a `<pre>`. Add two download buttons:
- "Download DOCX" → `GET /api/v1/tailoring/cv/{id}/export/docx` → `Blob` download.
- "Download PDF" → `GET /api/v1/tailoring/cv/{id}/export/pdf` → `Blob` download.

**Verify:** Manual browser test — generate a CV, confirm sections render, download DOCX and PDF.

### 5. Fix frontend `analyzeCvAgainstJobDescription()` response handling
**File:** `src/lib/applications.ts`
Map `TailoringAnalysisRead` fields to the UI's `TailoringResult` shape:
- `summary` ← `result.recommendations?.tailoring_summary`
- `strengths` ← `result.match_result?.matching_skills?.map(s => s.skill)`
- `gaps` ← `result.match_result?.missing_requirements?.map(r => r.requirement)`
- `match_score` ← computed from `matching_skills.length / (matching_skills.length + missing_requirements.length) * 100`

**Verify:** Manual browser test — run analysis, confirm match score and gaps render correctly.

### 6. Add structured CV preview component (MVP: read-only section list)
**File:** `src/routes/cv-tailoring.tsx` (and optionally a new `src/components/CVPreview.tsx`)
Replace the `<pre>` block with a simple section-by-section render: heading as `<h3>`, `content` as `<p>`, `items` as a `<ul>`. For experience dict items, render title bold, company as subtitle, bullets as `<li>`. This is sufficient for the MVP — no drag-and-drop required.
**Verify:** Manual browser test — generated CV renders in sections with correct headings.

### 7. Write/update tests for validation layer
**File to create:** `tests/test_cv_validator.py`
Cover: supported claims pass, unsupported employer fails, fabricated date fails, known certification passes, unknown certification flagged, quantified figure traceable to original passes. All tests use deterministic inputs — no LLM calls.
**Verify:** `pytest tests/test_cv_validator.py -v` — all tests pass.

---

## Evidence Notes

- **Migration files** present: `a1b2c3d4e5f7`, `b2c3d4e5f6a7`, `c3d4e5f6a7b8`, `be801b690825`, `a7b8c9d0e1f2` — all five tailoring table migrations are present and in order. No missing migration for existing models.
- **Dependencies** (`requirements.txt`): `python-docx==1.2.0`, `reportlab>=4.0,<5.0`, `pypdf>=5.0,<7.0` — all three export/QA libraries are pinned. No new dependency needed for the deterministic validation layer.
- **`openai==3.3.1`** is pinned to the old v3 SDK. The LLM abstraction in `backend/ai/llm.py` wraps this — no change needed unless the SDK is upgraded.
- **Frontend framework:** Bun + Vite + TanStack Router + shadcn/ui. The CV tailoring page is a file-route at `/cv-tailoring`. No test framework found for the frontend in this investigation.
- **No duplicate route** risk: the single `tailoring.py` router handles all phases. The Phase 3 imports are deferred (at module level but after the Phase 1 section) to avoid circular imports.

---

*Report written from direct file inspection. No code was modified.*
