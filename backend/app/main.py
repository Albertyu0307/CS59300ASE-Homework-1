"""FastAPI entry point. Run from backend/:  uvicorn app.main:app --reload"""

from fastapi import FastAPI

app = FastAPI(title="AI Research Assistant")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
