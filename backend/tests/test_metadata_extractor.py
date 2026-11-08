"""services/metadata_extractor.py: title / authors / year / abstract / arXiv ID heuristics for
uploaded PDFs, and the optional LLM pass that improves them.

Most tests build the extracted page (PdfContent) by hand, so each heuristic is tested on exactly the
layout it has to handle; a few run on real PDFs made with PyMuPDF.
"""

from datetime import date

import pytest

from app.config import settings
from app.services import llm_client, metadata_extractor
from app.services.llm_client import LLMRateLimitError
from app.services.metadata_extractor import PaperMetadata, extract_metadata, improve_with_llm
from app.services.pdf_processor import PdfContent, TextLine, read_pdf
from tests.conftest import LLM_URL
from tests.factories import completion, make_pdf

ABSTRACT = (
    "We propose a new method for learning sparse attention patterns. It reduces memory use by half "
    "and improves accuracy on three long-document benchmarks."
)


def line(text: str, y: float, size: float = 10.0, horizontal: bool = True) -> TextLine:
    return TextLine(text=text, size=size, x=72.0, y=y, horizontal=horizontal)


def page(*lines: TextLine, text: str = "", info: dict | None = None) -> PdfContent:
    return PdfContent(text=text, page_count=1, info=info or {}, first_page_lines=list(lines), first_page_height=842.0)


def extract(*lines: TextLine, text: str = "", info: dict | None = None, filename: str = "paper.pdf") -> PaperMetadata:
    return extract_metadata(page(*lines, text=text, info=info), filename)


TITLE = line("Learning Sparse Attention Patterns", 80, size=18)


# --- Title ---------------------------------------------------------------------------------------


def test_title_is_the_largest_text_in_the_top_half():
    meta = extract(line("Workshop on Testing, 2020", 40, size=9), TITLE, line("Alice Smith", 110, size=11))

    assert meta.title == "Learning Sparse Attention Patterns"


def test_title_spanning_several_lines_is_joined():
    meta = extract(line("Learning Sparse Attention", 80, size=18), line("Patterns at Scale", 102, size=18))

    assert meta.title == "Learning Sparse Attention Patterns at Scale"


def test_title_stops_at_a_large_vertical_gap():
    meta = extract(TITLE, line("Section Heading Far Below", 300, size=18))

    assert meta.title == "Learning Sparse Attention Patterns"


@pytest.mark.parametrize(
    "distractor",
    [
        line("Huge Text Near The Bottom", 700, size=30),  # bottom half of the page
        line("ACM", 20, size=30),  # under 4 letters, e.g. a logo or page number
        line("arXiv:2103.01234v1 [cs.CL] 2 Mar 2021", 400, size=30, horizontal=False),  # rotated
    ],
    ids=["bottom-half", "too-few-letters", "rotated"],
)
def test_title_ignores_text_that_cannot_be_a_title(distractor):
    assert extract(distractor, TITLE).title == "Learning Sparse Attention Patterns"


def test_implausible_title_falls_back_to_the_pdf_title_field():
    meta = extract(line("Draft", 80, size=18), info={"title": "  A Proper   Title From Info  "})

    assert meta.title == "A Proper Title From Info"


# Each value is long enough (10+ characters, 2+ words) to pass as a title, so only the junk filter can
# reject it: an authoring tool's name, a placeholder, or a file name. Too-short titles are #5's case.
@pytest.mark.parametrize(
    "info_title",
    ["Microsoft Word - paper.docx", "Untitled Document 3", "Title of the Submission", "camera ready main.tex",
     "final paper v3.pdf", "Thesis Draft.docx"],
)
def test_junk_title_fields_fall_back_to_the_filename(info_title):
    meta = extract(info={"title": info_title}, filename="sparse_attention_2021.pdf")

    assert meta.title == "sparse_attention_2021"


def test_no_usable_title_and_no_filename_gives_a_placeholder():
    assert extract(filename="").title == "Untitled paper"


