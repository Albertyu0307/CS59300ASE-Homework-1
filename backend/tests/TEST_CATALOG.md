# Test catalog

Every automated test, grouped by feature: 412 test cases in 39 groups and 9 areas.
For how to run them and how they are set up, see "Running the tests" in the [README](../../README.md).

- The group and test numbers are only for this catalog (e.g. "group 17, #4"); they aren't in the code.
- "(×N: …)" means one test function that runs N cases (`@pytest.mark.parametrize`).
- **xfail** marks a test for a known bug: it is expected to fail until the bug is fixed.
- Last checked on 2026-11-04: 408 passed, 4 xfailed. `pytest --collect-only -q` lists every case.
  When tests are added or removed, update this file and the counts.

| Area | Groups | Test cases |
|---|---|---|
| A. App and infrastructure | 1–3 | 24 |
| B. Search | 4–10 | 60 |
| C. Library API | 11–15 | 57 |
| D. LLM features through the API | 16–18 | 36 |
| E. LLM client | 19–22 | 51 |
| F. PDF processing | 23–25 | 30 |
| G. Metadata extraction for uploads | 26–31 | 80 |
| H. Prompts and paper text | 32–33 | 20 |
| I. Frontend in a real browser | 34–39 | 54 |
| **Total** | **39** | **412** |

---

## A. App and infrastructure (`test_app.py`): 24 cases

### 1. Health check and static frontend (15 cases)
**What they test:** whether one server serves both the JSON API and the frontend files.
1. `test_health_check`: `GET /api/health` returns 200 with `{"status": "ok"}`.
2. `test_frontend_pages_are_served` (×4: `/`, `index.html`, `library.html`, `paper.html`): each returns 200 as HTML with its page's content.
3. `test_frontend_scripts_are_served_as_javascript` (×5: the 5 `.js` files): each returns 200 with a JavaScript content type, which ES modules require.
4. `test_stylesheet_is_served`: `style.css` returns 200 as `text/css`.
5. `test_unknown_paths_return_404` (×3: missing page, missing script, missing API route): each returns 404.
6. `test_api_routes_take_precedence_over_the_static_mount`: all 9 API routes are registered, and `/api/papers` answers JSON, so the frontend mount at `/` didn't swallow them.

### 2. Path traversal (4 cases)
**What they test:** whether URLs containing `..` can read files outside `frontend/`.
1. `test_files_outside_the_frontend_folder_are_not_served` (×3 encoded `../` paths to `config.py`, `.env.example`, `README.md`): each returns 404, and none of the file's text leaks.
2. `test_dot_dot_inside_the_frontend_folder_still_resolves`: `/js/%2e%2e/index.html` returns 200. This is the control: it shows the server does decode `..`, so the 404s above are real refusals.

### 3. Startup, persistence, settings (5 cases)
**What they test:** that the database is created at startup, survives a restart, and that settings resolve correctly.
1. `test_startup_creates_the_data_folder_and_tables`: starting with a missing data folder creates `library.db` with the `paper` and `chatmessage` tables.
2. `test_saved_papers_survive_a_restart`: a saved paper is still listed after reconnecting to the same database file.
3. `test_relative_data_dir_is_resolved_against_the_backend_folder`: `DATA_DIR=./data` resolves to `backend/data`.
4. `test_absolute_data_dir_is_used_as_is`: an absolute path is left unchanged.
5. `test_settings_default_to_mock_mode_and_gemini`: with no `.env`, mock mode is on, the key is empty, the URL is Gemini's, and the limit is 200,000 characters.

---

## B. Search (`test_api_search.py`, `test_search_client.py`): 60 cases

### 4. Search API: normal use (`test_api_search.py`, 8 cases)
**What they test:** that `GET /api/search` returns correctly shaped results and calls arXiv the right way.
1. `test_search_returns_arxiv_results_in_the_common_format`: returns 200 with exactly the normalized fields: ID without version, cleaned title and abstract, `https` links.
2. `test_query_words_are_combined_with_and`: `graph  neural-networks!` becomes `all:graph AND all:neural AND all:networks`, with the requested limit.
3. `test_default_limit_is_20`: with no `limit`, arXiv is asked for 20 results.
4. `test_openalex_source_never_contacts_arxiv`: with `source=openalex`, only OpenAlex is called.
5. `test_no_matches_returns_an_empty_list`: returns 200 with `[]`.
6. `test_query_without_any_words_returns_nothing_without_calling_an_api` (×2: auto, openalex): `?! ...` returns `[]`, and neither API is contacted.
7. `test_repeated_search_is_answered_from_the_cache`: the same words in different case give the same results, and arXiv is called once.

