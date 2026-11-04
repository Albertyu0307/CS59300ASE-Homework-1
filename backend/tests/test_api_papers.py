"""The library API: save a search result, upload a PDF, list, get, delete, serve the stored PDF."""

import hashlib

import httpx
import pytest

from app.routers import papers as papers_router
from app.services import library, metadata_extractor
from tests.conftest import LLM_URL
from tests.factories import arxiv_abs_page, completion, make_pdf, make_scanned_pdf, search_result

PDF_URL = "https://arxiv.org/pdf/2101.00001v2"
PUBLIC_FIELDS = {"id", "source", "external_id", "title", "authors", "year", "abstract", "url", "pdf_url",
                 "text_status", "created_at", "has_pdf"}


def upload(client, data: bytes, filename: str = "paper.pdf", content_type: str = "application/pdf"):
    return client.post("/api/papers/upload", files={"file": (filename, data, content_type)})


@pytest.fixture
def pdf_download(http_mock):
    return http_mock.get(PDF_URL)


# --- POST /api/papers: save a search result -----------------------------------------------------


def test_saving_a_result_downloads_its_pdf_and_stores_the_full_text(client, pdf_download, db, stored_pdfs):
    pdf = make_pdf()
    pdf_download.respond(200, content=pdf, headers={"content-type": "application/pdf"})

    response = client.post("/api/papers", json=search_result())

    assert response.status_code == 201
    saved = response.json()
    assert saved["title"] == "Graph Neural Networks: A Review"
    assert saved["text_status"] == "full"
    assert saved["has_pdf"] is True
    stored = db.paper(saved["id"])
    assert "LongBench and SCROLLS datasets" in stored.full_text  # the body of the PDF, not just metadata
    assert [path.read_bytes() for path in stored_pdfs()] == [pdf]
    assert pdf_download.calls.last.request.headers["user-agent"].startswith("ai-research-assistant/")


def test_response_never_contains_full_text_or_local_paths(client, pdf_download):
    pdf_download.respond(200, content=make_pdf())

    saved = client.post("/api/papers", json=search_result()).json()

    assert set(saved) == PUBLIC_FIELDS


def test_result_without_a_pdf_is_saved_with_its_abstract_only(client, db):
    response = client.post("/api/papers", json=search_result(pdf_url=None))

    assert response.status_code == 201
    assert response.json()["text_status"] == "abstract_only"
    assert response.json()["has_pdf"] is False
    assert db.paper(response.json()["id"]).full_text is None


@pytest.mark.parametrize(
    "download",
    [
        httpx.Response(404),
        httpx.Response(200, text="<html>Please log in</html>"),
        httpx.Response(200, content=make_scanned_pdf()),
        httpx.Response(200, content=make_pdf(password="secret")),
        httpx.ReadTimeout("slow"),
        httpx.ConnectError("offline"),
    ],
    ids=["http-404", "html-instead-of-pdf", "scanned-pdf", "password-protected", "timeout", "unreachable"],
)
def test_result_is_still_saved_when_its_pdf_cannot_be_used(client, pdf_download, db, stored_pdfs, download):
    pdf_download.mock(side_effect=[download])

    response = client.post("/api/papers", json=search_result())

    assert response.status_code == 201
    assert response.json()["text_status"] == "failed"
    assert response.json()["has_pdf"] is False
    assert db.paper(response.json()["id"]).full_text is None
    assert stored_pdfs() == []


def test_saving_the_same_result_twice_is_refused(client, pdf_download):
    pdf_download.respond(200, content=make_pdf())
    client.post("/api/papers", json=search_result())

    response = client.post("/api/papers", json=search_result(title="Retitled", url=None))

    assert response.status_code == 409
    assert response.json()["detail"] == "This paper is already in your library."
    assert pdf_download.call_count == 1  # checked before downloading again


def test_same_external_id_from_another_source_is_a_different_paper(client):
    client.post("/api/papers", json=search_result(pdf_url=None))

    response = client.post("/api/papers", json=search_result(source="openalex", pdf_url=None))

    assert response.status_code == 201
    assert len(client.get("/api/papers").json()) == 2