@pytest.mark.parametrize(
    "title, plausible",
    [
        ("Graph Nets", True),  # 10 characters: the shortest title accepted
        ("Graph Net", False),  # 9
        ("Attention " + "x" * 290, True),  # 300 characters: the longest
        ("Attention " + "x" * 291, False),  # 301
        ("Transformers", False),  # a single word, however long
    ],
    ids=["10-chars", "9-chars", "300-chars", "301-chars", "one-word"],
)
def test_title_length_limits(title, plausible):
    meta = extract(line(title, 80, size=18), filename="fallback name.pdf")

    assert meta.title == (title if plausible else "fallback name")


@pytest.mark.parametrize("gap, joined", [(36, True), (37, False)], ids=["2x-font-size", "just-over"])
def test_next_title_line_may_be_up_to_twice_the_font_size_below(gap, joined):
    meta = extract(line("Learning Sparse Attention", 80, size=18), line("Patterns at Scale", 80 + gap, size=18))

    assert meta.title == ("Learning Sparse Attention Patterns at Scale" if joined else "Learning Sparse Attention")


# --- Authors -------------------------------------------------------------------------------------


def test_authors_from_the_pdf_author_field():
    meta = extract(TITLE, info={"author": "Alice Smith; Bob Jones and Carol King & Dan Brown"})

    assert meta.authors == ["Alice Smith", "Bob Jones", "Carol King", "Dan Brown"]


@pytest.mark.parametrize("author_field", ["LaTeX with hyperref", "admin", "jsmith2", "Alice Smith, J. Doe et al. (eds.)"])
def test_author_field_that_is_not_all_names_is_ignored(author_field):
    meta = extract(TITLE, line("Eve Adams", 110), info={"author": author_field})

    assert meta.authors == ["Eve Adams"]


@pytest.mark.xfail(strict=True, reason="Known limitation: Word's default author field looks like a person's name")
def test_word_default_author_field_is_not_taken_as_a_name():
    meta = extract(TITLE, line("Eve Adams", 110), info={"author": "Microsoft Office User"})

    assert meta.authors == ["Eve Adams"]


def test_authors_from_the_lines_below_the_title():
    meta = extract(
        TITLE,
        line("Alice Smith1,2, Bob Jones*", 110),
        line("Carol King† and Dan Brown", 125),
        line("Department of Computer Science, University of Testing", 140),
        line("{alice,bob}@test.edu", 155),
        line("carol@example.com", 170),
        line("Abstract", 200),
        line("Eve Adams", 230),  # below "Abstract": not an author
    )

    assert meta.authors == ["Alice Smith", "Bob Jones", "Carol King", "Dan Brown"]


def test_author_lines_keep_name_particles_and_drop_duplicates():
    meta = extract(TITLE, line("Ludwig van Beethoven, Maria de la Cruz", 110), line("Ludwig van Beethoven", 125))

    assert meta.authors == ["Ludwig van Beethoven", "Maria de la Cruz"]


@pytest.mark.parametrize(
    "text",
    ["Google Research", "Meta AI", "Stanford University", "Max Planck Institute", "Bell Labs", "single"],
)
def test_affiliations_and_other_text_are_not_authors(text):
    assert extract(TITLE, line(text, 110)).authors == []


def test_no_authors_without_a_title():
    assert extract(line("Alice Smith", 110)).authors == []


def test_author_list_is_capped():
    names = "; ".join(f"Author {chr(65 + i // 26)}{chr(97 + i % 26)}" for i in range(60))

    assert len(extract(TITLE, info={"author": names}).authors) == metadata_extractor.MAX_AUTHORS


@pytest.mark.parametrize(
    "name, is_name",
    [
        ("Alice Smith", True),  # 2 words: the fewest
        ("Maria Luisa de la Cruz", True),  # 5 words: the most
        ("Maria Luisa de la Cruz Garcia", False),  # 6
        ("Alice", False),  # 1
        ("A" + "a" * 28 + " B" + "b" * 29, True),  # 60 characters: the longest
        ("A" + "a" * 28 + " B" + "b" * 30, False),  # 61
    ],
    ids=["2-words", "5-words", "6-words", "1-word", "60-chars", "61-chars"],
)
def test_author_name_limits(name, is_name):
    assert extract(TITLE, info={"author": name}).authors == ([name] if is_name else [])


