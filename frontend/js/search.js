// Logic for index.html: search form and results list.
// The form submits as index.html?q=..., so on load we read q from the URL and run the search.

import { getHealth, searchPapers } from "./api.js";
import { clearStatus, renderPaperCard, showError, showLoading, showMessage } from "./ui.js";

const healthEl = document.getElementById("health");
const inputEl = document.getElementById("search-input");
const statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results");

getHealth()
  .then((data) => { healthEl.textContent = `Backend: ${data.status}`; })
  .catch((err) => { healthEl.textContent = `Backend unreachable: ${err.message}`; });

async function runSearch(query) {
  resultsEl.replaceChildren();
  showLoading(statusEl, `Searching for "${query}"...`);
  try {
    const papers = await searchPapers(query);
    if (papers.length === 0) {
      showMessage(statusEl, `No papers found for "${query}". Try fewer or different words.`);
      return;
    }
    // The backend falls back to OpenAlex when arXiv is unavailable (e.g. rate-limited).
    if (papers[0].source === "openalex") {
      showMessage(statusEl, "arXiv is unavailable right now, so these results come from OpenAlex. Some may have no PDF.");
    } else {
      clearStatus(statusEl);
    }
    resultsEl.replaceChildren(...papers.map((paper) => renderPaperCard(paper)));
  } catch (err) {
    showError(statusEl, `Search failed: ${err.message}`);
  }
}

const query = new URLSearchParams(window.location.search).get("q")?.trim();
if (query) {
  inputEl.value = query;
  runSearch(query);
} else {
  inputEl.focus();
}