def test_simultaneous_saves_of_one_paper_store_it_once(client, pdf_download, monkeypatch, db, stored_pdfs):
    """Two requests that both pass the "already saved?" check (e.g. a double click): the database's
    unique constraint stops the second one, and its downloaded PDF is cleaned up."""
    monkeypatch.setattr(library, "_ensure_not_saved", lambda *args: None)
    pdf_download.respond(200, content=make_pdf())

    first = client.post("/api/papers", json=search_result())
    second = client.post("/api/papers", json=search_result())

    assert (first.status_code, second.status_code) == (201, 409)
    assert len(db.papers()) == 1
    assert len(stored_pdfs()) == 1


def test_client_cannot_set_server_side_fields(client, db):
    """Only PaperResult fields are accepted; the text, summary and status are the server's to fill."""
    body = search_result(pdf_url=None, full_text="injected text", summary="fake summary", text_status="full", id=999)

    saved = client.post("/api/papers", json=body).json()

    stored = db.paper(saved["id"])
    assert saved["id"] != 999
    assert (stored.full_text, stored.summary, stored.text_status) == (None, None, "abstract_only")


@pytest.mark.parametrize(
    "body",
    [
        search_result(source="upload"),
        search_result(source="google"),
        {key: value for key, value in search_result().items() if key != "title"},
        {key: value for key, value in search_result().items() if key != "external_id"},
        search_result(authors="Alice Smith"),
        search_result(year="last year"),
        [],
    ],
    ids=["source-upload", "unknown-source", "no-title", "no-external-id", "authors-not-a-list", "bad-year", "not-an-object"],
)
def test_invalid_search_result_is_rejected_with_422(client, body, db):
    assert client.post("/api/papers", json=body).status_code == 422
    assert db.papers() == []


# --- POST /api/papers/upload ---------------------------------------------------------------------


def test_upload_extracts_text_and_metadata(client, db, stored_pdfs):
    pdf = make_pdf(header="Proceedings of the Test Conference 2019")

    response = upload(client, pdf, filename="sparse-attention.pdf")

    assert response.status_code == 201
    paper = response.json()
    assert paper["source"] == "upload"
    assert paper["external_id"] == hashlib.sha256(pdf).hexdigest()
    assert paper["title"] == "Sparse Attention for Long Document Understanding at Scale"
    assert paper["authors"] == ["Alice Smith", "Bob Jones"]
    assert paper["year"] == 2019
    assert paper["abstract"].startswith("We study sparse attention for long documents.")
    assert paper["text_status"] == "full"
    assert paper["has_pdf"] is True
    assert "Longformer and BigBird" in db.paper(paper["id"]).full_text
    assert [path.read_bytes() for path in stored_pdfs()] == [pdf]


def test_upload_filename_extension_is_case_insensitive(client):
    assert upload(client, make_pdf(), filename="PAPER.PDF").status_code == 201


def test_uploading_the_same_file_twice_is_refused(client, stored_pdfs):
    pdf = make_pdf()
    upload(client, pdf, filename="a.pdf")

    response = upload(client, pdf, filename="renamed.pdf")

    assert response.status_code == 409
    assert response.json()["detail"] == "This paper is already in your library."
    assert len(stored_pdfs()) == 1


def test_arxiv_pdf_upload_gets_arxivs_exact_metadata(client, http_mock, monkeypatch):
    http_mock.get("https://arxiv.org/abs/2103.01234").respond(200, text=arxiv_abs_page())
    monkeypatch.setattr(metadata_extractor, "improve_with_llm", lambda *args: pytest.fail("no LLM call for arXiv papers"))

    response = upload(client, make_pdf(arxiv_stamp="arXiv:2103.01234v2 [cs.CL] 5 Mar 2021"))

    assert response.status_code == 201
    paper = response.json()
    assert (paper["source"], paper["external_id"]) == ("arxiv", "2103.01234")
    assert paper["title"] == "Sparse Attention for Long Documents"
    assert paper["authors"] == ["Alice Smith", "Bob Jones"]
    assert paper["url"] == "https://arxiv.org/abs/2103.01234"
    assert paper["has_pdf"] is True  # the uploaded file is kept