### 5. Search API: fallback and errors (7 cases)
**What they test:** what users get when arXiv or OpenAlex fails.
1. `test_auto_falls_back_to_openalex_when_arxiv_fails` (×5: 429, 503, rejected query, timeout, unreachable): returns 200 with OpenAlex results.
2. `test_both_sources_failing_returns_502_with_both_reasons`: returns 502, with a detail naming both failures.
3. `test_openalex_only_failure_returns_502`: returns 502 with "OpenAlex did not respond in time."

### 6. Search API: parameter validation (10 cases)
**What they test:** that bad query parameters are rejected and boundary values are accepted.
1. `test_invalid_parameters_are_rejected_with_422` (×7: missing or empty `q`, `q` of 301 characters, `limit` 0, 51 or "ten", unknown source): each returns 422, and arXiv is never called.
2. `test_boundary_parameters_are_accepted` (×3: `q` of 300 characters, `limit` 1, `limit` 50): each returns 200.

### 7. arXiv result parsing (`test_search_client.py`, 8 cases)
**What they test:** converting arXiv's Atom XML into a `PaperResult`.
1. `test_arxiv_entry_is_normalized`: the version is dropped, line breaks are collapsed, the year comes from the published date, and `http` becomes `https`.
2. `test_old_style_arxiv_ids_keep_their_archive_prefix`: `hep-th/9901001v1` becomes `hep-th/9901001`.
3. `test_arxiv_entry_with_missing_fields`: a blank title becomes "(untitled)"; a missing abstract, year or PDF becomes `None`, and missing authors become `[]`.
4. `test_arxiv_query_error_is_reported`: arXiv's error entry raises "arXiv rejected the query: …".
5. `test_arxiv_failures_have_user_facing_messages` (×4: 429, 500, timeout, unreachable): each gives its own readable message.

### 8. arXiv cache, cooldown and throttle (9 cases, using a fake clock)
**What they test:** the measures that keep arXiv from blocking the app.
1. `test_same_search_is_cached`: the same words with different case or punctuation make one request.
2. `test_a_different_limit_is_a_different_search`: two different limits make two requests.
3. `test_cached_results_expire`: after 1 hour, a new request is made.
4. `test_cache_is_emptied_when_full`: when the cache is full it is cleared, so an older query is fetched again.
5. `test_arxiv_is_skipped_for_a_while_after_a_failure`: after a 429, searches fail immediately without contacting arXiv; after 10 minutes, arXiv is tried again.
6. `test_cached_results_are_still_served_during_the_cooldown`: a cached query still answers during the cooldown.
7. `test_failed_searches_are_not_cached`: a failure isn't stored as a result.
8. `test_requests_to_arxiv_are_spaced_out`: a search 1 s after the previous one waits 2 s; one 5 s later doesn't wait.
9. `test_auto_search_during_the_cooldown_goes_straight_to_openalex`: during the cooldown, auto mode skips arXiv.

### 9. OpenAlex parsing (13 cases)
**What they test:** converting OpenAlex's JSON into a `PaperResult`.
1. `test_openalex_work_is_normalized`: the ID is the `W…` part of the URL, the abstract is rebuilt in word order, and the DOI becomes the link.
2. `test_openalex_requests_only_the_needed_fields`: the request passes `per-page` and a `select` list of fields.
3. `test_openalex_landing_page_preference` (×3): the DOI is preferred, then the landing page, then the OpenAlex page.
4. `test_openalex_pdf_link_preference` (×3): the open-access copy is preferred, then the primary location, else `None`.
5. `test_openalex_work_with_missing_fields`: produces "(untitled)" and `None` values, and skips authors without names.
6. `test_unexpected_openalex_response_is_an_error` (×2: not JSON, no `results` key): raises "OpenAlex returned an unexpected response."
7. `test_openalex_rate_limit_is_reported`: a 429 gives the rate-limit message.
8. `test_auto_search_reports_both_failures`: produces the exact combined arXiv and OpenAlex message.

### 10. arXiv abs-page lookup (5 cases)
**What they test:** reading one paper's metadata from its arXiv page, which is how uploaded arXiv PDFs are recognized.
1. `test_fetch_arxiv_paper_reads_the_citation_tags`: the title is HTML-unescaped, "Smith, Alice" becomes "Alice Smith", and the year comes from the date.
2. `test_fetch_arxiv_paper_with_missing_optional_tags`: a missing date, PDF or abstract becomes `None`.
3. `test_fetch_arxiv_paper_returns_none_when_unavailable` (×3: 404, no title tag, unreachable): returns `None`, so the caller falls back to heuristics.

---

## C. Library API (`test_api_papers.py`): 57 cases

