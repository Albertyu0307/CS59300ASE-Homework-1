"""FastAPI entry point. Run from backend/:  uvicorn app.main:app --reload"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import BACKEND_DIR
from app.database import create_db
from app.routers import papers, search

FRONTEND_DIR = BACKEND_DIR.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    create_db()
    yield


app = FastAPI(title="AI Research Assistant", lifespan=lifespan)
app.include_router(search.router)
app.include_router(papers.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Mounted last: a mount at "/" matches every path, so /api routes must be registered first.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