def test_arxiv_pdf_upload_falls_back_to_heuristics_when_arxiv_is_unreachable(client, http_mock):
    http_mock.get("https://arxiv.org/abs/2103.01234").mock(side_effect=httpx.ConnectError("offline"))

    response = upload(client, make_pdf(arxiv_stamp="arXiv:2103.01234v2 [cs.CL] 5 Mar 2023"))

    assert response.status_code == 201
    paper = response.json()
    assert paper["source"] == "upload"
    assert paper["title"] == "Sparse Attention for Long Document Understanding at Scale"
    assert paper["year"] == 2021  # from the ID (submitted 2021-03), not the stamp's latest-version date


def test_arxiv_pdf_upload_matches_the_same_paper_saved_from_search(client, http_mock, stored_pdfs):
    http_mock.get("https://arxiv.org/abs/2103.01234").respond(200, text=arxiv_abs_page())
    client.post("/api/papers", json=search_result(external_id="2103.01234", pdf_url=None))

    response = upload(client, make_pdf(arxiv_stamp="arXiv:2103.01234v1 [cs.CL] 2 Mar 2021"))

    assert response.status_code == 409
    assert stored_pdfs() == []


def test_upload_uses_the_llm_for_metadata_when_enabled(client, http_mock, real_llm):
    llm = http_mock.post(LLM_URL).respond(200, json=completion(
        '```json\n{"title": "The Real Title From the LLM", "authors": ["Alice Smith", "Robert Jones"], '
        '"year": 2020, "abstract": null}\n```'
    ))

    paper = upload(client, make_pdf()).json()

    assert llm.call_count == 1
    assert paper["title"] == "The Real Title From the LLM"
    assert paper["authors"] == ["Alice Smith", "Robert Jones"]
    assert paper["year"] == 2020
    assert paper["abstract"].startswith("We study sparse attention")  # LLM had none: heuristic kept


def test_upload_still_works_when_the_llm_fails(client, http_mock, real_llm):
    http_mock.post(LLM_URL).respond(429, text='{"error": "quota GenerateRequestsPerDayPerProjectPerModel"}')

    response = upload(client, make_pdf())

    assert response.status_code == 201
    assert response.json()["title"] == "Sparse Attention for Long Document Understanding at Scale"


def test_duplicate_upload_is_refused_before_spending_an_llm_call(client, http_mock, real_llm):
    llm = http_mock.post(LLM_URL).respond(200, json=completion("{}"))
    pdf = make_pdf()
    upload(client, pdf)

    upload(client, pdf)

    assert llm.call_count == 1


@pytest.mark.parametrize("filename", ["paper.txt", "paper.pdf.exe", "paper", "notes.docx"])
def test_upload_of_a_non_pdf_filename_is_rejected_with_415(client, filename, db):
    response = upload(client, make_pdf(), filename=filename)

    assert response.status_code == 415
    assert response.json()["detail"] == "Only PDF files can be uploaded."
    assert db.papers() == []


@pytest.mark.parametrize(
    "data, detail",
    [
        (b"", "This file is not a PDF."),
        (b"Hello, this is plain text pretending to be a PDF.", "This file is not a PDF."),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, "This file is not a PDF."),
        (b"%PDF-1.7\n" + b"\x00garbage" * 50, "The PDF could not be opened. It may be damaged."),
        (make_pdf(password="secret"), "The PDF is password-protected."),
        (make_scanned_pdf(), "probably a scanned document"),
    ],
    ids=["empty", "text-file", "png-file", "damaged", "password-protected", "scanned"],
)
def test_unusable_pdf_upload_is_rejected_with_422(client, data, detail, db, stored_pdfs):
    response = upload(client, data)

    assert response.status_code == 422
    assert detail in response.json()["detail"]
    assert db.papers() == []
    assert stored_pdfs() == []


def test_upload_over_the_size_limit_is_rejected_with_413(client, monkeypatch):
    monkeypatch.setattr(papers_router, "MAX_PDF_BYTES", 1024 * 1024)

    response = upload(client, b"%PDF-1.7\n" + b"0" * (1024 * 1024))

    assert response.status_code == 413
    assert response.json()["detail"] == "The PDF is larger than 1 MB."


def test_upload_exactly_at_the_size_limit_is_not_rejected_for_size(client, monkeypatch):
    pdf = make_pdf()
    monkeypatch.setattr(papers_router, "MAX_PDF_BYTES", len(pdf))

    assert upload(client, pdf).status_code == 201


