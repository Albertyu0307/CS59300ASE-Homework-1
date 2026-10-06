"""GET /api/search: search for papers (results are not saved)."""

from fastapi import APIRouter, HTTPException, Query

from app.schemas import PaperResult
from app.services.search_client import SearchError, SearchSource, search_papers

router = APIRouter(prefix="/api", tags=["search"])


# A plain def (not async): FastAPI runs it in a worker thread, so the arXiv throttle's sleep
# doesn't block other requests.
@router.get("/search")
def search(
    q: str = Query(min_length=1, max_length=300, description="Search words, e.g. graph neural networks"),
    limit: int = Query(20, ge=1, le=50),
    source: SearchSource = Query("auto", description="auto: arXiv, with OpenAlex as the fallback. openalex: OpenAlex only"),
) -> list[PaperResult]:
    try:
        return search_papers(q, limit, source)
    except SearchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
