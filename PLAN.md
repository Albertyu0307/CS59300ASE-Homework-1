# HW1 Plan: AI Research Assistant

Task tracking lives in [TODO.txt](TODO.txt). This file covers the reasoning: what we're building, how it's structured, and in what order.

**Constraints:**
- The project must cost **$0**.
- The frontend is plain HTML/CSS/JavaScript (no React/TypeScript/build tools).
- The LLM provider is **still open** and will be decided at the start of Phase 4 (see §4).

---

## 1. Requirements → tasks

| # | Requirement (from hw1.pdf) | What it means in code | Phase |
|---|---|---|---|
| 1 | Search for papers (title, authors, year, abstract, link) | Backend calls a public search API, normalizes results; frontend renders cards | 1 |
| 2 | Save papers to a local library; survives restart/refresh; browse saved | SQLite DB + CRUD endpoints + Library page | 2 |
| 3 | Upload a PDF; extract title/authors/year/abstract; render like search results | Multipart upload, PDF text extraction, metadata extraction, shared paper-card renderer | 3 |
| 4 | LLM summary based on paper **content**, not just metadata | Full text must be stored for every paper (incl. ones saved from search) | 3 + 4 |
| 5 | Ask natural-language questions about a paper | Q&A endpoint with full text in context + chat UI | 4 |
| — | GUI, frontend+backend, external API, local DB, PDF processing, LLM API | Covered by the above | — |
| — | README, deps/config files, no secrets in git | `.env` + `.env.example`, README | 0, 5 |

Out of scope: user accounts/auth, multi-user, vector databases, background job queues, and tests (tests are HW2).

---

## 2. Architecture

```
                 ┌───────────────────────────────────────┐
                 │  Browser: plain HTML/CSS/JS pages     │
                 │  index.html | library.html | paper.html│
                 └──────────────────┬────────────────────┘
                                    │ fetch("/api/...")  (JSON, multipart)
                                    ▼
┌───────────────────────────────────────────────────────────────┐
│ FastAPI backend (Python)  -- also serves the frontend/ files  │
│  routers/   search.py   papers.py   assistant.py              │
│  services/  search_client  pdf_processor  metadata_extractor  │
│             llm_client  prompts                               │
└───────┬───────────────┬───────────────────┬───────────────────┘
        │               │                   │
        ▼               ▼                   ▼
  SQLite file      PDF files on disk     External APIs (free)
  data/library.db  data/pdfs/*.pdf       • arXiv (search + PDF download)
                                         • LLM API (provider decided in Phase 4)
```

### How many programs?

**One.** FastAPI serves the JSON API under `/api/*` and serves the `frontend/` folder as static files at `/`. Development and the demo run the same way:

```
cd backend
uvicorn app.main:app --reload      ->  open http://localhost:8000
```

Edit an HTML/JS file and refresh the browser. There is no build step, no Node.js and no second server.

All LLM and external-API calls go through the backend. The browser never sees an API key.

---

## 3. Recommended tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend language | **Python 3.13** (installed: 3.13.15) | Best PDF and LLM libraries; least code for this kind of app |
| Web framework | **FastAPI** + Uvicorn | Auto-generated `/docs` page lets you test every endpoint before the UI exists; Pydantic validation; built-in file upload; can serve the static frontend |
| Database | **SQLite** via **SQLModel** | One file, zero setup, meets the persistence requirement. SQLModel (SQLAlchemy + Pydantic) keeps models short |
| Paper search API | **arXiv API** (primary); **OpenAlex** (fallback) | arXiv: free, **no key**, and **every result has a PDF**, which makes requirement 4 (content-based summary) easy. But since 2026 arXiv blocks an IP for 20+ min with HTTP 429 after a handful of requests, so search falls back to OpenAlex (free, no key, generous limits; not every result has a PDF). Semantic Scholar's keyless API was also returning 429 |
| HTTP client | `httpx` | Clean API, timeouts |
| arXiv XML parsing | `feedparser` | arXiv returns Atom XML |
| PDF processing | **PyMuPDF** (`pymupdf`) | Fast, accurate text, and font-size info (useful for title heuristics). AGPL license is fine for coursework; `pypdf` is the permissive fallback |
| LLM | **Open, decided in Phase 4** (§4) | Leading candidates are the Gemini free tier (needs a free key) and OVHcloud's keyless tier |
| Config | `pydantic-settings` + `.env` | Keeps secrets out of code; `.env.example` documents the variables |
| Frontend | **Plain HTML + CSS + JavaScript** (ES modules), one HTML page per screen | Nothing to install or build; every line is readable without learning a framework. See §5 |
| Styling | **Pico.css** (one `<link>` tag from a CDN) | Makes plain semantic HTML look good with almost no custom CSS |
| Markdown rendering | **marked** + **DOMPurify** (`<script>` tags from a CDN) | LLM output is Markdown; DOMPurify sanitizes the HTML before it is inserted |