def test_upload_without_a_file_is_rejected_with_422(client):
    assert client.post("/api/papers/upload").status_code == 422


# --- GET /api/papers, GET /api/papers/{id} -------------------------------------------------------


def test_library_starts_empty(client):
    response = client.get("/api/papers")

    assert response.status_code == 200
    assert response.json() == []


def test_library_lists_newest_first(client, db):
    for title in ("First", "Second", "Third"):
        db.add_paper(title=title)

    papers = client.get("/api/papers").json()

    assert [paper["title"] for paper in papers] == ["Third", "Second", "First"]
    assert all(set(paper) == PUBLIC_FIELDS for paper in papers)


def test_get_one_paper(client, db):
    paper = db.add_paper(title="One Paper", authors=["Ada Lovelace"], year=1843, source="upload", external_id="abc")

    response = client.get(f"/api/papers/{paper.id}")

    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["title"], body["authors"], body["year"]) == (paper.id, "One Paper", ["Ada Lovelace"], 1843)
    assert body["has_pdf"] is False


def test_get_missing_paper_is_404(client):
    response = client.get("/api/papers/12345")

    assert response.status_code == 404
    assert response.json()["detail"] == "Paper not found. It may have been deleted."


@pytest.mark.parametrize("paper_id", ["abc", "1.5"])
def test_non_numeric_paper_id_is_rejected_with_422(client, paper_id):
    assert client.get(f"/api/papers/{paper_id}").status_code == 422


# --- GET /api/papers/{id}/pdf ---------------------------------------------------------------------


def test_stored_pdf_is_served_inline(client):
    pdf = make_pdf()
    paper = upload(client, pdf).json()

    response = client.get(f"/api/papers/{paper['id']}/pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" not in response.headers.get("content-disposition", "")
    assert response.content == pdf


def test_pdf_of_a_paper_without_one_is_404(client, db):
    paper = db.add_paper()

    response = client.get(f"/api/papers/{paper.id}/pdf")

    assert response.status_code == 404
    assert response.json()["detail"] == "No PDF is stored for this paper."


def test_pdf_whose_file_was_removed_from_disk_is_404(client, stored_pdfs):
    paper = upload(client, make_pdf()).json()
    stored_pdfs()[0].unlink()

    assert client.get(f"/api/papers/{paper['id']}/pdf").status_code == 404


def test_pdf_of_a_missing_paper_is_404(client):
    assert client.get("/api/papers/999/pdf").status_code == 404


# --- DELETE /api/papers/{id} ----------------------------------------------------------------------


def test_delete_removes_the_paper_its_pdf_and_its_chat(client, db, stored_pdfs):
    paper = upload(client, make_pdf()).json()
    client.post(f"/api/papers/{paper['id']}/ask", json={"question": "What is it about?"})
    assert len(db.messages(paper["id"])) == 2

    response = client.delete(f"/api/papers/{paper['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"/api/papers/{paper['id']}").status_code == 404
    assert stored_pdfs() == []
    assert db.messages(paper["id"]) == []


def test_delete_leaves_other_papers_alone(client, db):
    keep, remove = db.add_paper(title="Keep"), db.add_paper(title="Remove")
    db.add_messages(keep.id, "Q?", "A.")

    client.delete(f"/api/papers/{remove.id}")

    assert [paper["title"] for paper in client.get("/api/papers").json()] == ["Keep"]
    assert len(db.messages(keep.id)) == 2


def test_delete_works_when_the_pdf_file_is_already_gone(client, stored_pdfs):
    paper = upload(client, make_pdf()).json()
    stored_pdfs()[0].unlink()

    assert client.delete(f"/api/papers/{paper['id']}").status_code == 204


def test_deleting_twice_gives_404_the_second_time(client, db):
    paper = db.add_paper()
    client.delete(f"/api/papers/{paper.id}")

    response = client.delete(f"/api/papers/{paper.id}")

    assert response.status_code == 404


def test_deleted_paper_can_be_saved_again(client):
    saved = client.post("/api/papers", json=search_result(pdf_url=None)).json()
    client.delete(f"/api/papers/{saved['id']}")

    assert client.post("/api/papers", json=search_result(pdf_url=None)).status_code == 201