### 11. Saving search results (20 cases)
**What they test:** `POST /api/papers`: downloading the PDF, handling download failures, and preventing duplicates.
1. `test_saving_a_result_downloads_its_pdf_and_stores_the_full_text`: returns 201 with `text_status: full`; the PDF's body text is in the database and the file is on disk.
2. `test_response_never_contains_full_text_or_local_paths`: the response contains exactly the 12 public fields.
3. `test_result_without_a_pdf_is_saved_with_its_abstract_only`: returns 201 with `abstract_only` and `has_pdf: false`.
4. `test_result_is_still_saved_when_its_pdf_cannot_be_used` (×6: 404, an HTML page, scanned, password-protected, timeout, unreachable): returns 201 with `failed`, and no file is stored.
5. `test_saving_the_same_result_twice_is_refused`: returns 409 "This paper is already in your library." without a second download.
6. `test_same_external_id_from_another_source_is_a_different_paper`: returns 201, giving two papers.
7. `test_simultaneous_saves_of_one_paper_store_it_once`: in a simulated double-click race, the second save gets 409, leaving one row and one file.
8. `test_client_cannot_set_server_side_fields`: `full_text`, `summary`, `text_status` and `id` in the request body are ignored.
9. `test_invalid_search_result_is_rejected_with_422` (×7: missing or wrong fields): returns 422, and nothing is saved.

### 12. Uploading PDFs: success paths (9 cases)
**What they test:** that `POST /api/papers/upload` extracts text and metadata, and recognizes arXiv papers.
1. `test_upload_extracts_text_and_metadata`: returns 201 with source `upload`, the SHA-256 as the ID, title, authors, year 2019 (from the header), abstract and full text.
2. `test_upload_filename_extension_is_case_insensitive`: `PAPER.PDF` returns 201.
3. `test_uploading_the_same_file_twice_is_refused`: returns 409 even under another filename, and one file is stored.
4. `test_arxiv_pdf_upload_gets_arxivs_exact_metadata`: a stamped PDF is saved as source `arxiv`, with metadata from arXiv and no LLM call.
5. `test_arxiv_pdf_upload_falls_back_to_heuristics_when_arxiv_is_unreachable`: uses the heuristics; the year is 2021 from the ID, not 2023 from the stamp.
6. `test_arxiv_pdf_upload_matches_the_same_paper_saved_from_search`: returns 409.
7. `test_upload_uses_the_llm_for_metadata_when_enabled`: one LLM call; its title, authors and year win, and the abstract stays heuristic because the LLM returned `null`.
8. `test_upload_still_works_when_the_llm_fails`: returns 201 with the heuristic metadata.
9. `test_duplicate_upload_is_refused_before_spending_an_llm_call`: two uploads of the same file make one LLM call.

### 13. Upload rejections (13 cases)
**What they test:** that bad files are refused with the right status code and a clear reason.
1. `test_upload_of_a_non_pdf_filename_is_rejected_with_415` (×4: `.txt`, `.pdf.exe`, no extension, `.docx`): returns 415 "Only PDF files can be uploaded."
2. `test_unusable_pdf_upload_is_rejected_with_422` (×6: empty, text, PNG, damaged, password-protected, scanned): returns 422 with the specific reason, and nothing is saved.
3. `test_upload_over_the_size_limit_is_rejected_with_413`: with the limit lowered to 1 MB, returns 413 "The PDF is larger than 1 MB."
4. `test_upload_exactly_at_the_size_limit_is_not_rejected_for_size`: returns 201.
5. `test_upload_without_a_file_is_rejected_with_422`: returns 422.

### 14. Listing, getting and serving PDFs (10 cases)
**What they test:** the read-only endpoints.
1. `test_library_starts_empty`: returns `[]`.
2. `test_library_lists_newest_first`: lists Third, Second, First, with public fields only.
3. `test_get_one_paper`: returns 200 with the paper's fields.
4. `test_get_missing_paper_is_404`: returns 404 "Paper not found. It may have been deleted."
5. `test_non_numeric_paper_id_is_rejected_with_422` (×2: `abc`, `1.5`): returns 422.
6. `test_stored_pdf_is_served_inline`: returns 200 `application/pdf`, shown in the browser rather than downloaded, with identical bytes.
7. `test_pdf_of_a_paper_without_one_is_404`: returns 404 "No PDF is stored for this paper."
8. `test_pdf_whose_file_was_removed_from_disk_is_404`: returns 404.
9. `test_pdf_of_a_missing_paper_is_404`: returns 404.

### 15. Deleting (5 cases)
**What they test:** that `DELETE` removes everything belonging to the paper and nothing else.
1. `test_delete_removes_the_paper_its_pdf_and_its_chat`: returns 204; the paper, its PDF file and its chat messages are gone.
2. `test_delete_leaves_other_papers_alone`: other papers and their chats are untouched.
3. `test_delete_works_when_the_pdf_file_is_already_gone`: still returns 204.
4. `test_deleting_twice_gives_404_the_second_time`: the second delete returns 404.
5. `test_deleted_paper_can_be_saved_again`: returns 201.

