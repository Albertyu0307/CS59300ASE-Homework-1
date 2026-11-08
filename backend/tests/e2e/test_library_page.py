"""The library page (library.html): listing saved papers, uploading PDFs, deleting."""

import re

import pytest

from tests.factories import arxiv_abs_page, make_pdf, make_scanned_pdf

expect = pytest.importorskip("playwright.sync_api").expect
pytestmark = pytest.mark.e2e

EMPTY = 'Your library is empty. Search for papers and click "Save to library" to add them here.'


def choose_file(page, name: str, data: bytes, mime_type: str = "application/pdf") -> None:
    page.set_input_files("#upload-input", files=[{"name": name, "mimeType": mime_type, "buffer": data}])


def test_empty_library_explains_how_to_add_papers(page):
    page.goto("/library.html")

    expect(page.locator("#status")).to_have_text(EMPTY)
    expect(page.locator(".paper-card")).to_have_count(0)


def test_saved_papers_are_listed_newest_first(page, db):
    older = db.add_paper(title="Older Paper")
    newer = db.add_paper(title="Newer Paper", source="upload", external_id="abc", text_status="failed", full_text=None)

    page.goto("/library.html")

    titles = page.locator(".paper-card .paper-title a")
    expect(titles).to_have_text(["Newer Paper", "Older Paper"])
    expect(titles.nth(0)).to_have_attribute("href", f"paper.html?id={newer.id}")
    expect(titles.nth(1)).to_have_attribute("href", f"paper.html?id={older.id}")
    newest = page.locator(".paper-card").nth(0)
    expect(newest.locator(".badge")).to_have_text(["Uploaded PDF", "Abstract only: the PDF couldn't be downloaded or read"])
    expect(page.locator("#status")).to_be_hidden()


def test_uploading_a_pdf_adds_it_to_the_top(page, db):
    db.add_paper(title="Already Here")
    page.goto("/library.html")

    choose_file(page, "sparse.pdf", make_pdf())

    expect(page.locator("#upload-status")).to_have_text(
        'Added "Sparse Attention for Long Document Understanding at Scale". '
        "Its details were read from the PDF automatically, so please check them below."
    )
    cards = page.locator(".paper-card")
    expect(cards).to_have_count(2)
    expect(cards.nth(0).locator(".paper-meta")).to_have_text("Alice Smith, Bob Jones · Year unknown")
    [uploaded] = [paper for paper in db.papers() if paper.source == "upload"]
    expect(cards.nth(0).get_by_role("link", name="PDF")).to_have_attribute("href", f"/api/papers/{uploaded.id}/pdf")
    expect(page.locator("#upload-input")).to_be_enabled()


def test_first_upload_replaces_the_empty_library_message(page):
    page.goto("/library.html")
    expect(page.locator("#status")).to_have_text(EMPTY)

    choose_file(page, "paper.pdf", make_pdf())

    expect(page.locator(".paper-card")).to_have_count(1)
    expect(page.locator("#status")).to_be_hidden()


def test_uploading_an_arxiv_pdf_says_it_was_recognized(page, http_mock):
    http_mock.get("https://arxiv.org/abs/2103.01234").respond(200, text=arxiv_abs_page())
    page.goto("/library.html")

    choose_file(page, "download.pdf", make_pdf(arxiv_stamp="arXiv:2103.01234v1 [cs.CL] 2 Mar 2021"))

    expect(page.locator("#upload-status")).to_have_text(
        'Added "Sparse Attention for Long Documents" (recognized as an arXiv paper; details from arXiv).'
    )
    expect(page.get_by_role("link", name="View on arXiv")).to_have_attribute("href", "https://arxiv.org/abs/2103.01234")


