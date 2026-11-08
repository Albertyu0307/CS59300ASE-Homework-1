"""services/pdf_processor.py: text extraction, rejecting unusable files, downloads, file storage."""

import re

import httpx
import pymupdf
import pytest

from app.services import pdf_processor
from app.services.pdf_processor import PdfError, delete_pdf, download_pdf, pdf_file, read_pdf, store_pdf
from tests.factories import make_pdf, make_pdf_without_pages, make_scanned_pdf

PAGE_TEXT = "This is page {n} of the paper. " * 12


# --- read_pdf ------------------------------------------------------------------------------------


def test_text_and_layout_of_the_first_page_are_extracted():
    content = read_pdf(make_pdf(arxiv_stamp="arXiv:2103.01234v2 [cs.CL] 5 Mar 2021"))

    assert content.page_count == 1
    assert content.first_page_height == pytest.approx(842)
    assert "Sparse Attention for Long Document" in content.text
    assert "LongBench and SCROLLS datasets" in content.text
    title = next(line for line in content.first_page_lines if line.text == "Sparse Attention for Long Document")
    assert (title.size, title.horizontal) == (18, True)
    stamp = next(line for line in content.first_page_lines if line.text.startswith("arXiv:"))
    assert stamp.horizontal is False  # rotated up the margin


def test_all_pages_are_extracted_in_order():
    content = read_pdf(make_pdf(extra_pages=(PAGE_TEXT.format(n=2), PAGE_TEXT.format(n=3))))

    assert content.page_count == 3
    assert content.text.index("page 2 of") < content.text.index("page 3 of")
    assert "\n\n" in content.text  # pages are separated by a blank line


def test_document_info_fields_are_read_and_blank_ones_dropped():
    content = read_pdf(make_pdf(metadata={"title": "Info Title", "author": "Alice Smith", "subject": "  ", "keywords": ""}))

    assert content.info["title"] == "Info Title"
    assert content.info["author"] == "Alice Smith"
    assert "subject" not in content.info and "keywords" not in content.info


def test_words_hyphenated_at_a_line_break_are_rejoined():
    body = "Transformers learn a representa-\ntion of the input. Large-scale Pre-\nTraining helps. " * 3

    text = read_pdf(make_pdf(body=body)).text

    assert "representation of the input" in text
    assert "representa-" not in text
    assert "Pre-\nTraining" in text  # capital after the break: a real hyphen, kept
    assert "Large-scale" in text  # hyphens inside a line are untouched


def test_a_little_junk_before_the_pdf_header_is_tolerated():
    assert read_pdf(b"\r\n\xef\xbb\xbfjunk" + make_pdf()).page_count == 1


def test_whitespace_only_lines_are_left_out_of_the_layout():
    """A run of spaces is still a text line in the PDF, here in a 30 pt font, bigger than the title's.
    It is dropped, so the metadata heuristics never see a blank line or its font size."""
    doc = pymupdf.open(stream=make_pdf(), filetype="pdf")
    doc[0].insert_text((72, 60), " " * 8, fontsize=30)

    lines = read_pdf(doc.tobytes()).first_page_lines

    assert all(line.text.strip() for line in lines)
    assert max(line.size for line in lines) == 18  # the title's size


def test_pdf_header_too_far_into_the_file_is_not_a_pdf():
    with pytest.raises(PdfError, match="This file is not a PDF."):
        read_pdf(b" " * 1024 + make_pdf())


@pytest.mark.parametrize(
    "data",
    [b"", b"Just some text.", b"<html><body>Login required</body></html>", b"\x89PNG\r\n\x1a\n" + bytes(200), b"PK\x03\x04docx"],
    ids=["empty", "text", "html", "png", "zip"],
)
def test_non_pdf_files_are_rejected(data):
    with pytest.raises(PdfError, match="This file is not a PDF."):
        read_pdf(data)


@pytest.mark.parametrize(
    "data",
    [b"%PDF-1.7\n" + b"\x00garbage" * 50, make_pdf()[:300]],
    ids=["garbage-after-header", "truncated-file"],
)
def test_damaged_pdfs_are_rejected(data):
    with pytest.raises(PdfError, match=r"The PDF could not be (opened|read)\. It may be damaged\."):
        read_pdf(data)


def test_password_protected_pdf_is_rejected():
    with pytest.raises(PdfError, match="The PDF is password-protected."):
        read_pdf(make_pdf(password="secret"))