`backend/requirements.txt` starts as:
`fastapi`, `uvicorn[standard]`, `sqlmodel`, `httpx`, `feedparser`, `pymupdf`, `pydantic-settings`, `python-multipart`.

The LLM SDK is added in Phase 4: `openai` for any OpenAI-compatible endpoint such as OVHcloud, or `google-genai` for Gemini.

**Why not Streamlit/Gradio?** They would be faster to build, but they blur the frontend/backend split the assignment explicitly asks for.

---

## 4. LLM choice: keeping it at $0 (decision deferred to Phase 4)

Phases 0–3 need no LLM. The app is built with `LLM_MOCK=true` (canned responses), and upload metadata comes from heuristics until Phase 4. So this decision can wait until the LLM features are actually being built.

### Using a key is allowed; committing it is not

The handout's rule ("do not commit API keys") is about **git**, not about whether keys may be used. The standard pattern, already part of this plan:
- The real key lives in `backend/.env`, which is gitignored and never leaves your machine.
- `backend/.env.example` is committed, listing the variable names with blank values.
- The README tells anyone who clones the repo to paste their own key, or to run with `LLM_MOCK=true`.

A keyless API is still attractive because it means zero setup for anyone running the app.

### Candidates (researched Oct 5, 2026)

Providers don't publish fixed free-tier numbers. The limits below are from provider docs and third-party reports.

| Option | Key? | Free limits (approx.) | Status |
|---|---|---|---|
| **Google Gemini API** (aistudio.google.com) | Yes (free, no card) | `gemini-3.5-flash-lite`: ~500 req/day; `gemini-3.8-flash`: ~20 req/day; 1M-token context | Strongest on quality and limits |
| **OVHcloud AI Endpoints**, anonymous | **No** | 2 req/min per IP per model. Open models (Qwen3.x, Llama 3.3 70B, gpt-oss, Mistral) with 128K–262K context. OVH states data is not stored | Keyless candidate. **Works, but heavily throttled** in our test (below) |
| Pollinations.ai, anonymous | **No** | ~1 req per 15 s; anonymous traffic is squeezed first under load | Keyless fallback, untested |
| Mistral "Experiment" plan | Yes (phone verification) | ~1 req/s, large monthly token allowance | Backup |
| Groq free tier | Yes | ~8K tokens/min on most chat models | Rejected: one paper (~20K tokens) exceeds it |
| OpenRouter `:free` models | Yes | 50 req/day | Rejected: too few |
| Claude / OpenAI APIs | Yes | No free tier | Rejected: costs money |
| Local model (Ollama) | No | Unlimited, offline | **Deferred by choice.** This PC could run it (RTX 2080 Super 8 GB, 32 GB RAM). If revisited: Ollama defaults to a 4K context on GPUs with <24 GB VRAM, so the context must be raised or papers get silently truncated |

Avoid little-known keyless gateways that show up in "free LLM API" lists: unknown operators, unclear data handling, and they may disappear before grading.

**General risk of keyless tiers:** they are meant for trying things out, and their limits can change without notice. That matters for a recorded demo and for graders running the app later.

### OVHcloud keyless test (Oct 5, 2026)

- **Endpoint:** OpenAI-compatible base URL `https://oai.endpoints.kepler.ai.cloud.ovh.net/v1`.
- **`GET /v1/models` works without a key.** Chat models with large contexts include `Qwen3.8-27B`, `Qwen3.6-27B`, `Qwen3.5-397B-A17B` (262K), and `Meta-Llama-3_3-70B-Instruct`, `gpt-oss-120b`, `Mistral-Small-3.2-24B-Instruct-2506` (131K).
- **Chat test:** a paper-sized prompt (11.5K tokens as counted by the model: a synthetic paper with one fact hidden in the middle), sent to `Qwen3.8-27B` and `Meta-Llama-3_3-70B-Instruct`, with retries every 30 s.
  - **8 of 9 requests returned HTTP 429** "API rate limit exceeded". That includes the first-ever request to Llama and all four to Qwen.
  - **The 9th succeeded** (Llama 3.3 70B, after ~90 s of retries). It answered in **5.4 s** and correctly found the hidden fact.