# --- Year ----------------------------------------------------------------------------------------


def test_year_comes_from_the_arxiv_id():
    stamp = line("arXiv:1706.03762v7 [cs.CL] 2 Aug 2023", 400, horizontal=False)

    assert extract(TITLE, stamp, line("Published at NeurIPS 2019", 40)).year == 2017


@pytest.mark.parametrize(
    "header, year",
    [
        ("Proceedings of the 36th Conference on Neural Information Processing Systems (NeurIPS 2022)", 2022),
        ("Published as a conference paper at ICLR 2021", 2021),
        ("© 2019 IEEE. Personal use of this material is permitted.", 2019),
        ("Workshop on Efficient Systems for Machine Learning, Vancouver, Canada, 2023", 2023),
    ],
)
def test_year_from_a_venue_header(header, year):
    assert extract(line(header, 40, size=8), TITLE).year == year


@pytest.mark.xfail(strict=True, reason="Known limitation: any digit between the venue name and the year (a volume "
                   "number, a month) stops the header-year pattern, so JMLR and TMLR headers give no year")
@pytest.mark.parametrize(
    "header, year",
    [
        ("Journal of Machine Learning Research 15 (2014) 1929-1958", 2014),
        ("Published in Transactions on Machine Learning Research (01/2024)", 2024),
    ],
    ids=["jmlr-volume", "tmlr-month"],
)
def test_year_from_a_header_with_numbers_before_the_year(header, year):
    assert extract(line(header, 40, size=8), TITLE).year == year


def test_year_in_the_future_is_not_trusted():
    future = date.today().year + 5

    assert extract(line(f"Proceedings of Something {future}", 40), TITLE).year is None


def test_year_from_the_subject_field():
    meta = extract(TITLE, info={"subject": "2016 IEEE Conference on Computer Vision and Pattern Recognition (CVPR)"})

    assert meta.year == 2016


def test_year_from_the_creation_date():
    assert extract(TITLE, info={"creationDate": "D:20190429173603Z"}).year == 2019


def test_header_year_wins_over_the_creation_date():
    meta = extract(line("Published at ICLR 2021", 40), TITLE, info={"creationDate": "D:20230101000000Z"})

    assert meta.year == 2021


def test_no_year_found():
    assert extract(TITLE, line("Alice Smith", 110), info={"creationDate": "yesterday"}).year is None


def test_future_year_in_the_subject_field_falls_back_to_the_creation_date():
    future = date.today().year + 5
    meta = extract(TITLE, info={"subject": f"{future} Conference on Things", "creationDate": "D:20190429173603Z"})

    assert meta.year == 2019


def test_creation_date_in_the_future_is_not_trusted():
    """E.g. a PDF written on a computer whose clock was wrong."""
    future = date.today().year + 5

    assert extract(TITLE, info={"creationDate": f"D:{future}0101000000Z"}).year is None


# --- Abstract ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        f"Title\nAlice Smith\nAbstract\n{ABSTRACT}\n1 Introduction\nDeep learning...",
        f"Title\nABSTRACT\n{ABSTRACT}\nI. INTRODUCTION\nDeep learning...",
        f"Title\nAbstract—{ABSTRACT}\nIndex Terms—attention, sparsity",
        f"Title\nAbstract: {ABSTRACT}\nKeywords: attention, sparsity",
        f"Title\nAbstract\n{ABSTRACT}\n∗Equal contribution.",
        f"Title\nAbstract\n{ABSTRACT}\narXiv:2103.01234v1 [cs.CL] 2 Mar 2021",
        f"Title\n  Abstract.  {ABSTRACT}\nCCS Concepts: Computing methodologies",
    ],
    ids=["numbered-heading", "roman-numeral-heading", "ieee-index-terms", "keywords", "footnote", "arxiv-stamp", "acm"],
)
def test_abstract_is_the_text_between_its_heading_and_the_next_section(text):
    assert extract(TITLE, text=text).abstract == ABSTRACT


