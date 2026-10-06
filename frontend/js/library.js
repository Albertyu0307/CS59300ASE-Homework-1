// Logic for library.html: list saved papers, delete, upload a PDF.

import { deletePaper, listPapers, uploadPaper } from "./api.js";
import { clearStatus, el, paperPageUrl, renderPaperCard, showError, showLoading, showMessage } from "./ui.js";

const uploadFormEl = document.getElementById("upload-form");
const uploadInputEl = document.getElementById("upload-input");
const uploadStatusEl = document.getElementById("upload-status");
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

async function upload(file) {
  uploadInputEl.disabled = true;
  showLoading(uploadStatusEl, `Processing "${file.name}": extracting its text and details...`);
  try {
    const paper = await uploadPaper(file);
    if (papersEl.children.length === 0) clearStatus(statusEl); // drop the "library is empty" message
    papersEl.prepend(renderLibraryCard(paper));
    // A PDF from arXiv gets arXiv's exact details; others are read from the PDF by heuristics.
    showMessage(
      uploadStatusEl,
      paper.source === "upload"
        ? `Added "${paper.title}". Its details were read from the PDF automatically, so please check them below.`
        : `Added "${paper.title}" (recognized as an arXiv paper; details from arXiv).`,
    );
  } catch (err) {
    showError(uploadStatusEl, `Upload failed: ${err.message}`);
  } finally {
    uploadInputEl.disabled = false;
    uploadFormEl.reset(); // so choosing the same file again triggers another upload
  }
}

uploadInputEl.addEventListener("change", () => {
  const file = uploadInputEl.files[0];
  if (file) upload(file);
});
// Pressing Enter in the form would otherwise reload the page.
uploadFormEl.addEventListener("submit", (event) => event.preventDefault());

loadLibrary();