- **Conclusion:** long prompts work, and the answer quality is fine. The problem is throttling, which looks stricter than the documented 2 req/min. Usable for the app only if `llm_client` retries patiently, and a user might wait a minute or more for an answer.
- **First step of Phase 4:** retest how often requests succeed when spaced a minute apart, then decide whether that wait is acceptable or switch to Gemini.

### Provider-neutral design, so the decision can wait

- `services/llm_client.py` is the **only** module that talks to an LLM. It is configured from `.env`:
  - `LLM_BASE_URL`
  - `LLM_API_KEY` (blank means no key)
  - `LLM_MODEL`
  - `LLM_MOCK`
- OVHcloud, Pollinations and Mistral all speak the OpenAI-style chat API, so one client with a configurable base URL covers them. Gemini would use its own SDK (`google-genai`).
- **Cache every summary in the DB.** Generate it once per paper, and again only when the user clicks "Regenerate".
- **At most one LLM call per user action.** Papers saved from arXiv need no LLM call, since arXiv already provides good metadata.
- **On HTTP 429, retry with backoff**, then show a friendly message ("LLM rate limit reached, try again shortly") instead of crashing.
- **`LLM_MOCK=true`** returns canned text without calling any API. Use it to build the UI without spending quota; it's also handy for HW2 tests.

### How to guarantee $0

- **Keyless tiers** are free by nature.
- **Gemini:** create the key in Google AI Studio in a project with **billing NOT enabled**. When the quota runs out you get HTTP 429, never a bill.
- **Free tiers may use your prompts.** On Gemini's free tier, Google may use prompts to improve its products. That's fine for public papers, but don't upload anything confidential.
- **Everything else is free too:** arXiv (no key), SQLite, Python and all libraries (open source), and GitHub. Run locally for the demo. Online deployment is optional; if you try it, use a free tier that doesn't ask for a card.

---

## 5. Frontend choice: plain HTML/CSS/JS

**Will plain HTML + JavaScript work? Yes.** The UI is three screens and about a dozen dynamic elements: a search box, result cards, an upload button, a summary box and a chat list. Plain JavaScript with `fetch()` handles all of that comfortably.

**Why React/Vite/TypeScript was suggested originally:** reusable components, automatic re-rendering when data changes, and type checking pay off in larger UIs. For this project they aren't worth the cost. You'd have to install Node, learn JSX, hooks and TypeScript, and run a second dev server, all while you're responsible for understanding and debugging what the agent writes. Familiarity matters more than framework power here. The handout lists "suggest a JavaScript framework" only as an *example* of agent use; it is not a requirement.

**What plain JS gives up, and the mitigations:**

| Downside | Mitigation |
|---|---|
| Manual DOM updates get messy as code grows | One JS file per page plus a shared `ui.js`, so no file grows into a 1000-line `app.js` |
| No reusable components | A plain function `renderPaperCard(paper, options)` in `ui.js` is the "component", used by all three pages |
| XSS risk when inserting text | Insert paper data with `textContent`, never `innerHTML`; LLM Markdown goes through DOMPurify |
| ES modules don't work if you double-click the HTML file | Always open the app via `http://localhost:8000` (FastAPI serves it) |

If you ever want a little reactivity without a build step, **Alpine.js** can be added with a single `<script>` tag. It's optional and probably not needed.

**Multi-page instead of a single-page app:** each screen is its own HTML file with its own small script. You don't need a router, the back button just works, and each file is easy to understand on its own. The paper detail page takes its ID from the URL: `paper.html?id=12`.

---

## 6. Project skeleton