@pytest.mark.parametrize(
    "name, data, message",
    [
        ("notes.txt", b"plain text", "Upload failed: Only PDF files can be uploaded."),
        ("fake.pdf", b"not really a pdf", "Upload failed: This file is not a PDF."),
        ("locked.pdf", make_pdf(password="secret"), "Upload failed: The PDF is password-protected."),
        ("scan.pdf", make_scanned_pdf(), "Upload failed: Almost no text could be extracted from this PDF."),
    ],
    ids=["wrong-type", "not-a-pdf", "password", "scanned"],
)
def test_rejected_uploads_show_why(page, db, name, data, message):
    page.goto("/library.html")

    choose_file(page, name, data)

    status = page.locator("#upload-status")
    expect(status).to_have_class(re.compile(r"\berror\b"))
    expect(status).to_contain_text(message)
    expect(page.locator("#upload-input")).to_be_enabled()
    assert db.papers() == []


def test_uploading_the_same_file_twice_is_refused(page):
    pdf = make_pdf()
    page.goto("/library.html")
    choose_file(page, "paper.pdf", pdf)
    expect(page.locator(".paper-card")).to_have_count(1)

    choose_file(page, "paper.pdf", pdf)  # the form was reset, so the same file uploads again

    expect(page.locator("#upload-status")).to_have_text("Upload failed: This paper is already in your library.")
    expect(page.locator(".paper-card")).to_have_count(1)


def test_deleting_a_paper_after_confirming(page, db):
    paper = db.add_paper(title="Doomed Paper")
    page.goto("/library.html")
    messages = []

    def accept(dialog):
        messages.append(dialog.message)
        dialog.accept()

    page.once("dialog", accept)
    page.get_by_role("button", name="Delete").click()

    expect(page.locator(".paper-card")).to_have_count(0)
    expect(page.locator("#status")).to_have_text(EMPTY)
    assert messages == ['Delete "Doomed Paper" from your library?']
    assert db.paper(paper.id) is None


def test_cancelling_the_confirmation_keeps_the_paper(page, db):
    paper = db.add_paper()
    page.goto("/library.html")

    page.once("dialog", lambda dialog: dialog.dismiss())
    page.get_by_role("button", name="Delete").click()

    expect(page.locator(".paper-card")).to_have_count(1)
    expect(page.get_by_role("button", name="Delete")).to_be_enabled()
    assert db.paper(paper.id) is not None


def test_deleting_a_paper_already_deleted_elsewhere_removes_its_card(page, db, client):
    paper = db.add_paper()
    page.goto("/library.html")
    expect(page.locator(".paper-card")).to_have_count(1)
    client.delete(f"/api/papers/{paper.id}")  # e.g. from another tab

    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Delete").click()

    expect(page.locator(".paper-card")).to_have_count(0)
    expect(page.locator("#status")).to_have_text(EMPTY)


def test_failed_delete_keeps_the_card_and_shows_the_error(page, db):
    db.add_paper(title="Sticky Paper")
    page.route(re.compile(r".*/api/papers/\d+$"), lambda route: route.fulfill(status=500, json={"detail": "Disk is full"})
               if route.request.method == "DELETE" else route.continue_())
    page.goto("/library.html")

    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Delete").click()

    expect(page.locator("#status")).to_have_text('Could not delete "Sticky Paper": Disk is full')
    expect(page.locator(".paper-card")).to_have_count(1)
    expect(page.get_by_role("button", name="Delete")).to_be_enabled()


def test_library_that_cannot_be_loaded_shows_an_error(page):
    page.route("**/api/papers", lambda route: route.fulfill(status=503, body="Service Unavailable", content_type="text/plain"))

    page.goto("/library.html")

    expect(page.locator("#status")).to_have_text("Could not load your library: 503 Service Unavailable")


def test_error_detail_that_is_not_a_message_is_shown_as_json(page):
    """FastAPI's own 422 errors carry a list of problems as their detail, not a sentence: the page
    shows it as JSON rather than "[object Object]"."""
    detail = [{"type": "missing", "loc": ["body", "file"], "msg": "Field required"}]
    page.route("**/api/papers/upload", lambda route: route.fulfill(status=422, json={"detail": detail}))
    page.goto("/library.html")

    choose_file(page, "paper.pdf", make_pdf())

    expect(page.locator("#upload-status")).to_have_text(
        'Upload failed: [{"type":"missing","loc":["body","file"],"msg":"Field required"}]'
    )