---

## D. LLM features through the API (`test_api_assistant.py`): 36 cases

### 16. Summary (10 cases)
**What they test:** generating, caching and describing a paper's summary.
1. `test_summary_info_before_any_summary`: `GET` returns `summary: null` and `based_on: full_text`, with no LLM call.
2. `test_summary_info_for_a_paper_with_only_an_abstract`: `based_on` is `abstract`.
3. `test_summary_info_for_a_paper_with_no_text_at_all`: `based_on` is `none`.
4. `test_summary_info_reports_a_truncated_paper`: `truncated` is `true`.
5. `test_summarize_generates_and_stores_a_summary`: `POST` stores the summary; the prompt contains the full text and the title.
6. `test_summary_is_cached_after_the_first_request`: a second `POST` returns the same summary with one LLM call in total.
7. `test_refresh_generates_a_new_summary`: `?refresh=true` replaces the old summary.
8. `test_summary_of_an_abstract_only_paper_tells_the_llm`: the prompt says only the abstract is available.
9. `test_summary_of_a_very_long_paper_sends_only_the_first_part`: the end of the text is cut, and the prompt says so.
10. `test_summary_of_a_paper_without_text_is_422_and_skips_the_llm`: returns 422 with no LLM call.

### 17. Questions, answers and history (17 cases)
**What they test:** `POST /ask` and `GET /messages`.
1. `test_ask_answers_and_records_the_exchange`: returns the answer; the history holds the trimmed question and the answer.
2. `test_chat_history_starts_empty`: returns `[]`.
3. `test_chat_history_is_oldest_first_and_per_paper`: returns this paper's questions only, in order.
4. `test_follow_up_questions_include_the_recent_history`: with 8 earlier messages, the LLM gets the last 6, oldest first, plus the new question.
5. `test_history_of_another_paper_is_not_sent`: other papers' chats are not sent.
6. `test_invalid_question_is_rejected_with_422` (×5: empty, 2001 characters, missing, null, wrong field): returns 422, with no LLM call and nothing stored.
7. `test_whitespace_only_question_is_rejected`: returns 422 "The question is empty."
8. `test_question_at_the_maximum_length_is_accepted`: 2000 characters returns 200.
9. `test_question_about_a_paper_without_text_is_422`: returns 422, and nothing is stored.
10. `test_llm_endpoints_of_a_missing_paper_are_404` (×4: both summary endpoints, ask, messages): each returns 404.

### 18. LLM failures through the API (9 cases)
**What they test:** that provider failures become clear HTTP errors and nothing half-finished is saved.
1. `test_llm_failures_become_friendly_http_errors` (×6), for both summary and ask, with nothing cached or recorded:
   - daily quota: 429 "…used up. Try again tomorrow."
   - rate limit: 429 "…try again shortly."
   - bad key or no key: 503
   - provider down: 502
   - bad request: 502, with the provider's message
2. `test_unconfigured_llm_is_503`: with no `LLM_BASE_URL`, returns 503 "The LLM is not configured…".
3. `test_real_llm_answer_is_returned_and_stored`: the `<think>` block is removed, the answer is stored, and the API key is sent.
4. `test_transient_llm_overload_is_retried_before_answering`: a 503 followed by success returns 200 after one 3 s wait.

---

## E. LLM client (`test_llm_client.py`): 51 cases

### 19. Mock mode, request and reply (15 cases)
**What they test:** how the client builds the request and reads the provider's reply.
1. `test_mock_mode_returns_canned_markdown_without_any_request`: returns "**Mock answer:**…" quoting the first line of the question.
2. `test_mock_mode_json_is_an_empty_object`: returns `{}`.
3. `test_chat_sends_an_openai_style_request`: POSTs `{model, messages}` to `/chat/completions` with a Bearer key; the reply is trimmed.
4. `test_trailing_slash_in_the_base_url_is_ignored`: the same URL is used.
5. `test_no_authorization_header_for_keyless_providers`: no header is sent.
6. `test_thinking_section_is_removed_from_the_reply`: `<think>…</think>` is stripped.
7. `test_empty_reply_is_an_error` (×4: empty, whitespace, null, only thinking): raises "The LLM returned an empty answer."
8. `test_malformed_reply_is_an_error` (×5: missing choices or content, a list, not JSON): raises "unexpected format".

