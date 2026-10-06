# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

CS 59300 ASE Homework 1: an AI research assistant (search arXiv, save papers to a local library, upload PDFs, LLM summary and Q&A about a paper). Due Sat Oct 10, 2026.

- **PLAN.md** is the design (architecture, data model in §7, API contract in §8, design decisions in §9). **TODO.txt** is the task tracker. Work one phase / TODO block at a time and tick items (`[x]`) as they are finished.
- Hard constraints: **total cost $0**; frontend is **plain HTML/CSS/JS** (ES modules, no React/TypeScript/npm/build step). The LLM provider is undecided until Phase 4 (PLAN.md §4).
- Tests are out of scope for HW1 (HW2 builds on `backend/tests/`).

## Commands

All backend commands run from `backend/` with the venv (Windows; Python 3.13):

```
cd backend
python -m venv .venv                      # once
.venv\Scripts\activate
pip install -r requirements.txt           # versions are pinned
copy .env.example .env                    # once; .env is gitignored
uvicorn app.main:app --reload             # app at http://localhost:8000, API docs at /docs
```

Without activating: `.venv/Scripts/python.exe -m uvicorn app.main:app`. Test endpoints in `/docs` before building UI for them. Always open the frontend through `http://localhost:8000`, never as `file://` (ES modules fail there).

No lint or test setup exists yet. Throwaway experiments go in `scratch/` (gitignored), e.g. `scratch/arxiv_smoke.py`.

## Architecture

**One process.** FastAPI (`backend/app/main.py`) serves the JSON API under `/api/*` and serves `frontend/` as static files at `/`. The static mount must stay **last** in `main.py`, because a mount at `/` swallows every path registered after it. The browser never calls external APIs or sees keys; everything goes through the backend.

**Backend layering** (target layout in PLAN.md §6; modules are added phase by phase):
- `routers/` stay thin; `services/` hold the logic.
- `services/llm_client.py` will be the **only** module that talks to an LLM, configured by `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` / `LLM_MOCK` (mock returns canned text). Rules: cache summaries in the DB, at most one LLM call per user action, retry with backoff on HTTP 429 and then show a friendly message.
- Search results from any source are normalized to one source-agnostic `PaperResult`.
- Saving a search result downloads its PDF and runs the **same** text-extraction pipeline as an upload, so every paper has `full_text` (requirement: summaries based on content, not metadata). No PDF → `text_status = abstract_only`. Whole paper text goes in the prompt; no RAG/vector DB.

**Config:** `app/config.py` loads `backend/.env` via pydantic-settings, located relative to the source file, so it works regardless of the working directory. Use `settings.data_path` (absolute), not `settings.data_dir`: a relative `DATA_DIR` is resolved against `backend/`. Runtime data (SQLite DB, PDFs) lives in `backend/data/`, which is gitignored.

**Frontend:** one HTML page per screen (`index.html` search, `library.html`, `paper.html?id=N`), each with its own script in `js/`. `js/api.js` holds one wrapper per endpoint over a shared `request()` that throws an `Error` carrying the backend's `detail`. `js/ui.js` holds shared rendering (`renderPaperCard()` is the reusable "component"). Insert paper data with `textContent`, never `innerHTML`; LLM Markdown is rendered with marked and then sanitized with DOMPurify. Pico.css, marked and DOMPurify come from jsDelivr with pinned versions; marked v18 has no `marked.min.js`, so use `lib/marked.umd.js`.

## Gotchas

- arXiv API (`https://export.arxiv.org/api/query`, Atom XML parsed with feedparser): join query words with AND (`all:graph AND all:neural AND all:networks`). A plain `all:graph neural networks` matches ANY word and returns off-topic results. Keep to ~1 request per 3 s.
- Windows console output can garble non-ASCII text (e.g. author names); set `PYTHONIOENCODING=utf-8` when printing. The data itself is fine.
- Existing text files use CRLF and `core.autocrlf=true`; Git's "LF will be replaced by CRLF" warnings are harmless.
