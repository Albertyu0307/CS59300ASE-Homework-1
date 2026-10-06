// Shared helpers used by every page: renderPaperCard(), showLoading(), showError(), renderMarkdown().
// Insert paper data with textContent, never innerHTML; LLM Markdown goes through DOMPurify.

const MAX_AUTHORS_SHOWN = 6;
const ABSTRACT_COLLAPSE_CHARS = 300; // shorter abstracts are shown in full, with no toggle

// Creates an element with optional text (set via textContent, so it's never parsed as HTML).
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function formatAuthors(authors) {
  if (!authors || authors.length === 0) return "Unknown authors";
  if (authors.length <= MAX_AUTHORS_SHOWN) return authors.join(", ");
  return `${authors.slice(0, MAX_AUTHORS_SHOWN).join(", ")}, et al. (${authors.length} authors)`;
}

// Builds the card for one paper (a search result or a saved paper) and returns the <article>.
// options.actions: extra elements (e.g. a Save button) appended to the card's footer.
export function renderPaperCard(paper, { actions = [] } = {}) {
  const card = el("article", "paper-card");

  const header = el("header");
  const title = el("h3", "paper-title");
  if (paper.url) {
    const link = el("a", null, paper.title);
    link.href = paper.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    title.append(link);
  } else {
    title.textContent = paper.title;
  }
  header.append(title);

  const meta = [formatAuthors(paper.authors), paper.year ?? "Year unknown"].join(" · ");
  header.append(el("p", "paper-meta", meta));
  card.append(header);

  if (paper.abstract) {
    const abstract = el("p", "paper-abstract", paper.abstract);
    card.append(abstract);
    if (paper.abstract.length > ABSTRACT_COLLAPSE_CHARS) {
      abstract.classList.add("collapsed");
      const toggle = el("button", "secondary outline toggle-abstract", "Show more");
      toggle.type = "button";
      toggle.addEventListener("click", () => {
        const collapsed = abstract.classList.toggle("collapsed");
        toggle.textContent = collapsed ? "Show more" : "Show less";
      });
      card.append(toggle);
    }
  } else {
    card.append(el("p", "paper-abstract muted", "No abstract available."));
  }

  const footer = el("footer", "paper-actions");
  if (paper.url) {
    const link = el("a", null, paper.source === "arxiv" ? "View on arXiv" : "View paper");
    link.href = paper.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    footer.append(link);
  }
  if (paper.pdf_url) {
    const link = el("a", null, "PDF");
    link.href = paper.pdf_url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    footer.append(link);
  }
  footer.append(...actions);
  card.append(footer);

  return card;
}

// Status helpers: each takes the element that shows the page's status line.
export function showLoading(statusEl, message = "Loading...") {
  statusEl.hidden = false;
  statusEl.className = "status";
  statusEl.setAttribute("aria-busy", "true");
  statusEl.textContent = message;
}

export function showError(statusEl, message) {
  statusEl.hidden = false;
  statusEl.className = "status error";
  statusEl.removeAttribute("aria-busy");
  statusEl.textContent = message;
}

export function showMessage(statusEl, message) {
  statusEl.hidden = false;
  statusEl.className = "status";
  statusEl.removeAttribute("aria-busy");
  statusEl.textContent = message;
}

export function clearStatus(statusEl) {
  statusEl.hidden = true;
  statusEl.removeAttribute("aria-busy");
  statusEl.textContent = "";
}
