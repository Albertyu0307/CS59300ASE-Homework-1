// Logic for index.html: search form and results list.
// The form submits as index.html?q=...&source=..., so on load we read both from the URL and run the search.

import { getHealth, listPapers, savePaper, searchPapers } from "./api.js";
import {
  clearStatus, el, paperPageUrl, renderPaperCard, showError, showLoading, showMessage, textStatusLabel,
} from "./ui.js";

const SOURCES = ["auto", "openalex"]; // the <select> values; see searchPapers() in api.js
const SOURCE_STORAGE_KEY = "searchSource";

const healthEl = document.getElementById("health");
const inputEl = document.getElementById("search-input");
const sourceEl = document.getElementById("search-source");
const statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results");

getHealth()
  .then((data) => { healthEl.textContent = `Backend: ${data.status}`; })
  .catch((err) => { healthEl.textContent = `Backend unreachable: ${err.message}`; });

// Search results and saved papers both carry source + external_id, which identify a paper.
function libraryKey(paper) {
  return `${paper.source}:${paper.external_id}`;
}

// The "Save to library" button of one result. savedId is the paper's library id if it's already saved.
function renderSaveControl(paper, savedId) {
  const control = el("span", "save-control");
  if (savedId !== undefined) {
    showSaved(control, savedId);
    return control;
  }

  const button = el("button", "save-button", "Save to library");
  button.type = "button";
  const errorEl = el("small", "error");
  button.addEventListener("click", async () => {
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    // The backend downloads the PDF and extracts its text, which takes a few seconds.
    button.textContent = paper.pdf_url ? "Saving (downloading PDF)..." : "Saving...";
    errorEl.textContent = "";
    try {
      const saved = await savePaper(paper);
      showSaved(control, saved.id, textStatusLabel(saved.text_status));
    } catch (err) {
      if (err.status === 409) {
        showSaved(control, null); // saved in the meantime, e.g. from another tab
        return;
      }
      button.disabled = false;
      button.removeAttribute("aria-busy");
      button.textContent = "Save to library";
      errorEl.textContent = `Save failed: ${err.message}`;
    }
  });
  control.append(button, errorEl);
  return control;
}

// Replaces the Save button with a "Saved" marker and a link to the paper (or the library, if the id is unknown).
// warning: shown when the paper was saved without its full text (see textStatusLabel).
function showSaved(control, id, warning = null) {
  const marker = el("button", "save-button secondary", "Saved");
  marker.type = "button";
  marker.disabled = true;
  const link = el("a", null, "Open in library");
  link.href = id == null ? "library.html" : paperPageUrl(id);
  control.replaceChildren(marker, link);
  if (warning) control.append(el("small", "badge warning", warning));
}

// The source picked last time, so choosing OpenAlex once makes it the default on later visits.
// localStorage can be unavailable (e.g. blocked site data); then the choice just isn't remembered.
function rememberedSource() {
  try {
    return localStorage.getItem(SOURCE_STORAGE_KEY);
  } catch {
    return null;
  }
}

sourceEl.addEventListener("change", () => {
  try {
    localStorage.setItem(SOURCE_STORAGE_KEY, sourceEl.value);
  } catch {
    // Not remembered; the choice still applies to the next search.
  }
});

async function runSearch(query, source) {
  resultsEl.replaceChildren();
  showLoading(statusEl, `Searching for "${query}"...`);
  try {
    // If the library can't be loaded, results just lack their "Saved" marks; saving again gets a 409.
    const [papers, saved] = await Promise.all([searchPapers(query, { source }), listPapers().catch(() => [])]);
    const savedIds = new Map(saved.map((paper) => [libraryKey(paper), paper.id]));
    if (papers.length === 0) {
      showMessage(statusEl, `No papers found for "${query}". Try fewer or different words.`);
      return;
    }
    // In "auto" mode the backend falls back to OpenAlex when arXiv is unavailable (e.g. rate-limited).
    if (source === "auto" && papers[0].source === "openalex") {
      showMessage(statusEl, "arXiv is unavailable right now, so these results come from OpenAlex. Some may have no PDF.");
    } else {
      clearStatus(statusEl);
    }
    resultsEl.replaceChildren(
      ...papers.map((paper) =>
        renderPaperCard(paper, { actions: [renderSaveControl(paper, savedIds.get(libraryKey(paper)))] }),
      ),
    );
  } catch (err) {
    showError(statusEl, `Search failed: ${err.message}`);
  }
}

const params = new URLSearchParams(window.location.search);
// The URL wins (refresh, back button), then the remembered choice, then "auto".
const source = [params.get("source"), rememberedSource()].find((value) => SOURCES.includes(value)) ?? "auto";
sourceEl.value = source;

const query = params.get("q")?.trim();
if (query) {
  inputEl.value = query;
  runSearch(query, source);
} else {
  inputEl.focus();
}
