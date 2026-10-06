# AI Research Assistant

CS 59300 ASE, Homework 1. Author: Albert Yu

A web app for working with research papers: search arXiv, save papers to a local library, upload your own PDFs, and get an LLM-generated summary of a paper and answers to questions about it.

> **Status:** work in progress. The project skeleton runs; features are being added phase by phase (see [TODO.txt](TODO.txt)).

## Architecture

One Python program: a **FastAPI** backend that serves both the JSON API (`/api/...`) and the plain HTML/CSS/JavaScript frontend. There is no Node.js, no build step and no second server.

| Part | Technology |
|---|---|
| Backend | Python, FastAPI, Uvicorn |
| Database | SQLite (a single file in `backend/data/`) |
| Paper search | arXiv API, with OpenAlex as the fallback when arXiv is rate-limiting (both free, no key). The search page can also be set to use OpenAlex only |
| PDF processing | PyMuPDF |
| Frontend | Plain HTML, CSS and JavaScript; Pico.css, marked and DOMPurify loaded from a CDN |

The design and its reasoning are in [PLAN.md](PLAN.md).

## Prerequisites

- **Python 3.13** (tested with 3.13.15). Check with `python --version` (Windows) or `python3 --version` (macOS/Linux).
  - Windows: install from [python.org](https://www.python.org/downloads/) or with `winget install Python.Python.3.13`.
  - Debian/Ubuntu: also install the venv module: `sudo apt install python3-venv`.
- **Git**
- An internet connection (the app calls arXiv, and the pages load their CSS/JS libraries from a CDN).

No API key is needed to run the app.

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

**6. Create your settings file** (optional for now: the defaults run the app without an LLM)

```powershell
# Windows
Copy-Item .env.example .env
```
```bash
# macOS / Linux
cp .env.example .env
```

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

Then open **http://localhost:8000** in a browser. Stop the server with `Ctrl+C`.

- `http://localhost:8000/docs` shows interactive documentation for every API endpoint.
- Always open the app through `http://localhost:8000`. Opening the HTML files directly from disk does not work.
- `--reload` restarts the server when a Python file changes. Frontend changes only need a browser refresh.

## Configuration

Settings are read from `backend/.env` (created in setup step 6). Every setting has a default, so the file is optional.

| Variable | Default | Meaning |
|---|---|---|
| `LLM_BASE_URL` | empty | LLM API endpoint (provider to be chosen; any OpenAI-compatible endpoint) |
| `LLM_API_KEY` | empty | API key, if the provider needs one |
| `LLM_MODEL` | empty | Model name |
| `LLM_MOCK` | `true` | `true` returns canned text instead of calling an LLM, so no key is needed |
| `DATA_DIR` | `./data` | Where the database and PDFs are stored, relative to `backend/` |

`.env` is ignored by Git. Never commit a real API key; `.env.example` lists the variables without values.

## Project structure

```
backend/
  app/
    main.py        FastAPI app: API routes + serves frontend/
    config.py      settings loaded from .env
    routers/       API endpoints
    services/      search, PDF processing, LLM logic
  requirements.txt
  .env.example
  data/            created at runtime: database and PDFs (not in Git)
frontend/
  index.html       search page
  library.html     saved papers and PDF upload
  paper.html       one paper: summary and Q&A
  css/  js/
```
