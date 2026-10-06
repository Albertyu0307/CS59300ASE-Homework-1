// Logic for paper.html?id=N: paper details, summary, chat, quick questions.
// So far it shows the paper's details; the summary and chat come in Phase 4.

import { getPaper } from "./api.js";
import { clearStatus, renderPaperCard, showError, showLoading } from "./ui.js";

const statusEl = document.getElementById("status");
const paperEl = document.getElementById("paper");

async function loadPaper(id) {
  showLoading(statusEl, "Loading paper...");
  try {
    const paper = await getPaper(id);
    document.title = `${paper.title} · AI Research Assistant`;
    clearStatus(statusEl);
    paperEl.replaceChildren(renderPaperCard(paper));
  } catch (err) {
    showError(statusEl, `Could not load the paper: ${err.message}`);
  }
}

const id = new URLSearchParams(window.location.search).get("id");
if (id && /^\d+$/.test(id)) {
  loadPaper(id);
} else {
  showError(statusEl, "No paper selected. Open one from your library.");
}
