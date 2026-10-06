"""FastAPI entry point. Run from backend/:  uvicorn app.main:app --reload"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import BACKEND_DIR

FRONTEND_DIR = BACKEND_DIR.parent / "frontend"

app = FastAPI(title="AI Research Assistant")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Mounted last: a mount at "/" matches every path, so /api routes must be registered first.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
