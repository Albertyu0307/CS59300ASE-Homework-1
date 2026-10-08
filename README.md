# AI Research Assistant

CS 59300 ASE, Homework 1. Author: Albert Yu

A web app for working with research papers: search arXiv (or OpenAlex), save papers to a local library, upload your own PDFs, and get an LLM-generated summary of a paper and answers to questions about it. It was built with the coding agent Claude Code.

## Features

The app has three pages: **Search**, **Library**, and a **paper page** for each saved paper. A typical session:

1. On the Search page, search for a topic (e.g. `graph neural networks`) and click **Save to library** on a result.
2. On the Library page, upload a PDF of your own.
3. Click a paper's title (or **Open**) to go to its paper page.
4. Click **Summarize**, then type a question or click one of the suggested questions.

### 1. Search for papers

- Enter keywords or a topic. Each result shows the title, authors, publication year and abstract (long ones are collapsed behind **Show more**), with links to the paper and its PDF.
- Results come from **arXiv** by default. arXiv's API often rate-limits (see [Limitations](#limitations-and-troubleshooting)), so when it fails, the app searches **OpenAlex** instead and says so above the results. A picker next to the search box can also be set to OpenAlex only; the browser remembers the choice.

### 2. Save papers to a local library

- **Save to library** stores the paper in a local SQLite database. Saved papers survive page refreshes and server restarts. Search results that are already saved show **Saved** and a link to the paper.
- Saving also **downloads the paper's PDF and extracts its full text**, so the summary and answers are based on the paper's content, not just its abstract. If a result has no PDF (common for OpenAlex) or the PDF can't be downloaded, the paper is still saved, with an "Abstract only" badge.
- The Library page lists the saved papers, newest first. **Delete** removes a paper together with its stored PDF and its questions and answers.

### 3. Upload PDF papers

- On the Library page, choose a PDF (up to 25 MB) and the upload starts. The app extracts the full text and the paper's **title, authors, year and abstract**, and shows the paper with the same card as a search result, marked "Uploaded PDF".
- The metadata comes from the best available source:
  1. **arXiv PDFs** have their arXiv ID printed in the page margin. The app looks the ID up on arXiv and uses arXiv's exact metadata (no LLM call).
  2. **Other PDFs:** one LLM call reads the first pages and returns the four fields.
  3. **Heuristics** (the largest font on page 1 for the title, the text after "Abstract" for the abstract, and so on) fill in any field the LLM gets wrong or misses. They do all the work in mock mode or when the LLM call fails.
- Files that aren't PDFs, damaged or password-protected PDFs, and scanned PDFs (images of pages, with no text) are rejected with a message saying why. Uploading the same file twice is refused.

### 4. Summarize a paper with an LLM

- On a paper's page, **Summarize** sends the paper's full extracted text to the LLM (Google Gemini) and shows a summary with the sections Problem, Approach, Experiments and results, Contributions, and Limitations. Because the model reads the whole paper, the summary includes details from its body, such as how the method works and the numbers it reports.
- The summary is saved in the database, so it appears instantly the next time; **Regenerate** asks for a new one.
- The page says when the LLM can only see the abstract (no PDF text), and when a very long paper had to be cut to fit the prompt.

### 5. Ask questions about a paper

- Type a question below the summary, or click one of the five suggested questions (the examples from the assignment, such as "What datasets are used?"). The model is told to answer from the paper and to say so when the paper doesn't contain the answer.
- Questions and answers are saved with the paper and shown again when you reopen it. The last three questions and answers are sent along with each new question, so follow-up questions work.
- Summaries and answers are rendered as formatted text, including math. Errors (no API key, rate limit, the LLM service being down) appear as a short message on the page.

## Architecture

The app is one Python program: a **FastAPI** backend that serves both the JSON API (`/api/...`) and the plain HTML/CSS/JavaScript frontend. There is no Node.js, no build step and no second server. The browser only talks to this backend, and the backend calls the external services, so API keys never reach the browser.