### 20. JSON replies (7 cases)
**What they test:** pulling a JSON object out of a model's reply (used for upload metadata).
1. `test_chat_json_extracts_the_object` (×3: bare, in a code fence, surrounded by text): returns the dict.
2. `test_chat_json_rejects_anything_but_a_json_object` (×4): raises "The LLM did not return valid JSON."

### 21. Retries and backoff (15 cases)
**What they test:** which failures are retried, and how long the client waits.
1. `test_transient_failure_is_retried_after_a_short_wait` (×5: 429, 500, 502, 503, 504): waits 3 s, retries, and succeeds.
2. `test_persistent_rate_limit_gives_up_after_three_retries_with_backoff`: 4 attempts with waits of 3, 8 and 20 s, then a rate-limit error.
3. `test_persistent_server_error_is_reported_as_unavailable`: 4 attempts, then "unavailable right now (HTTP 500)".
4. `test_used_up_daily_quota_is_not_retried`: 1 attempt, no wait, then "Try again tomorrow."
5. `test_retry_after_header_sets_the_wait`: `Retry-After: 5` gives a 6 s wait.
6. `test_gemini_retry_delay_in_the_body_sets_the_wait`: `"retryDelay": "12s"` gives a 13 s wait.
7. `test_too_long_requested_wait_is_not_waited_for` (×3: 120 s header, 45 s body, exactly 30 s): fails at once.
8. `test_retries_can_be_turned_off`: `retries=0` makes 1 attempt.
9. `test_chat_json_honours_its_retry_count`: `retries=1` makes 2 attempts.

### 22. Errors that aren't retried (14 cases)
**What they test:** configuration, key and request errors that a retry can't fix.
1. `test_rejected_api_key` (×2: 401, 403): raises "rejected the API key" after 1 attempt.
2. `test_missing_api_key`: raises "needs an API key".
3. `test_rejected_request_includes_the_providers_message` (×4: OpenAI-style, Gemini-style, plain text, other JSON): the message includes the provider's own text.
4. `test_long_provider_message_is_shortened`: the error stays under 400 characters.
5. `test_missing_configuration_fails_without_a_request` (×2: no base URL, no model): raises "not configured" without sending a request.
6. `test_network_failures` (×3: two timeouts, unreachable): raises "took too long" or "Could not reach the LLM service".
7. `test_error_types_share_one_base_class`: both specific errors are `LLMError` subclasses, which the router relies on.

---

## F. PDF processing (`test_pdf_processor.py`): 30 cases

### 23. Text extraction (5 cases)
**What they test:** reading text and layout from a valid PDF.
1. `test_text_and_layout_of_the_first_page_are_extracted`: the text, page size, title font size (18 pt) and rotated arXiv stamp are all read.
2. `test_all_pages_are_extracted_in_order`: page 2 comes before page 3, separated by a blank line.
3. `test_document_info_fields_are_read_and_blank_ones_dropped`: title and author are kept; blank fields are omitted.
4. `test_words_hyphenated_at_a_line_break_are_rejoined`: "representa-↵tion" becomes "representation"; "Pre-↵Training" and "Large-scale" keep their hyphens.
5. `test_a_little_junk_before_the_pdf_header_is_tolerated`: the file still opens.

### 24. Rejecting unusable files (12 cases)
**What they test:** that files without usable text are refused with a clear reason.
1. `test_pdf_header_too_far_into_the_file_is_not_a_pdf`: a header after 1024 bytes means "not a PDF".
2. `test_non_pdf_files_are_rejected` (×5: empty, text, HTML, PNG, ZIP): raises "This file is not a PDF."
3. `test_damaged_pdfs_are_rejected` (×2: garbage after the header, truncated file): raises "…may be damaged."
4. `test_password_protected_pdf_is_rejected`: raises "The PDF is password-protected."
5. `test_scanned_pdf_without_a_text_layer_is_rejected` (×2: 1 page, 3 pages): raises "…probably a scanned document".
6. `test_too_little_text_for_the_number_of_pages_is_rejected`: one page of text plus 8 near-empty pages is rejected as scanned.

### 25. Downloads and storage (13 cases)
**What they test:** downloading PDFs and keeping them in `data/pdfs/`.
1. `test_download_returns_the_bytes_and_identifies_the_app`: returns the exact bytes and sends the app's User-Agent.
2. `test_download_follows_redirects`: a 301 is followed.
3. `test_download_failures_have_user_facing_messages` (×5: 404, 403, 500, timeout, unreachable): each has its own message.
4. `test_download_larger_than_the_limit_is_stopped`: raises "larger than 1 MB".
5. `test_download_exactly_at_the_limit_is_accepted`: succeeds.
6. `test_store_pdf_writes_under_the_data_folder`: writes `pdfs/<32 hex>.pdf` with the exact bytes.
7. `test_every_stored_pdf_gets_its_own_name`: the same bytes twice make two files.
8. `test_delete_pdf_removes_the_file`: the file is gone.
9. `test_deleting_a_missing_pdf_is_not_an_error`: no exception is raised.