def test_abstract_line_breaks_are_joined():
    wrapped = ABSTRACT.replace(" ", "\n", 5)

    assert extract(TITLE, text=f"Abstract\n{wrapped}\n1 Introduction").abstract == ABSTRACT


def test_no_abstract_heading_means_no_abstract():
    text = f"Title\nIn this abstract we describe things.\n{ABSTRACT}\n1 Introduction"

    assert extract(TITLE, text=text).abstract is None


def test_too_short_abstract_is_ignored():
    assert extract(TITLE, text="Abstract\nToo short to be real.\n1 Introduction").abstract is None


def test_abstract_without_an_end_marker_is_cut_at_a_sentence():
    text = "Abstract\n" + "This sentence is part of a very long abstract. " * 200

    abstract = extract(TITLE, text=text).abstract

    assert len(abstract) <= metadata_extractor.MAX_ABSTRACT_CHARS
    assert abstract.endswith("long abstract.")


# --- arXiv ID ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stamp, arxiv_id",
    [
        ("arXiv:1706.03762v7 [cs.CL] 2 Aug 2023", "1706.03762"),
        ("arXiv:2103.01234v1 [cs.LG] 2 Mar 2021", "2103.01234"),
        ("arXiv:2103.01234 [cs.LG] 2 Mar 2021", "2103.01234"),
    ],
)
def test_arxiv_id_from_the_margin_stamp(stamp, arxiv_id):
    assert extract(TITLE, line(stamp, 400, horizontal=False)).arxiv_id == arxiv_id


def test_arxiv_id_mentioned_in_the_text_is_not_a_stamp():
    """E.g. a citation on page 1: only the rotated margin stamp identifies the paper itself."""
    assert extract(TITLE, line("See arXiv:1810.04805 for details.", 600)).arxiv_id is None


def test_old_style_arxiv_ids_are_not_recognized():
    assert extract(TITLE, line("arXiv:hep-th/9901001v1 4 Jan 1999", 400, horizontal=False)).arxiv_id is None


# --- On real PDFs ---------------------------------------------------------------------------------


def test_all_fields_from_a_generated_paper():
    content = read_pdf(make_pdf(header="Published as a conference paper at ICLR 2020"))

    meta = extract_metadata(content, "upload.pdf")

    assert meta.title == "Sparse Attention for Long Document Understanding at Scale"
    assert meta.authors == ["Alice Smith", "Bob Jones"]
    assert meta.year == 2020
    assert meta.abstract.startswith("We study sparse attention for long documents.")
    assert meta.abstract.endswith("long-range benchmarks.")
    assert meta.arxiv_id is None


def test_generated_arxiv_paper():
    content = read_pdf(make_pdf(arxiv_stamp="arXiv:1912.00001v4 [cs.LG] 1 Jan 2024"))

    meta = extract_metadata(content, "upload.pdf")

    assert (meta.arxiv_id, meta.year) == ("1912.00001", 2019)


def test_paper_with_no_detectable_metadata_uses_the_filename():
    content = read_pdf(make_pdf(title=("Draft",), authors=None, affiliation=None, email=None, abstract=None))

    meta = extract_metadata(content, "My Notes.pdf")

    assert meta.title == "My Notes"
    assert meta.authors == []
    assert meta.abstract is None


# --- improve_with_llm ----------------------------------------------------------------------------

HEURISTIC = PaperMetadata(title="Heuristic Title Here", authors=["Alice Smith"], year=2018, abstract=ABSTRACT, arxiv_id=None)