@pytest.mark.parametrize("pages", [1, 3])
def test_scanned_pdf_without_a_text_layer_is_rejected(pages):
    with pytest.raises(PdfError, match="probably a scanned document"):
        read_pdf(make_scanned_pdf(pages))


def test_too_little_text_for_the_number_of_pages_is_rejected():
    """One page of text followed by many blank-ish pages averages under MIN_CHARS_PER_PAGE."""
    with pytest.raises(PdfError, match="scanned document"):
        read_pdf(make_pdf(extra_pages=("Figure.",) * 8))


def test_pdf_without_pages_is_rejected():
    with pytest.raises(PdfError, match=r"^The PDF has no pages\.$"):
        read_pdf(make_pdf_without_pages())


def test_pdf_whose_pages_cannot_be_read_is_rejected(monkeypatch):
    """Some damaged files open, then fail once a page's content is parsed."""
    pdf = make_pdf()

    def broken_page(*args, **kwargs):
        raise RuntimeError("syntax error in content stream")

    monkeypatch.setattr(pymupdf.Page, "get_text", broken_page)

    with pytest.raises(PdfError, match=r"^The PDF could not be read\. It may be damaged\.$"):
        read_pdf(pdf)


# --- download_pdf --------------------------------------------------------------------------------

URL = "https://example.org/paper.pdf"


def test_download_returns_the_bytes_and_identifies_the_app(http_mock):
    pdf = make_pdf()
    route = http_mock.get(URL).respond(200, content=pdf)

    assert download_pdf(URL) == pdf
    assert route.calls.last.request.headers["user-agent"] == pdf_processor.USER_AGENT


def test_download_follows_redirects(http_mock):
    http_mock.get("https://arxiv.org/pdf/2101.00001").respond(301, headers={"Location": URL})
    http_mock.get(URL).respond(200, content=b"%PDF-1.7 data")

    assert download_pdf("https://arxiv.org/pdf/2101.00001") == b"%PDF-1.7 data"


@pytest.mark.parametrize(
    "failure, message",
    [
        (httpx.Response(404), "The PDF download failed (HTTP 404)."),
        (httpx.Response(403), "The PDF download failed (HTTP 403)."),
        (httpx.Response(500), "The PDF download failed (HTTP 500)."),
        (httpx.ReadTimeout("slow"), "The PDF download timed out."),
        (httpx.ConnectError("offline"), "The PDF could not be downloaded."),
    ],
)
def test_download_failures_have_user_facing_messages(http_mock, failure, message):
    http_mock.get(URL).mock(side_effect=[failure])

    with pytest.raises(PdfError) as error:
        download_pdf(URL)

    assert str(error.value) == message


def test_download_larger_than_the_limit_is_stopped(http_mock, monkeypatch):
    monkeypatch.setattr(pdf_processor, "MAX_PDF_BYTES", 1024 * 1024)
    http_mock.get(URL).respond(200, content=b"0" * (1024 * 1024 + 1))

    with pytest.raises(PdfError, match="The PDF is larger than 1 MB."):
        download_pdf(URL)


def test_download_exactly_at_the_limit_is_accepted(http_mock, monkeypatch):
    monkeypatch.setattr(pdf_processor, "MAX_PDF_BYTES", 1000)
    http_mock.get(URL).respond(200, content=b"0" * 1000)

    assert len(download_pdf(URL)) == 1000


# --- Stored files --------------------------------------------------------------------------------


def test_store_pdf_writes_under_the_data_folder(data_dir):
    relative_path = store_pdf(b"%PDF-1.7 content")

    assert re.fullmatch(r"pdfs/[0-9a-f]{32}\.pdf", relative_path)
    assert pdf_file(relative_path) == data_dir / relative_path
    assert pdf_file(relative_path).read_bytes() == b"%PDF-1.7 content"


def test_every_stored_pdf_gets_its_own_name():
    first, second = store_pdf(b"%PDF same"), store_pdf(b"%PDF same")

    assert first != second
    assert pdf_file(first).is_file() and pdf_file(second).is_file()


def test_delete_pdf_removes_the_file():
    relative_path = store_pdf(b"%PDF-1.7")

    delete_pdf(relative_path)

    assert not pdf_file(relative_path).exists()


def test_deleting_a_missing_pdf_is_not_an_error():
    delete_pdf("pdfs/does-not-exist.pdf")
