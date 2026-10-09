# Code walkthrough

A guided tour of how the AI Research Assistant works: what each component does, and what happens in the code when you click something. Every link opens the exact file and line it describes.

**Suggested way to read it:** read sections 1–4 once for the big picture. Then, for each feature in section 5, keep the app open in a browser and follow the links as you click through it. Section 11 has questions to check your understanding.

**Contents**
1. [The big picture](#1-the-big-picture)
2. [Map of the files](#2-map-of-the-files)
3. [What happens when the server starts](#3-what-happens-when-the-server-starts)
4. [The data: tables vs. API shapes](#4-the-data-tables-vs-api-shapes)
5. [Feature by feature: following a click through the code](#5-feature-by-feature-following-a-click-through-the-code)
6. [How errors travel from the server to the screen](#6-how-errors-travel-from-the-server-to-the-screen)
7. [The frontend in more detail](#7-the-frontend-in-more-detail)
8. [Configuration and secrets](#8-configuration-and-secrets)
9. [Why it's built this way](#9-why-its-built-this-way)
10. [Known weak spots](#10-known-weak-spots)
11. [Check your understanding](#11-check-your-understanding)

---

## 1. The big picture

The app is **one Python program**. FastAPI does two jobs in the same process:

- it answers **JSON API** requests under `/api/...`, and
- it serves the **frontend** files (HTML, CSS, JavaScript) from the `frontend/` folder at `/`.

```
 BROWSER                                 FASTAPI (one Python process)                 OUTSIDE WORLD
 ───────                                 ────────────────────────────                 ─────────────
 index.html   + js/search.js  ─┐
 library.html + js/library.js  ├─ fetch ─►  routers/    HTTP in / out, status codes
 paper.html   + js/paper.js   ─┘  /api/...     │
   (all three use js/api.js                    ▼
    and js/ui.js)                          services/   the actual logic ───────────►  arXiv, OpenAlex (search)
                                               │                         ───────────►  Gemini (LLM)
                                               ▼
                                          backend/data/library.db  (SQLite: papers, Q&A)
                                          backend/data/pdfs/*.pdf  (stored PDFs)
```

The rule behind this layout is that **the browser only ever talks to our own backend.** The backend calls arXiv, OpenAlex and Gemini, so the Gemini API key never leaves the server.

Every user action follows the same path:

> click → page script (`js/*.js`) → `js/api.js` sends `fetch("/api/...")` → a **router** function receives it → it calls a **service** function, which does the work (database, PDF, LLM, search API) → the router returns JSON → the page script draws the result with helpers from `js/ui.js`.

---

## 2. Map of the files

| Layer | File | Role |
|---|---|---|
| **Startup** | [main.py](backend/app/main.py) | Creates the FastAPI app, registers the routers, serves `frontend/` |
| | [config.py](backend/app/config.py) | Reads settings from `backend/.env` |
| | [database.py](backend/app/database.py) | SQLite connection; creates tables; gives each request a session |
| **Data** | [models.py](backend/app/models.py) | Database tables: `Paper`, `ChatMessage` |
| | [schemas.py](backend/app/schemas.py) | What the API sends and receives (`PaperResult`, `PaperOut`, ...) |
| **Routers** (HTTP only) | [routers/search.py](backend/app/routers/search.py) | `GET /api/search` |
| | [routers/papers.py](backend/app/routers/papers.py) | Save, upload, list, get, delete papers; serve a stored PDF |
| | [routers/assistant.py](backend/app/routers/assistant.py) | Summary, questions, chat history |
| **Services** (the logic) | [search_client.py](backend/app/services/search_client.py) | arXiv search, OpenAlex fallback, arXiv lookup by ID |
| | [library.py](backend/app/services/library.py) | Saving search results and uploads, deleting |
| | [pdf_processor.py](backend/app/services/pdf_processor.py) | Download, read (PyMuPDF), store and delete PDFs |
| | [metadata_extractor.py](backend/app/services/metadata_extractor.py) | Title/authors/year/abstract of an uploaded PDF |
| | [assistant.py](backend/app/services/assistant.py) | Summary (cached) and Q&A with history |
| | [prompts.py](backend/app/services/prompts.py) | Every prompt text sent to the LLM |
| | [llm_client.py](backend/app/services/llm_client.py) | The **only** code that calls the LLM |
| **Frontend** | [index.html](frontend/index.html) + [search.js](frontend/js/search.js) | Search page |
| | [library.html](frontend/library.html) + [library.js](frontend/js/library.js) | Library page and PDF upload |
| | [paper.html](frontend/paper.html) + [paper.js](frontend/js/paper.js) | One paper: summary and Q&A |
| | [api.js](frontend/js/api.js) | One function per backend endpoint |
| | [ui.js](frontend/js/ui.js) | Shared drawing code: paper cards, Markdown, status lines |
| | [style.css](frontend/css/style.css) | Small additions to Pico.css |

**Who calls whom.** Routers call services. Services call each other and the database. Nothing calls a router except FastAPI itself. Two boundaries are kept strict:

- `llm_client.py` is the only module that sends requests to the LLM. It is used by `assistant.py` (summary, Q&A) and `metadata_extractor.py` (upload metadata).
- `pdf_processor.py` is the only module that uses PyMuPDF. It hands plain Python data (`PdfContent`) to everything else.

---

## 3. What happens when the server starts

You run `uvicorn app.main:app --reload` from `backend/`. Uvicorn imports `app/main.py` and looks for the variable `app` in it. In order:

1. **Settings load.** Importing `main.py` imports `config.py`, which creates [`Settings()`](backend/app/config.py#L10). pydantic-settings fills each field from, in priority order: an environment variable with the same name, then `backend/.env`, then the default written in the class. The `.env` path is built from the file's own location ([`BACKEND_DIR`](backend/app/config.py#L7)), so it works no matter which folder you start from.
2. **Database engine.** [`database.py`](backend/app/database.py#L10) prepares a connection to `backend/data/library.db`. Nothing is created on disk yet.
3. **App and routes.** [`main.py`](backend/app/main.py#L22-L29) creates the FastAPI app, adds the three routers' endpoints, and defines `GET /api/health`.
4. **Frontend mount, last.** [`app.mount("/", StaticFiles(...))`](backend/app/main.py#L34) serves the `frontend/` folder. It **must be last**: a mount at `/` matches every path, so any route registered after it would never be reached. `html=True` makes `/` serve `index.html`.
5. **Tables created.** When the server actually starts, [`lifespan`](backend/app/main.py#L17) runs [`create_db()`](backend/app/database.py#L16). It creates `backend/data/` and any missing tables. It never changes existing tables, so after you change a column in `models.py` you must delete `library.db` (there are no migrations).

Then you open `http://localhost:8000`, and the browser receives `index.html` from the static mount. The page's script calls `GET /api/health` to fill in the "Backend: ok" footer ([search.js:18](frontend/js/search.js#L18)).

> **Why plain `def` instead of `async def`?** Most route functions are ordinary `def`s. FastAPI runs those in a thread pool, so a slow PDF download, LLM call or the arXiv throttle's `sleep` doesn't freeze other requests. An `async def` that does blocking work would block the whole server.

---

## 4. The data: tables vs. API shapes

There are two kinds of "model" classes. Keeping them separate is a deliberate choice.

### Database tables ([models.py](backend/app/models.py))

[`Paper`](backend/app/models.py#L13) is one row per saved paper:

| Column | What it holds |
|---|---|
| `source`, `external_id` | Where the paper came from (`arxiv` / `openalex` / `upload`) and its ID there. For uploads, the ID is the **SHA-256 hash of the file**. The pair is `UNIQUE`, so the same paper can't be saved twice |
| `title`, `authors`, `year`, `abstract` | The metadata shown on cards. `authors` is a Python list stored as JSON text |
| `url`, `pdf_url` | Landing page and remote PDF link |
| `pdf_path` | The local copy, **relative** to the data folder: `pdfs/<random>.pdf` |
| `full_text` | All text extracted from the PDF. **This is what the LLM reads** |
| `text_status` | `full` (we have the PDF text), `abstract_only` (no PDF existed), `failed` (the PDF couldn't be downloaded or read) |
| `summary` | The cached LLM summary |

[`ChatMessage`](backend/app/models.py#L36) is one question or one answer: `paper_id`, `role` (`user`/`assistant`), `content`.

### API shapes ([schemas.py](backend/app/schemas.py))

| Class | Used for |
|---|---|
| [`PaperResult`](backend/app/schemas.py#L9) | One search result. Not in the database. arXiv and OpenAlex results are both converted to this one shape |
| [`PaperOut`](backend/app/schemas.py#L22) | A saved paper as the API returns it |
| [`SummaryOut`](backend/app/schemas.py#L47) | `{summary, based_on, truncated}` |
| [`AskRequest`](backend/app/schemas.py#L57) / [`AnswerOut`](backend/app/schemas.py#L61) | `{question}` in, `{answer}` out |
| [`ChatMessageOut`](backend/app/schemas.py#L65) | One message of the history |

**Why not just send `Paper`?** `full_text` can be 100,000+ characters, and the library list would carry all of it for every paper. `pdf_path` is a server detail. And a search result isn't a database row yet.

**How a `Paper` becomes a `PaperOut`.** Routes declare `response_model=PaperOut` but return a `Paper`. FastAPI converts the object field by field into a `PaperOut`, and anything `PaperOut` doesn't declare (`full_text`, `summary`) is dropped. `pdf_path` is declared but marked `exclude=True`: it is read so that [`has_pdf`](backend/app/schemas.py#L42) can be computed, then left out of the JSON.

**Sessions.** A *session* is a unit of database work. [`get_session()`](backend/app/database.py#L22) opens one per request and closes it afterwards. Routes ask for it with `session: SessionDep` (defined in [routers/papers.py](backend/app/routers/papers.py#L18)); FastAPI's `Depends` calls `get_session` and passes the result in. That is FastAPI's *dependency injection*.

---

## 5. Feature by feature: following a click through the code

### 5.1 Search for papers

**You:** type "graph neural networks" and press Search.

1. **The form is a plain HTML GET form** ([index.html:24](frontend/index.html#L24)). Submitting it navigates to `index.html?q=graph+neural+networks&source=auto`, with no JavaScript involved. Because the query is in the URL, a refresh or the back button repeats the same search.
2. **The page script reads the URL** when the page loads ([search.js:119-127](frontend/js/search.js#L119-L127)) and calls `runSearch`.
3. **[`runSearch`](frontend/js/search.js#L92)** shows "Searching..." and starts two requests at once ([line 97](frontend/js/search.js#L97)): the search, and the library list (so results you already saved show **Saved** instead of a Save button).
4. **[`searchPapers`](frontend/js/api.js#L35)** in `api.js` sends `GET /api/search?q=...&limit=20&source=auto` through the shared [`request()`](frontend/js/api.js#L5).
5. **The router** ([routers/search.py](backend/app/routers/search.py#L14)) has FastAPI check the parameters first (`q` 1–300 characters, `limit` ≤ 50, `source` must be `auto` or `openalex`; anything else gets an automatic 422). It then calls the service and turns a `SearchError` into HTTP 502.
6. **[`search_papers`](backend/app/services/search_client.py#L42)** decides the source. `openalex` means OpenAlex only. `auto` tries arXiv, and if arXiv raises `SearchError`, it [falls back to OpenAlex](backend/app/services/search_client.py#L48).
7. **[`search_arxiv`](backend/app/services/search_client.py#L55)**, step by step:
   - [Builds the query](backend/app/services/search_client.py#L61) `all:graph AND all:neural AND all:networks`. Without `AND`, arXiv matches papers containing *any* of the words.
   - [Returns a cached result](backend/app/services/search_client.py#L65) if the same search ran in the last hour.
   - [Skips arXiv entirely](backend/app/services/search_client.py#L68) for 10 minutes after any failure. arXiv blocks an IP for 20+ minutes when it sees too many requests, and retrying during a block can extend it.
   - [Waits its turn](backend/app/services/search_client.py#L71) via [`_wait_for_turn`](backend/app/services/search_client.py#L158): at most one arXiv request every 3 seconds, enforced with a lock.
   - Sends the request with `httpx`. arXiv answers in Atom XML, which [`feedparser`](backend/app/services/search_client.py#L79) parses.
   - [`_arxiv_entry_to_result`](backend/app/services/search_client.py#L168) converts each entry to a `PaperResult`. It strips the version suffix (`2101.00001v2` → `2101.00001`), so v1 and v2 count as the same paper.
8. **[`search_openalex`](backend/app/services/search_client.py#L91)** is simpler: one JSON request. OpenAlex stores abstracts as an *inverted index* (`{"graph": [0, 7], "neural": [1], ...}`, each word mapped to its positions), so [`_rebuild_abstract`](backend/app/services/search_client.py#L200) puts the words back in order.
9. **Back in the browser:** if the user chose arXiv but the results came from OpenAlex, a note says so ([search.js:104](frontend/js/search.js#L104)). Each result is drawn with [`renderPaperCard`](frontend/js/ui.js#L52) plus a Save button.

> The cache, the cooldown timer and the throttle are ordinary module-level variables ([search_client.py:28-31](backend/app/services/search_client.py#L28-L31)). They live in memory, so restarting the server resets them.

### 5.2 Save a paper and browse the library

**You:** click **Save to library** on a result.

1. **[The click handler](frontend/js/search.js#L38)** disables the button, shows "Saving (downloading PDF)...", and calls [`savePaper`](frontend/js/api.js#L41). That sends `POST /api/papers` with the **whole search result** as JSON, so the backend doesn't have to search again.
2. **[`save_paper`](backend/app/routers/papers.py#L26)** receives it. FastAPI validates the body as a `PaperResult`; since `source` must be `arxiv` or `openalex`, nobody can fake an "upload" through this endpoint. An `AlreadySavedError` becomes HTTP 409.
3. **[`library.save_search_result`](backend/app/services/library.py#L22)** does the work:
   - [Checks for a duplicate first](backend/app/services/library.py#L27), before spending seconds on a download.
   - If the result has a `pdf_url`: [`download_pdf`](backend/app/services/pdf_processor.py#L91) (streamed, capped at 25 MB) → [`read_pdf`](backend/app/services/pdf_processor.py#L58) extracts the text → [`store_pdf`](backend/app/services/pdf_processor.py#L114) writes `data/pdfs/<random>.pdf` → `text_status = "full"`.
   - If the download or reading fails: [`text_status = "failed"`](backend/app/services/library.py#L36). No `pdf_url` at all (common for OpenAlex): `abstract_only`. **The paper is saved either way.**
   - [`_insert`](backend/app/services/library.py#L93) commits the row. If two requests race (a double click), the database's `UNIQUE` constraint rejects the second one with an [`IntegrityError`](backend/app/services/library.py#L97). The code then rolls back, deletes the PDF it just stored, and reports "already saved".
4. **The response** is a `PaperOut`. The button turns into **Saved** with an "Open in library" link, plus a warning badge if only the abstract is available.

**This is why summaries are based on the paper's content.** Saving runs the same PDF pipeline as uploading, so a paper saved from search has its `full_text` too.

**The Library page** ([library.js](frontend/js/library.js)):
- [`loadLibrary`](frontend/js/library.js#L46) calls `GET /api/papers` ([`list_papers`](backend/app/routers/papers.py#L55): all papers, newest first) and draws each with [`renderLibraryCard`](frontend/js/library.js#L16), which is `renderPaperCard` plus **Open** and **Delete**.
- **Delete** asks for confirmation and sends `DELETE /api/papers/{id}`. [`library.delete_paper`](backend/app/services/library.py#L75) deletes the paper's chat messages ([explicitly](backend/app/services/library.py#L79), because SQLite doesn't enforce foreign keys by default), then the paper, then its PDF file.

**Persistence:** everything lives in the files `backend/data/library.db` and `backend/data/pdfs/`, which is why saved papers survive a refresh and a server restart.

### 5.3 Upload a PDF

**You:** choose a PDF on the Library page.

1. **Choosing the file starts the upload.** The input's [`change` event](frontend/js/library.js#L83) calls [`upload`](frontend/js/library.js#L61), and [`uploadPaper`](frontend/js/api.js#L46) wraps the file in a `FormData`, which the browser sends as `multipart/form-data` to `POST /api/papers/upload`.
2. **[`upload_paper`](backend/app/routers/papers.py#L38)** checks the obvious things: the name must end in `.pdf` ([415](backend/app/routers/papers.py#L41)), and it [reads at most 25 MB + 1 byte](backend/app/routers/papers.py#L43) (one byte more than allowed means the file is too big: 413). A `PdfError` becomes 422 and a duplicate becomes 409.
3. **[`library.save_upload`](backend/app/services/library.py#L42)** runs the pipeline:

   **a. Read the PDF:** [`pdf_processor.read_pdf`](backend/app/services/pdf_processor.py#L58)
   - [Checks for the `%PDF-` header](backend/app/services/pdf_processor.py#L61), opens the file from memory with PyMuPDF, and rejects [password-protected](backend/app/services/pdf_processor.py#L69) files.
   - Extracts every page's text, expanding ligatures (the single glyph "ﬁ" becomes "fi").
   - For page 1, [`_lines`](backend/app/services/pdf_processor.py#L134) also records each line's **font size, position, and whether it's horizontal**. The metadata heuristics need these. The rotated arXiv stamp in the margin is the non-horizontal one.
   - [Rejoins words hyphenated across line breaks](backend/app/services/pdf_processor.py#L82) ("representa-\ntion" → "representation").
   - [Rejects scanned PDFs](backend/app/services/pdf_processor.py#L83): fewer than 200 characters per page means the pages are images, which would need OCR.
   - Returns a [`PdfContent`](backend/app/services/pdf_processor.py#L50): plain data, no PyMuPDF objects.

   **b. Guess the metadata from the layout:** [`metadata_extractor.extract_metadata`](backend/app/services/metadata_extractor.py#L68), pure Python heuristics:
   - **Title:** [`_title_lines`](backend/app/services/metadata_extractor.py#L116) finds the run of consecutive lines in the largest font in the top half of page 1. If that doesn't look like a title, it falls back to the PDF's title field, then the file name.
   - **Authors:** the PDF's author field if it holds real names; otherwise [`_authors_from_lines`](backend/app/services/metadata_extractor.py#L141) collects name-like text between the title and "Abstract", skipping emails and affiliations. *This is the weakest heuristic.*
   - **Year** ([`_year`](backend/app/services/metadata_extractor.py#L176)), from the first source that has one: the arXiv ID (`1706.03762` → 2017), a venue header like "Proceedings ... 2019", the PDF's subject field, or the file's creation date.
   - **Abstract** ([`_abstract`](backend/app/services/metadata_extractor.py#L194)): the text between an "Abstract" heading and "Introduction" / "Keywords" / the first footnote.
   - **arXiv ID** ([`_arxiv_id`](backend/app/services/metadata_extractor.py#L168)): `arXiv:1706.03762` in the rotated margin stamp.

   **c. If an arXiv ID was found**, [`search_client.fetch_arxiv_paper`](backend/app/services/search_client.py#L111) reads arXiv's exact metadata from the `citation_*` meta tags of the paper's abs page ([library.py:52](backend/app/services/library.py#L52)). No LLM is needed. The paper is stored as `source="arxiv"`, so it counts as a duplicate of the same paper saved from search.

   **d. Otherwise** the paper's ID is the [file's SHA-256 hash](backend/app/services/library.py#L58), so uploading the same file twice is refused, and that check happens before any LLM call. Then [`improve_with_llm`](backend/app/services/metadata_extractor.py#L88) sends the first ~8,000 characters (about 2 pages) to the LLM, asking for JSON `{title, authors, year, abstract}` ([prompt](backend/app/services/prompts.py#L58)). **Each field the LLM returns replaces the heuristic value only if it looks plausible** ([line 109](backend/app/services/metadata_extractor.py#L109) onward). If the LLM call fails, or in mock mode, the heuristic values stay.

   **e.** The full text, `text_status = "full"` and the stored PDF are attached, and the row is inserted.
4. **The browser** puts the new card at the top of the list, with the same `renderPaperCard` as search results, marked "Uploaded PDF".

### 5.4 Summarize a paper

**You:** open a paper and click **Summarize**.

1. **Opening `paper.html?id=7`:** [paper.js](frontend/js/paper.js#L187-L191) checks the id is a number and calls [`loadPaper`](frontend/js/paper.js#L32). That fetches `GET /api/papers/7`, draws the card, and then runs `loadSummary` and `loadChat`.
2. **[`loadSummary`](frontend/js/paper.js#L52)** calls `GET /api/papers/7/summary`, which **never calls the LLM**. It returns the cached summary (or `null`), `based_on` (`full_text` / `abstract` / `none`) and `truncated`. [`showContextNote`](frontend/js/paper.js#L64) uses the last two to warn you, e.g. "based on its abstract only".
3. **The click** ([paper.js:91](frontend/js/paper.js#L91)) sends `POST /api/papers/7/summary?refresh=false` (or `refresh=true` for **Regenerate**).
4. **[`create_summary`](backend/app/routers/assistant.py#L42)** loads the paper (404 if missing) and calls the service inside [`with _llm_errors():`](backend/app/routers/assistant.py#L19), which converts exceptions to HTTP status codes (section 6).
5. **[`assistant.summarize`](backend/app/services/assistant.py#L39):** only [if there is no cached summary or `refresh` is set](backend/app/services/assistant.py#L44), it builds the prompt, calls the LLM and saves the result in `paper.summary`. When you reopen the page later, step 2 shows the stored summary without any LLM call.
6. **[`_paper_prompt`](backend/app/services/assistant.py#L82)** chooses what the model reads: `full_text` if there is any, otherwise the abstract, otherwise `NoTextError`. Text longer than `LLM_MAX_PAPER_CHARS` (200,000 characters, ~50K tokens) is [cut at a paragraph break](backend/app/services/assistant.py#L88-L92), and `truncated` is set so the page can say so.
7. **[`prompts.summary_messages`](backend/app/services/prompts.py#L81)** builds the conversation sent to the model:

   ```
   [ {"role": "system", "content": "You are a research assistant ... base everything on the paper ...
                                    <paper> Title: ...  Authors: ...  Year: ...  <the full text> </paper>"},
     {"role": "user",   "content": "Summarize this paper ... ## Problem  ## Approach  ## Experiments and results
                                    ## Contributions  ## Limitations ... under ~500 words ... concrete details"} ]
   ```

   The whole paper goes into the prompt: no chunking, no vector database. A paper is 10–30K tokens and the model's context holds 1M.
8. **[`llm_client.chat`](backend/app/services/llm_client.py#L51):**
   - With `LLM_MOCK=true` it [waits 0.8 s and returns canned text](backend/app/services/llm_client.py#L53).
   - Otherwise [`_complete`](backend/app/services/llm_client.py#L75) sends one HTTP request in the **OpenAI chat-completions format**, which Gemini also accepts:

     ```
     POST https://generativelanguage.googleapis.com/v1beta/openai/chat/completions
     Authorization: Bearer <LLM_API_KEY>
     {"model": "gemini-3.5-flash-lite", "messages": [...]}
       → {"choices": [{"message": {"content": "## Problem\n..."}}]}
     ```

   - [The retry loop](backend/app/services/llm_client.py#L85-L102): on HTTP 429 (rate limit) or 5xx (overloaded), it waits 3 s, then 8 s, then 20 s, or however long the provider asks if that is under 30 s. If the **daily** quota is used up (the response mentions `PerDay`), waiting won't help, so it stops at once. When retries run out, [`_error_for`](backend/app/services/llm_client.py#L126) turns the response into an `LLMError` with a message meant for the user.
   - [`_reply_text`](backend/app/services/llm_client.py#L155) pulls out `choices[0].message.content`.
9. **The browser** shows the summary with [`renderMarkdown`](frontend/js/ui.js#L119): *marked* converts Markdown to HTML, its KaTeX extension renders `$...$` math, and *DOMPurify* removes anything dangerous before the HTML is inserted ([ui.js:127](frontend/js/ui.js#L127)).

> Because the model is called through the generic OpenAI-style format with plain `httpx` (no Google SDK), switching to another provider only means changing `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` in `.env`.

### 5.5 Ask a question

**You:** type "What datasets are used?" and press Enter (or click a suggested question).

1. **[`ask`](frontend/js/paper.js#L142)** ignores the click if a question is already being answered, shows your question bubble immediately, and calls [`askQuestion`](frontend/js/api.js#L81): `POST /api/papers/7/ask` with `{"question": "..."}`.
2. **[`ask_question`](backend/app/routers/assistant.py#L51)**: `AskRequest` requires 1–2000 characters, and a question that is only spaces is rejected with 422.
3. **[`assistant.ask`](backend/app/services/assistant.py#L52):**
   - [Loads the last 6 messages](backend/app/services/assistant.py#L58) (3 questions and 3 answers), oldest first, so follow-ups like "how does *it* compare..." make sense.
   - Builds `[system(paper), Q1, A1, Q2, A2, Q3, A3, new question]` with [`prompts.question_messages`](backend/app/services/prompts.py#L85) and calls the LLM.
   - [Only after the LLM answers](backend/app/services/assistant.py#L60) are the question and the answer saved as two `ChatMessage` rows. A failed call leaves no half-finished conversation.
4. **The browser** adds the answer bubble (Markdown rendered). On an error, it [removes your question bubble](frontend/js/paper.js#L157) and leaves the text in the input, so you can retry.
5. **Reopening the page:** [`loadChat`](frontend/js/paper.js#L109) calls `GET /api/papers/7/messages` and redraws the history.

> The LLM API keeps no memory between calls, so the whole paper is sent again with every question. That is normal for chat APIs, and fine on Gemini's free tier.

---

## 6. How errors travel from the server to the screen

Every failure follows the same path:

```
service raises an exception with a user-facing message     e.g. PdfError("The PDF is password-protected.")
   ▼
router turns it into HTTPException(status, detail)         e.g. 422
   ▼
FastAPI responds with JSON  {"detail": "The PDF is password-protected."}
   ▼
api.js request() throws an Error(detail) with .status      (frontend/js/api.js, line 5)
   ▼
the page's catch block shows it: showError(statusEl, "Upload failed: The PDF is password-protected.")
```

| Status | Meaning in this app | Raised in |
|---|---|---|
| 404 | Paper not found | [`get_paper_or_404`](backend/app/routers/papers.py#L81) |
| 409 | Already in the library | `AlreadySavedError` → [papers.py](backend/app/routers/papers.py#L33) |
| 413 / 415 | PDF too large / not a `.pdf` | [`upload_paper`](backend/app/routers/papers.py#L38) |
| 422 | Bad PDF (damaged, locked, scanned), no text to work with, or invalid input | `PdfError`, `NoTextError`, FastAPI validation |
| 429 | LLM rate limit or daily quota | `LLMRateLimitError` → [`_llm_errors`](backend/app/routers/assistant.py#L19) |
| 502 | Search failed, or the LLM call failed | `SearchError`, `LLMError` |
| 503 | LLM not configured, or the key was rejected | `LLMNotConfiguredError` |

The services never import anything HTTP-related. They raise plain Python exceptions, and only the routers know about status codes. That separation makes the services easy to unit-test (HW2).

---

## 7. The frontend in more detail

**Multi-page, no framework.** Each page is an HTML skeleton whose empty elements have `id`s (`#results`, `#papers`, `#summary`, `#chat-log`), plus one script that fills them in. There is no router or build step; following a link loads the next page.

**ES modules.** `<script type="module" src="js/search.js">` lets scripts `import` from each other (`import { searchPapers } from "./api.js"`). Browsers refuse to load modules from `file://` pages, which is why the app must be opened through `http://localhost:8000`.

**[api.js](frontend/js/api.js)** is the only file that knows the backend's URLs. Each endpoint has one small function on top of [`request()`](frontend/js/api.js#L5), which calls `fetch`, and on a non-2xx response throws an `Error` carrying the backend's `detail` message and the HTTP status.

**[ui.js](frontend/js/ui.js)** holds what the pages share:
- [`el(tag, class, text)`](frontend/js/ui.js#L21) creates an element and sets its text with **`textContent`**.
- [`renderPaperCard(paper, {actions, titleHref})`](frontend/js/ui.js#L52) is the reusable "component", used by all three pages. The options let each page add its own buttons (Save, Open/Delete) and choose where the title links to.
- [`renderMarkdown`](frontend/js/ui.js#L119) renders LLM output (marked → KaTeX → DOMPurify).
- [`showLoading` / `showError` / `showMessage` / `clearStatus`](frontend/js/ui.js#L134) manage the status line under each section.

**Why `textContent` and never `innerHTML` for paper data.** `textContent` never interprets HTML. A paper whose title contains `<img src=x onerror=alert(1)>` is displayed as those characters instead of running code (an XSS attack). LLM output is the one exception, because Markdown has to become HTML; that HTML goes through DOMPurify first.

**Libraries from a CDN** (pinned versions in the `<head>` of each page): Pico.css styles plain semantic HTML with almost no custom CSS. marked, KaTeX, marked-katex-extension and DOMPurify are loaded only on [paper.html](frontend/paper.html#L11-L15), as classic scripts that create global variables (`window.marked`, ...). `renderMarkdown` checks they exist and falls back to plain text if the CDN didn't load.

---

## 8. Configuration and secrets

- All settings are in [`Settings`](backend/app/config.py#L10); the README's Configuration table lists them. Restart the server after editing `.env`.
- **The Gemini key lives only in `backend/.env`**, which `.gitignore` excludes. Only `llm_client.py` reads it, and it runs on the server, so the browser never receives it. `.env.example` (committed) lists the variables without a key.
- **`LLM_MOCK=true`** makes `chat()` return canned text and `chat_json()` return `{}`, so the app works without a key or quota, and tests (HW2) can run offline.
- **`settings.data_path`** (not `data_dir`) is the absolute data folder; a relative `DATA_DIR` is resolved against `backend/` ([config.py:26](backend/app/config.py#L26)).

---

## 9. Why it's built this way

The full reasoning is in [PLAN.md §9](PLAN.md#9-key-design-decisions). In short:

1. **Every paper gets its full text.** Requirement 4 says summaries must use the paper's content, so saving a search result downloads its PDF and runs the same pipeline as an upload.
2. **The whole paper goes into the prompt; no RAG.** Papers fit easily in a 1M-token context, and it's simpler and more accurate than retrieving chunks.
3. **One LLM module.** Swapping providers or mocking the LLM happens in one place.
4. **Staying within free tiers:** summaries are cached, each action makes at most one LLM call, and retries with backoff come before an error message.
5. **One result format.** arXiv and OpenAlex both become `PaperResult`, so adding OpenAlex didn't touch anything else.
6. **Thin routers, logic in services.** HTTP details stay in one layer; the logic is plain functions that are easy to test.

---

## 10. Known weak spots

Good to know for the demo, for the report, and as test targets for HW2:

- **Author extraction** for non-arXiv PDFs is the least reliable step. The LLM usually fixes it, but there's no way to edit metadata in the app.
- **No migrations:** changing a column in `models.py` requires deleting `library.db`.
- **arXiv's state lives in memory** (cache, cooldown, throttle): it's per process and lost on restart.
- **Everything is synchronous:** saving waits for the PDF download, and an upload waits for an LLM call. Fine for one user, but there are no progress updates.
- **OpenAlex results** sometimes lack an abstract (~15%) or a PDF (so the paper is saved as "Abstract only").
- **Very long papers** are cut at 200,000 characters (the page says so).
- **Single user, no login.** There is one shared library. By default uvicorn only accepts connections from this computer, so that's fine locally, but it would need accounts before going online.

---

## 11. Check your understanding

Try answering before opening each one.

<details><summary>1. Why must <code>app.mount("/")</code> be the last line in <code>main.py</code>?</summary>

A mount at `/` matches every path. Any route registered after it would be swallowed by the static-file server and return 404.
</details>

<details><summary>2. What stops the same paper from being saved twice?</summary>

Two layers. `_ensure_not_saved` checks the database before the slow work (download / LLM call) and raises `AlreadySavedError` → 409. For two requests that arrive at the same moment, the `UNIQUE(source, external_id)` constraint makes the second insert fail with an `IntegrityError`, which `_insert` also turns into `AlreadySavedError`. Uploads use the file's SHA-256 as `external_id`; uploaded arXiv papers use their arXiv ID, so they also collide with the same paper saved from search.
</details>

<details><summary>3. A paper saved from OpenAlex had no PDF. What does the summary use, and how does the user know?</summary>

Its `text_status` is `abstract_only` and `full_text` is empty, so `_paper_prompt` uses the abstract and adds a note telling the model only the abstract is available. The user sees an "Abstract only" badge on the card and a note on the paper page (`based_on: "abstract"`).
</details>

<details><summary>4. How does the summary end up based on the content and not only the metadata?</summary>

The prompt's `<paper>` block contains `full_text`, the text extracted from the whole PDF, for both uploads and papers saved from search (saving downloads the PDF). The summary prompt explicitly asks for details from the body, such as how the method works and the numbers it reports.
</details>

<details><summary>5. How would you switch to a different LLM model?</summary>

Change `LLM_MODEL` (and `LLM_BASE_URL` / `LLM_API_KEY` for another provider) in `backend/.env` and restart. No code changes, because `llm_client.py` speaks the generic OpenAI-compatible format.
</details>

<details><summary>6. What does the user see if Gemini answers 429 every time?</summary>

`_complete` retries three times (about 3 + 8 + 20 seconds, or the provider's suggested wait), then raises `LLMRateLimitError("LLM rate limit reached, try again shortly.")`. The router turns that into HTTP 429, and the page shows "Could not generate the summary: LLM rate limit reached, try again shortly." If the daily quota is used up, it gives up immediately with "try again tomorrow".
</details>

<details><summary>7. Why don't uploads of arXiv PDFs use an LLM call?</summary>

arXiv prints the paper's ID in the margin of its PDFs. `_arxiv_id` finds it, and `fetch_arxiv_paper` reads the exact metadata from arXiv's abs page, which is better than any guess and costs no quota.
</details>

<details><summary>8. Why is <code>full_text</code> not part of <code>PaperOut</code>?</summary>

It can be hundreds of thousands of characters, and the library list would send it for every paper. Only the server needs it (to build prompts).
</details>

<details><summary>9. Why can't the browser see the API key?</summary>

The browser never calls Gemini. It only calls our backend's `/api/...` endpoints, and the backend adds the key when it calls Gemini. The key is read from `backend/.env` on the server and never included in any response.
</details>

<details><summary>10. Why are answers inserted with <code>innerHTML</code> when everything else uses <code>textContent</code>?</summary>

Answers are Markdown, which has to become HTML (headings, lists, math). That HTML is passed through `DOMPurify.sanitize` first, which strips scripts and event handlers. Paper data never needs HTML, so it always uses `textContent`.
</details>

---

### Exploring on your own

- **`http://localhost:8000/docs`** lists every endpoint. Try them there to see the raw JSON.
- **Browser DevTools (F12) → Network tab:** watch the `fetch` calls each click makes, with their request bodies and responses.
- **The uvicorn terminal** logs every request and status code, plus warnings when an LLM call is retried.