```
Browser: index.html (Search) · library.html (Library) · paper.html (one paper)
    │  fetch("/api/...")
    ▼
FastAPI backend (Python)
    routers/   HTTP endpoints, kept thin
    services/  the logic: search, library, PDF processing, metadata, summary and Q&A, LLM client
    │
    ├──► SQLite database   backend/data/library.db (papers, questions and answers)
    ├──► PDF files         backend/data/pdfs/
    └──► External APIs     arXiv (search, PDFs, metadata lookup), OpenAlex (search fallback),
                           Google Gemini (LLM)
```

For example, clicking **Summarize** sends `POST /api/papers/{id}/summary`. `routers/assistant.py` passes it to `services/assistant.py`, which loads the paper's text from SQLite and builds the prompt with `services/prompts.py`. `services/llm_client.py` sends the prompt to Gemini; the summary is saved in the database and returned, and `js/paper.js` renders it.

Main design choices:

- **Every paper has its full text.** Saving a search result runs the same PDF pipeline as an upload, so summaries and answers are based on the paper's content.
- **The whole paper goes in the prompt**, with no RAG or vector database. A typical paper is 10–30K tokens, far below the model's context window; very long papers are cut at `LLM_MAX_PAPER_CHARS`.
- **One module talks to the LLM** (`services/llm_client.py`), through the OpenAI-compatible chat API. Switching to another provider only means editing `.env`, and mock mode gives canned answers without calling any API.
- **Staying within free tiers:** summaries are cached, each user action makes at most one LLM call, and rate-limit and server errors are retried with backoff before the user sees a message.
- **One result format:** search results from arXiv and OpenAlex are converted to the same `PaperResult`, so the rest of the app doesn't care where a paper came from.