---

## G. Metadata extraction for uploads (`test_metadata_extractor.py`): 80 cases

### 26. Title (14 cases)
**What they test:** finding the title on page 1, and the fallbacks when none is found.
1. `test_title_is_the_largest_text_in_the_top_half`: an 18 pt line beats the 9 pt header and 11 pt authors.
2. `test_title_spanning_several_lines_is_joined`: two 18 pt lines are joined into one title.
3. `test_title_stops_at_a_large_vertical_gap`: a distant 18 pt heading isn't added to the title.
4. `test_title_ignores_text_that_cannot_be_a_title` (×3: big text in the bottom half, "ACM" with under 4 letters, rotated text): the real title still wins.
5. `test_implausible_title_falls_back_to_the_pdf_title_field`: "Draft" is replaced by the PDF's title field.
6. `test_junk_title_fields_fall_back_to_the_filename` (×6: "Microsoft Word - paper.docx", "untitled", "main.tex", …): the filename (without `.pdf`) is used instead.
7. `test_no_usable_title_and_no_filename_gives_a_placeholder`: "Untitled paper".

### 27. Authors (16 cases)
**What they test:** finding author names and filtering out affiliations, emails and footnote marks.
1. `test_authors_from_the_pdf_author_field`: the field is split on `;`, `and` and `&` into 4 names.
2. `test_author_field_that_is_not_all_names_is_ignored` (×4: "LaTeX with hyperref", "admin", …): the names are read from the page lines instead.
3. `test_word_default_author_field_is_not_taken_as_a_name`: **xfail (known bug).** "Microsoft Office User" should be rejected, but it is currently accepted as a name.
4. `test_authors_from_the_lines_below_the_title`: footnote marks are stripped, lines are split on commas and "and", affiliation and email lines are skipped, and lines below "Abstract" are ignored.
5. `test_author_lines_keep_name_particles_and_drop_duplicates`: "Ludwig van Beethoven" and "Maria de la Cruz" are kept, each once.
6. `test_affiliations_and_other_text_are_not_authors` (×6: "Google Research", "Stanford University", …): returns `[]`.
7. `test_no_authors_without_a_title`: returns `[]`.
8. `test_author_list_is_capped`: 60 names become 50.

### 28. Year (12 cases)
**What they test:** where the publication year comes from, and which source wins.
1. `test_year_comes_from_the_arxiv_id`: ID `1706.…` gives 2017, even when a header or stamp says otherwise.
2. `test_year_from_a_venue_header` (×4: NeurIPS 2022, ICLR 2021, "© 2019 IEEE", a 2023 workshop): each year is found.
3. `test_year_from_a_header_with_numbers_before_the_year` (×2: JMLR volume, TMLR month): **xfail (known bug).** Expected 2014 and 2024, but the result is currently `None`.
4. `test_year_in_the_future_is_not_trusted`: a year 5 years ahead gives `None`.
5. `test_year_from_the_subject_field`: "2016 IEEE Conference…" gives 2016.
6. `test_year_from_the_creation_date`: `D:2019…` gives 2019.
7. `test_header_year_wins_over_the_creation_date`: 2021 from the header, not 2023 from the creation date.
8. `test_no_year_found`: `None`.

### 29. Abstract and arXiv ID (16 cases)
**What they test:** cutting out the abstract, and recognizing arXiv's margin stamp.
1. `test_abstract_is_the_text_between_its_heading_and_the_next_section` (×7 layouts: numbered and Roman-numeral headings, IEEE "Index Terms", "Keywords", a footnote, an arXiv stamp, ACM "CCS Concepts"): extracts exactly the abstract.
2. `test_abstract_line_breaks_are_joined`: a wrapped abstract becomes one line.
3. `test_no_abstract_heading_means_no_abstract`: "abstract" mid-sentence is not a heading, so the result is `None`.
4. `test_too_short_abstract_is_ignored`: under 100 characters gives `None`.
5. `test_abstract_without_an_end_marker_is_cut_at_a_sentence`: cut at 3000 characters or less, ending at a full stop.
6. `test_arxiv_id_from_the_margin_stamp` (×3): the ID is read without its version.
7. `test_arxiv_id_mentioned_in_the_text_is_not_a_stamp`: an ID cited in the text gives `None`.
8. `test_old_style_arxiv_ids_are_not_recognized`: `hep-th/…` gives `None`.

