// Logic for library.html: list saved papers, delete, upload a PDF (Phase 3).

import { deletePaper, listPapers } from "./api.js";
import { clearStatus, el, paperPageUrl, renderPaperCard, showError, showLoading, showMessage } from "./ui.js";

const statusEl = document.getElementById("status");
const papersEl = document.getElementById("papers");

function showEmpty() {
  showMessage(statusEl, 'Your library is empty. Search for papers and click "Save to library" to add them here.');
}

function renderLibraryCard(paper) {
  const open = el("a", null, "Open");
  open.href = paperPageUrl(paper.id);
  const deleteButton = el("button", "secondary outline delete-button", "Delete");
  deleteButton.type = "button";

  const card = renderPaperCard(paper, { titleHref: paperPageUrl(paper.id), actions: [open, deleteButton] });

  deleteButton.addEventListener("click", async () => {
    if (!confirm(`Delete "${paper.title}" from your library?`)) return;
    deleteButton.disabled = true;
    deleteButton.setAttribute("aria-busy", "true");
    try {
      await deletePaper(paper.id);
    } catch (err) {
      // 404: already deleted (e.g. in another tab), so removing the card is still right.
      if (err.status !== 404) {
        deleteButton.disabled = false;
        deleteButton.removeAttribute("aria-busy");
        showError(statusEl, `Could not delete "${paper.title}": ${err.message}`);
        return;
      }
    }
    card.remove();
    if (papersEl.children.length === 0) showEmpty();
  });

  return card;
}

async function loadLibrary() {
  showLoading(statusEl, "Loading your library...");
  try {
    const papers = await listPapers();
    if (papers.length === 0) {
      showEmpty();
      return;
    }
    clearStatus(statusEl);
    papersEl.replaceChildren(...papers.map(renderLibraryCard));
  } catch (err) {
    showError(statusEl, `Could not load your library: ${err.message}`);
  }
}

loadLibrary();