```
CS59300ASE-Homework-1/
├── README.md                    # setup/run, features, architecture (submission requirement)
├── PLAN.md                      # this file
├── TODO.txt                     # task tracker
├── .gitignore                   # + .env, backend/data/, .venv/, __pycache__/
│
├── backend/
│   ├── requirements.txt
│   ├── .env.example             # LLM_BASE_URL=  LLM_API_KEY=  LLM_MODEL=  LLM_MOCK=true  DATA_DIR=./data
│   ├── .env                     # real values/secrets -- gitignored
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI app: includes routers, inits DB on startup,
│   │   │                        #   mounts ../frontend as static files at "/" (mounted LAST)
│   │   ├── config.py            # Settings class loaded from .env
│   │   ├── database.py          # SQLite engine, get_session() dependency, create_db()
│   │   ├── models.py            # DB tables: Paper, ChatMessage
│   │   ├── schemas.py           # API shapes: PaperResult, PaperOut, AskRequest, AnswerOut...
│   │   ├── routers/
│   │   │   ├── __init__.py
│   │   │   ├── search.py        # GET /api/search
│   │   │   ├── papers.py        # library CRUD, upload, serve local PDF
│   │   │   └── assistant.py     # summary + Q&A endpoints
│   │   └── services/            # all business logic; routers stay thin
│   │       ├── __init__.py
│   │       ├── search_client.py      # arXiv, OpenAlex fallback -> list[PaperResult]; arXiv abs-page lookup
│   │       ├── library.py            # save a search result / an upload (one PDF pipeline), delete
│   │       ├── pdf_processor.py      # download PDF, save to disk, extract text (the only PyMuPDF user)
│   │       ├── metadata_extractor.py # PDF text -> title/authors/year/abstract
│   │       ├── llm_client.py         # the ONLY module that talks to an LLM (+ mock mode)
│   │       └── prompts.py            # prompt templates in one place
│   ├── data/                    # runtime only, gitignored: library.db, pdfs/
│   └── tests/                   # empty placeholder for HW2
│       └── __init__.py
│
└── frontend/                    # plain static files -- no build step, no npm
    ├── index.html               # Search page: search box + results
    ├── library.html             # Library page: saved papers + PDF upload
    ├── paper.html               # One paper (paper.html?id=12): metadata, summary, Q&A
    ├── css/
    │   └── style.css            # small tweaks on top of Pico.css
    └── js/
        ├── api.js               # one fetch() wrapper per backend endpoint
        ├── ui.js                # shared: renderPaperCard(), showLoading(), showError(), renderMarkdown()
        ├── search.js            # logic for index.html
        ├── library.js           # logic for library.html (list, delete, upload)
        └── paper.js             # logic for paper.html (summary, chat, quick questions)
```

Design rule: **routers stay thin, services hold the logic, and `llm_client.py` is the only file that knows which LLM provider is used.** It also makes HW2's testing easier, because services can be unit-tested and the LLM mocked at a single seam.

---

## 7. Data model (SQLite)

**`paper`**

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `source` | TEXT | `arxiv` / `openalex` / `upload`. An uploaded PDF that carries an arXiv ID is stored as `arxiv` |
| `external_id` | TEXT NULL | arXiv ID, OpenAlex ID, or for uploads the SHA-256 of the file. `UNIQUE(source, external_id)` prevents duplicate saves and repeated uploads |
| `title` | TEXT | |
| `authors` | TEXT | JSON array of names |
| `year` | INTEGER NULL | |
| `abstract` | TEXT NULL | |
| `url` | TEXT NULL | Landing page link |
| `pdf_url` | TEXT NULL | Remote PDF link |
| `pdf_path` | TEXT NULL | Local file, relative to the data folder: `pdfs/<uuid>.pdf` |
| `full_text` | TEXT NULL | Extracted text, which feeds the LLM |
| `text_status` | TEXT | `full` / `abstract_only` / `failed`, shown as a badge in the UI |
| `summary` | TEXT NULL | Cached LLM summary |
| `created_at` | DATETIME | |

**`chatmessage`**: `id`, `paper_id` (FK), `role` (`user`/`assistant`), `content`, `created_at`.

---

## 8. API contract

| Method & path | Body / params | Returns | Notes |
|---|---|---|---|
| `GET /api/health` | — | `{status:"ok"}` | Walking-skeleton check |
| `GET /api/search` | `q`, `limit=20`, `source=auto` | `PaperResult[]` | Not saved. `source=auto`: arXiv, with OpenAlex as the fallback; `source=openalex`: OpenAlex only |
| `POST /api/papers` | `PaperResult` | `PaperOut` | Saves, downloads PDF, extracts text; 409 if already saved. Saved even if the PDF fails (`text_status`) |
| `GET /api/papers` | — | `PaperOut[]` | Library list (no full text, to keep it light). `PaperOut.has_pdf`: a local copy is stored |
| `GET /api/papers/{id}` | — | `PaperOut` | |
| `DELETE /api/papers/{id}` | — | 204 | Also deletes the PDF file |
| `POST /api/papers/upload` | multipart `file` | `PaperOut` | Extract text + metadata, then save. 409 already saved, 413 over 25 MB, 415 not `.pdf`, 422 not a PDF / damaged / password-protected / scanned |
| `GET /api/papers/{id}/pdf` | — | PDF file | The stored copy, shown inline; the cards' "PDF" link uses it when `has_pdf` |
| `POST /api/papers/{id}/summary` | `?refresh=false` | `{summary}` | Cached after the first call |
| `POST /api/papers/{id}/ask` | `{question}` | `{answer}` | Appends to chat history |
| `GET /api/papers/{id}/messages` | — | `ChatMessage[]` | |