### 30. Metadata from generated PDFs (3 cases)
**What they test:** the whole extraction on actual PDF files rather than hand-built page data.
1. `test_all_fields_from_a_generated_paper`: title, 2 authors, year 2020 from the ICLR header, the abstract, and no arXiv ID.
2. `test_generated_arxiv_paper`: ID `1912.00001` and year 2019.
3. `test_paper_with_no_detectable_metadata_uses_the_filename`: title "My Notes", no authors, no abstract.

### 31. LLM improvement of metadata (19 cases)
**What they test:** when the LLM's values replace the heuristic ones.
1. `test_mock_llm_leaves_the_heuristics_unchanged`: nothing changes in mock mode.
2. `test_llm_values_replace_the_heuristic_ones`: plausible LLM values replace all four fields.
3. `test_llm_gets_the_start_of_the_paper_and_one_retry`: the LLM gets the first 8000 characters, with `retries=1`.
4. `test_implausible_llm_values_keep_the_heuristic_ones` (×12: a too-short or too-long title, nulls, authors as a string, empty or blank names, years 1850 / two years ahead / "unknown" / 2019.5, a short abstract, unknown keys): the heuristics stay unchanged.
5. `test_llm_author_names_are_cleaned`: whitespace is collapsed, and blanks, duplicates and names over 100 characters are dropped.
6. `test_llm_year_as_a_string_is_accepted`: "2019" becomes 2019.
7. `test_llm_failure_keeps_the_heuristics`: on an LLM error, the heuristics are kept.
8. `test_llm_metadata_through_the_real_client`: a fenced JSON reply updates the title and year, and the authors are kept.

---

## H. Prompts and paper text (`test_assistant_service.py`): 20 cases

### 32. Which text goes to the LLM (10 cases)
**What they test:** choosing between full text and abstract, and truncating long papers.
1. `test_full_text_is_preferred_over_the_abstract`: the full text is used.
2. `test_abstract_is_used_when_there_is_no_full_text` (×2: `None`, `""`): the abstract is used, flagged as abstract-only.
3. `test_no_text_at_all_raises` (×2): raises `NoTextError`.
4. `test_text_exactly_at_the_limit_is_not_cut`: not truncated.
5. `test_long_text_is_cut_at_a_paragraph_break_near_the_limit`: with a break at 950 of 1000 characters, the text is cut there.
6. `test_long_text_without_a_nearby_paragraph_break_is_cut_at_the_limit`: with the only break at 500, the text is cut at exactly 1000.
7. `test_long_abstract_is_cut_too`: a long abstract is truncated as well.
8. `test_summary_info_never_calls_the_llm`: no LLM call.

### 33. Prompt layout (10 cases)
**What they test:** the exact structure of the messages sent to the LLM.
1. `test_summary_prompt_has_the_paper_in_the_system_message`: the system message has a `<paper>` block with title, authors, year and text; the user message asks for the 5 summary sections.
2. `test_unknown_authors_and_year_are_spelled_out`: missing values appear as "unknown".
3. `test_notes_tell_the_llm_what_it_is_missing` (×4 combinations): the abstract-only and truncated notes appear only when they apply.
4. `test_paper_text_with_braces_is_inserted_verbatim`: LaTeX `{…}` in the paper doesn't break the template.
5. `test_question_prompt_puts_the_history_between_the_paper_and_the_question`: the order is system, history, question.
6. `test_question_and_summary_prompts_share_the_same_system_message`: both share an identical first message, so providers can cache it.
7. `test_metadata_prompt_asks_for_json_only`: says "Reply with only a JSON object".

---

## I. Frontend in a real browser (`tests/e2e/`): 54 cases

### 34. Search page (17 cases)
**What they test:** what a user sees and can do on `index.html`.
1. `test_footer_shows_the_backend_is_up`: the footer says "Backend: ok".
2. `test_search_shows_a_card_per_result`: the URL becomes `?q=…&source=auto`; there are 2 cards with title, "Alice Smith, Bob Jones · 2021", abstract, and links.
3. `test_search_runs_from_the_url`: opening a search URL fills the form and shows the results.
4. `test_openalex_choice_is_remembered`: after a reload, the picker still says OpenAlex, and arXiv is never called.
5. `test_no_results_message`: shows 'No papers found for "xyzzy"…'.
6. `test_search_failure_message`: shows a red "Search failed: …" naming both failures.
7. `test_notice_when_results_come_from_the_fallback`: shows "arXiv is unavailable right now…".
8. `test_saving_a_result_downloads_its_pdf`: the button turns into a disabled "Saved" with an "Open in library" link.
9. `test_saving_a_result_without_a_pdf_warns_about_it`: shows "Abstract only: no PDF available".
10. `test_results_already_in_the_library_are_marked_saved`: "Saved" is shown on page load.
11. `test_failed_save_shows_the_error_and_can_be_retried`: shows "Save failed: Database is locked", and the button is enabled again.
12. `test_save_refused_as_duplicate_counts_as_saved`: a 409 shows "Saved" with a link to the library.
13. `test_long_abstract_can_be_expanded_and_collapsed`: "Show more" toggles to "Show less".
14. `test_short_abstract_has_no_toggle`: no button.
15. `test_result_without_authors_or_year_or_abstract`: "Unknown authors · Year unknown" and "No abstract available."
16. `test_long_author_lists_are_shortened`: 6 names plus "et al. (9 authors)".
17. `test_paper_data_is_shown_as_text_never_as_html`: HTML in a title or abstract is displayed as plain text and never runs.