| Part | Technology |
|---|---|
| Backend | Python 3.13, FastAPI, Uvicorn |
| Database | SQLite through SQLModel (a single file in `backend/data/`) |
| Paper search | arXiv API, with OpenAlex as the fallback (both free, no key) |
| PDF processing | PyMuPDF |
| LLM | Google Gemini (free tier, `gemini-3.5-flash-lite`) through its OpenAI-compatible API, called with httpx (no SDK); any OpenAI-compatible provider can be configured instead |
| Frontend | Plain HTML, CSS and JavaScript (ES modules); Pico.css (styling), marked and KaTeX (Markdown and math) and DOMPurify (sanitizing) loaded from a CDN |
| Other libraries | httpx (HTTP), feedparser (arXiv's Atom XML), pydantic-settings (`.env`); all versions are pinned in `backend/requirements.txt` |

While the app runs, `http://localhost:8000/docs` lists every API endpoint and lets you try it. The full design and its reasoning (data model, API contract, the comparison of free LLM providers) are in [PLAN.md](PLAN.md).

## Prerequisites

- **Python 3.13** (tested with 3.13.15). Check with `python --version` (Windows) or `python3 --version` (macOS/Linux).
  - Windows: install from [python.org](https://www.python.org/downloads/) or with `winget install Python.Python.3.13`.
  - Debian/Ubuntu: also install the venv module: `sudo apt install python3-venv`.
- **Git**
- An internet connection (the app calls arXiv and Gemini, and the pages load their CSS/JS libraries from a CDN).
- A free **Gemini API key** for the LLM features (summary, questions, metadata of uploaded PDFs). Setup step 7 says how to get one.

## Setup (once)

The steps are the same on every OS. Where a command differs, both versions are shown: the Windows commands are for **PowerShell**.

**1. Clone the repository and enter the backend folder**

```
git clone https://github.com/Albertyu0307/CS59300ASE-Homework-1.git
cd CS59300ASE-Homework-1/backend
```

**2. Create a virtual environment** (a private folder for this project's Python packages)

```powershell
# Windows
python -m venv .venv
```
```bash
# macOS / Linux
python3 -m venv .venv
```

**3. Windows only: allow PowerShell to run the activation script** (macOS/Linux: skip to step 4)

Activating the virtual environment runs a PowerShell script, and Windows blocks scripts by default ("running scripts is disabled on this system"). Check the current setting:

```powershell
Get-ExecutionPolicy
```

If it prints `RemoteSigned`, `Unrestricted` or `Bypass`, scripts are already enabled: skip to step 4. Otherwise (usually `Restricted`), pick **one** of these:

- **Option A: allow scripts permanently for your user account** (one time, no admin rights needed; type `Y` if asked to confirm). Scripts created on your machine can run; scripts downloaded from the internet still need a signature.
  ```powershell
  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
  ```
- **Option B: allow scripts in this PowerShell window only.** Nothing is changed permanently, but you must run it again in every new window before activating.
  ```powershell
  Set-ExecutionPolicy -Scope Process Bypass
  ```

**4. Activate the virtual environment**

```powershell
# Windows
.venv\Scripts\Activate.ps1
```
```bash
# macOS / Linux
source .venv/bin/activate
```

Your prompt now starts with `(.venv)`.

**5. Install the dependencies**

```
pip install -r requirements.txt
```

**6. Create your settings file**

```powershell
# Windows
Copy-Item .env.example .env
```
```bash
# macOS / Linux
cp .env.example .env
```

**7. Add a free Gemini API key**

1. Go to https://aistudio.google.com and sign in with a Google account.
2. Click **Get API key** and create a key in a project **without billing enabled**. Then it can't cost anything: when the free quota runs out, the app shows "LLM rate limit reached" instead.
3. In `backend/.env`, set `LLM_API_KEY=` to the key and change `LLM_MOCK=true` to `LLM_MOCK=false`.

Without a key, skip this step: the app still runs, but summaries and answers are a canned "Mock answer", and uploaded PDFs without an arXiv ID get their metadata from the heuristics only. On Gemini's free tier, Google may use prompts to improve its products, so don't upload confidential papers.

## Running the app

**Windows (PowerShell), copy-paste:** paste this whole block into any PowerShell window and press Enter. It uses Option B from step 3, so it works in every new window. The first line assumes the repository was cloned to your Desktop; if it is somewhere else, change that path.

```powershell
Set-Location "$HOME\Desktop\CS59300ASE-Homework-1\backend"
Set-ExecutionPolicy -Scope Process Bypass -Force
.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```

**Manually:** from the `backend` folder, with the virtual environment activated. In a new terminal, activate it again first (step 4; on Windows with Option B, step 3 too):

```
uvicorn app.main:app --reload
```

Then open **http://localhost:8000** in a browser; the footer of the Search page should say "Backend: ok". Stop the server with `Ctrl+C`.

- Always open the app through `http://localhost:8000`. Opening the HTML files directly from disk does not work.
- `http://localhost:8000/docs` shows interactive documentation for every API endpoint.

## Configuration

Settings are read from `backend/.env` (created in setup step 6) when the server starts, so restart the server after changing them. Every setting has a default, so the file is optional.

| Variable | Default | Meaning |
|---|---|---|
| `LLM_BASE_URL` | Gemini's OpenAI-compatible endpoint | LLM API endpoint; any OpenAI-compatible endpoint works |
| `LLM_API_KEY` | empty | API key (setup step 7) |
| `LLM_MODEL` | `gemini-3.5-flash-lite` | Model name |
| `LLM_MOCK` | `true` | `true` returns canned text instead of calling an LLM, so no key is needed. Set it to `false` once you have a key |
| `LLM_MAX_PAPER_CHARS` | `200000` | Longer paper texts are cut to this many characters before they go to the LLM; the paper page says when that happens |
| `DATA_DIR` | `./data` | Where the database and PDFs are stored, relative to `backend/` |

`.env` is ignored by Git. Never commit a real API key; `.env.example` lists the variables without a key.

## Project structure

```
backend/
  app/
    main.py                   FastAPI app: registers the routers, creates the database, serves frontend/
    config.py                 settings, loaded from backend/.env
    database.py               SQLite connection; creates the tables at startup
    models.py                 database tables: Paper, ChatMessage
    schemas.py                API request and response shapes, e.g. PaperResult (a search result)
    routers/                  HTTP endpoints only; the logic is in services/
      search.py               GET /api/search
      papers.py               save, upload, list, get and delete papers; serve a stored PDF
      assistant.py            summary, questions, chat history
    services/
      search_client.py        arXiv search with the OpenAlex fallback; arXiv lookup by ID
      library.py              saving search results and uploads (one PDF pipeline), deleting
      pdf_processor.py        downloads, stores and reads PDFs (PyMuPDF)
      metadata_extractor.py   title, authors, year and abstract of an uploaded PDF (heuristics + LLM)
      assistant.py            summary (cached) and Q&A with chat history
      prompts.py              every LLM prompt
      llm_client.py           the only module that calls the LLM; retries, mock mode
  tests/                      empty; tests are part of HW2
  requirements.txt            Python dependencies (pinned versions)
  .env.example                template for backend/.env
  data/                       created at runtime, not in Git: library.db and pdfs/
frontend/
  index.html                  Search page
  library.html                Library page: saved papers and PDF upload
  paper.html                  one paper (paper.html?id=N): summary and Q&A
  css/style.css               small additions to Pico.css
  js/
    api.js                    one function per backend endpoint
    ui.js                     shared rendering: renderPaperCard(), renderMarkdown(), status messages
    search.js                 Search page logic
    library.js                Library page logic
    paper.js                  paper page logic
```

## Limitations and troubleshooting

- **"arXiv is unavailable right now, so these results come from OpenAlex."** arXiv's search API blocks a computer for 20+ minutes (HTTP 429) after a handful of requests. The app then uses OpenAlex and tries arXiv again after 10 minutes. Some OpenAlex results have no PDF and are saved as "Abstract only". Downloading arXiv PDFs and recognizing uploaded arXiv papers still work during a block.
- **Answers start with "Mock answer".** `LLM_MOCK` is still `true`; see setup step 7, then restart the server.
- **"LLM rate limit reached" or "The LLM's free daily quota is used up".** Gemini's free tier limits requests per minute and per day. Wait a minute, or until the next day for the daily quota.
- **"The LLM service is unavailable right now (HTTP 503)".** Gemini is overloaded. Try again in a minute.
- **"The LLM provider rejected the API key".** Check `LLM_API_KEY` in `backend/.env`, then restart the server.
- **Uploaded papers' metadata may need checking.** For PDFs without an arXiv ID, the fields come from the LLM and the heuristics and can be wrong (authors are the hardest). The app has no way to edit them.
- **Scanned PDFs are not supported.** There is no OCR, so a PDF made of page images is rejected.
- **Very long papers:** only the first 200,000 characters (about 50K tokens) go to the LLM, and the paper page says so.
- **Port 8000 already in use:** start the server with `uvicorn app.main:app --reload --port 8001` and open `http://localhost:8001`.
- **Single user, local only:** there are no accounts. Everything is stored in `backend/data/`; to start over with an empty library, stop the server and delete that folder.

## Other files in this repository

| File | Contents |
|---|---|
| [PLAN.md](PLAN.md) | The design: requirements, architecture, data model, API contract, design decisions, and the comparison of free LLM providers |
| [TODO.txt](TODO.txt) | The task list by phase, with notes on how each item was tested |
| [CLAUDE.md](CLAUDE.md) | Instructions that Claude Code, the coding agent used to build the app, reads at the start of every session: commands, architecture, known pitfalls |
| [hw1.pdf](hw1.pdf) | The assignment handout |