The frontend sends the **whole `PaperResult`** when saving, so the backend doesn't have to re-query the search API.

---

## 9. Key design decisions

1. **"Based on content, not metadata" is the trap in requirement 4.** A paper saved from search only has an abstract unless we fetch more. Fix: on save, the backend downloads the PDF (arXiv always has one) and runs the **same** extraction pipeline as uploads, so there is one pipeline with two entry points. If no PDF can be fetched, store `text_status = abstract_only` and show a badge such as "summary based on abstract only".
2. **No RAG and no vector DB.** A typical paper is 10–30K tokens, and every candidate model has a 128K–1M context, so put the whole extracted text in the prompt. That is simpler and gives better answers than retrieving chunks. Very long documents get capped, and the UI says so instead of truncating silently.
3. **Metadata extraction for uploads: heuristics first, the LLM added in Phase 4.**
   - Heuristics: PDF metadata fields, the largest-font line on page 1, and a regex for "Abstract".
   - In Phase 4, send the first ~2 pages to the LLM and request JSON `{title, authors[], year, abstract}`. The heuristics remain the fallback when the LLM fails or is rate-limited.
   - Bonus (done in Phase 3): arXiv stamps its ID up the left margin of its PDFs. When that stamp is found, the exact metadata comes from the paper's arXiv abs page (its `citation_*` meta tags), which needs no LLM call. The abs page isn't rate-limited like the search API.
4. **Synchronous processing is fine.** Upload/save takes a few seconds, so show a spinner. No job queue.
5. **Free-tier discipline** (see §4): cache summaries, at most one LLM call per action, mock mode, and retries plus a friendly message on 429.
6. **Q&A prompt layout:** a fixed system instruction, then the paper text, then recent chat history, then the new question.
7. **Keep the search layer source-agnostic.** Everything becomes a `PaperResult`, so adding the OpenAlex fallback was one more function and no other code changes.
8. **Secrets:** any key goes in `backend/.env` (gitignored), and `backend/.env.example` is committed with blank values. The backend gives a clear message if a required key is missing.
9. **Fallback for bad PDFs:** if text extraction yields almost nothing (a scanned PDF), some providers (e.g. Gemini) accept the PDF file directly. Keep this as a fallback only; extracting text ourselves is visibly the "PDF processing" the rubric asks for.

---

## 10. How to approach it (build order)

Build in **vertical slices**. Finish one feature end-to-end (service → endpoint → test in `/docs` → HTML/JS page) before starting the next, so you always have a working, demoable app and can commit at each step.

| Day | Phase | Goal | Why this order |
|---|---|---|---|
| Mon 10/5 | 0 Setup | Install Python (done); scaffold the backend + `frontend/` folder; `index.html` shows the `/api/health` result; confirm the arXiv API works in a scratch script | Shakes out environment problems on day one, while they're cheap |
| Tue 10/6 | 1 Search, 2 Library | Search results render, Save works, Library persists across restarts | Search produces the data everything else consumes; the DB is needed before anything can be summarized |
| Wed 10/7 | 3 PDF | Upload + heuristic metadata extraction; saving a search result also fetches full text | The LLM features depend on full text existing, so build the text pipeline first |
| Thu 10/8 | 4 LLM | **Decide the provider (§4)**, then Summary + Q&A on `paper.html` and LLM metadata extraction | Built last because it depends on everything above. Use `LLM_MOCK` while building the UI |
| Fri 10/9 | 5 Polish | Error states, styling, README, fresh-clone test, demo video | The README and fresh-clone test catch "works on my machine" issues |
| Sat 10/10 | 6 Submit | Buffer, report, submit | The report is 40% of the grade, so keep this day for it |

**Start here:** Python 3.13 is installed, so ask the agent to create the Phase 0 skeleton from §6 and get the health check showing in the browser.

### Working with the coding agent

- Give it **one phase (or one TODO block) per request**. Point it at `PLAN.md` and `TODO.txt` and ask it to tick items off as they are finished.
- After the skeleton exists, run `/init` so a `CLAUDE.md` captures the layout and run commands for every new session.
- Verify each endpoint yourself in `http://localhost:8000/docs` before moving on. That's your check that the generated code actually works.
- The handout suggests finding and installing a PDF-processing skill. Read any third-party skill's files before installing (the "toxic skills" warning). A built-in `pdf` skill is already available in this Claude Code setup.
- **Screenshot as you go**, especially when the agent gets something wrong and you correct it. The report needs these, and they can't be recreated later.