### 35. Library page (15 cases)
**What they test:** listing, uploading and deleting on `library.html`.
1. `test_empty_library_explains_how_to_add_papers`: shows the "Your library is empty…" message.
2. `test_saved_papers_are_listed_newest_first`: titles link to `paper.html?id=N`, with "Uploaded PDF" and warning badges.
3. `test_uploading_a_pdf_adds_it_to_the_top`: shows 'Added "…". Its details were read from the PDF…', and the new card is first.
4. `test_first_upload_replaces_the_empty_library_message`: the empty message disappears.
5. `test_uploading_an_arxiv_pdf_says_it_was_recognized`: shows "(recognized as an arXiv paper; details from arXiv)".
6. `test_rejected_uploads_show_why` (×4: `.txt`, fake PDF, password-protected, scanned): shows a red "Upload failed: <reason>", and nothing is saved.
7. `test_uploading_the_same_file_twice_is_refused`: shows "Upload failed: This paper is already in your library."
8. `test_deleting_a_paper_after_confirming`: the confirm dialog names the paper; the card and the database row are gone.
9. `test_cancelling_the_confirmation_keeps_the_paper`: the card stays, and the database is unchanged.
10. `test_deleting_a_paper_already_deleted_elsewhere_removes_its_card`: a 404 still removes the card.
11. `test_failed_delete_keeps_the_card_and_shows_the_error`: shows 'Could not delete "…": Disk is full'.
12. `test_library_that_cannot_be_loaded_shows_an_error`: shows "Could not load your library: 503 Service Unavailable".

### 36. Paper page: details and summary (5 cases)
**What they test:** the top half of `paper.html`.
1. `test_paper_details_are_shown`: title, authors, tab title, "No summary yet…", a "Summarize" button and 5 quick-question buttons.
2. `test_summarize_shows_and_stores_the_summary`: the summary appears, the button becomes "Regenerate", and the summary is saved.
3. `test_cached_summary_is_shown_on_load`: the saved summary shows without clicking.
4. `test_regenerate_asks_for_a_fresh_summary`: the request ends in `?refresh=true`.
5. `test_summary_failure_is_explained`: shows "Could not generate the summary: …quota…", and the button is enabled again.

### 37. Paper page: what the LLM sees (4 cases)
**What they test:** the notes that explain what text the LLM gets.
1. `test_abstract_only_paper_says_so`: a note and an "Abstract only" badge.
2. `test_very_long_paper_says_only_its_start_is_used`: a "very long" note.
3. `test_paper_without_any_text_offers_no_llm_features`: a note; Summarize, the quick questions and the question box are marked hidden.
4. `test_paper_without_any_text_hides_its_controls_even_without_the_cdn`: **xfail (known bug).** When Pico.css can't load, the quick-question buttons stay visible.

### 38. Paper page: questions and answers (6 cases)
**What they test:** asking questions in the chat section.
1. `test_asking_a_typed_question`: "You" and "Assistant" messages appear, the box is cleared, and 2 messages are saved.
2. `test_enter_sends_and_shift_enter_adds_a_line`: a two-line question is sent with Enter.
3. `test_quick_question_buttons_ask_right_away`: one click asks the question.
4. `test_blank_question_is_not_sent`: nothing happens.
5. `test_earlier_questions_are_shown_when_the_page_opens`: the 4 earlier messages appear in order.
6. `test_failed_question_is_kept_for_a_retry`: an error message; the question disappears from the chat but stays in the box; nothing is saved.

### 39. Paper page: bad links and Markdown (7 cases)
**What they test:** broken page links, and how LLM answers are rendered.
1. `test_page_without_a_valid_id` (×5: no ID, empty, `abc`, `1;DROP`, `-1`): shows "No paper selected. Open one from your library."
2. `test_page_of_a_deleted_paper`: shows a red "Could not load the paper: Paper not found…".
3. `test_llm_markdown_and_math_are_rendered_and_sanitized`: bold renders as bold and `$O(n^2)$` renders as math, while `<script>` and `onerror` are stripped and never run. This is the only test that needs internet (for the CDN libraries); it skips when offline.