@pytest.fixture
def llm_reply(monkeypatch):
    """Makes llm_client.chat_json return the given dict (or raise the given exception)."""
    calls = []

    def set_reply(reply):
        def chat_json(messages, **kwargs):
            calls.append((messages, kwargs))
            if isinstance(reply, Exception):
                raise reply
            return reply

        monkeypatch.setattr(llm_client, "chat_json", chat_json)
        return calls

    return set_reply


def test_mock_llm_leaves_the_heuristics_unchanged():
    assert settings.llm_mock is True
    assert improve_with_llm(page(text="Some text"), HEURISTIC) == HEURISTIC


def test_llm_values_replace_the_heuristic_ones(llm_reply):
    better_abstract = "An improved abstract from the model, long enough to count as a real abstract of a paper. " * 2
    llm_reply({"title": "The Real Title", "authors": ["Alice Smith", "Bob Jones"], "year": 2020, "abstract": better_abstract})

    meta = improve_with_llm(page(text="..."), HEURISTIC)

    assert meta.title == "The Real Title"
    assert meta.authors == ["Alice Smith", "Bob Jones"]
    assert meta.year == 2020
    assert meta.abstract == better_abstract.strip()


def test_llm_gets_the_start_of_the_paper_and_one_retry(llm_reply):
    calls = llm_reply({})

    improve_with_llm(page(text="A" * 20_000), HEURISTIC)

    [(messages, kwargs)] = calls
    assert messages[-1]["content"] == "A" * metadata_extractor.LLM_INPUT_CHARS
    assert kwargs == {"retries": 1}


@pytest.mark.parametrize(
    "reply",
    [
        {"title": "Paper"},  # too short to be a title
        {"title": "x" * 400},
        {"title": None, "authors": None, "year": None, "abstract": None},
        {"authors": "Alice Smith, Bob Jones"},  # a string, not a list
        {"authors": []},
        {"authors": ["", "  "]},
        {"year": 1850},
        {"year": date.today().year + 2},
        {"year": "unknown"},
        {"year": 2019.5},
        {"abstract": "Too short."},
        {"unexpected": "keys"},
        {"authors": [{"name": "Alice Smith"}, 42]},  # objects and numbers, not names
        {"title": ["Attention Is", "All You Need"]},  # a list, not a string
        {"abstract": {"text": ABSTRACT}},  # an object, not a string
    ],
)
def test_implausible_llm_values_keep_the_heuristic_ones(llm_reply, reply):
    llm_reply(reply)

    assert improve_with_llm(page(text="..."), HEURISTIC) == HEURISTIC


def test_llm_author_names_are_cleaned(llm_reply):
    llm_reply({"authors": ["  Alice   Smith ", "", "Alice Smith", "B" * 101, "Bob Jones"]})

    assert improve_with_llm(page(text="..."), HEURISTIC).authors == ["Alice Smith", "Bob Jones"]


def test_llm_year_as_a_string_is_accepted(llm_reply):
    llm_reply({"year": "2019"})

    assert improve_with_llm(page(text="..."), HEURISTIC).year == 2019


def test_llm_failure_keeps_the_heuristics(llm_reply):
    llm_reply(LLMRateLimitError("rate limited"))

    assert improve_with_llm(page(text="..."), HEURISTIC) == HEURISTIC


def test_llm_metadata_through_the_real_client(http_mock, real_llm):
    """chat_json and improve_with_llm together: a fenced JSON reply from the provider."""
    http_mock.post(LLM_URL).respond(200, json=completion('```json\n{"title": "Attention Is All You Need", "year": 2017}\n```'))

    meta = improve_with_llm(page(text="..."), HEURISTIC)

    assert (meta.title, meta.year, meta.authors) == ("Attention Is All You Need", 2017, ["Alice Smith"])


def test_llm_author_list_is_capped(llm_reply):
    names = [f"Author {chr(65 + i // 26)}{chr(97 + i % 26)}" for i in range(60)]
    llm_reply({"authors": names})

    assert improve_with_llm(page(text="..."), HEURISTIC).authors == names[: metadata_extractor.MAX_AUTHORS]
