// Logic for paper.html?id=N: the paper's details, its LLM summary, and questions and answers.

import { askQuestion, generateSummary, getMessages, getPaper, getSummary } from "./api.js";
import { clearStatus, el, renderMarkdown, renderPaperCard, showError, showLoading } from "./ui.js";

// The example questions from the handout, one click each.
const QUICK_QUESTIONS = [
  "What problem does this paper address?",
  "What is the main idea of the proposed approach?",
  "What datasets are used?",
  "What are the major limitations?",
  "How does this method compare with the baselines?",
];

const statusEl = document.getElementById("status");
const paperEl = document.getElementById("paper");
const assistantEl = document.getElementById("assistant");
const contextNoteEl = document.getElementById("context-note");
const summaryButton = document.getElementById("summary-button");
const summaryStatusEl = document.getElementById("summary-status");
const summaryEl = document.getElementById("summary");
const chatLogEl = document.getElementById("chat-log");
const quickQuestionsEl = document.getElementById("quick-questions");
const chatStatusEl = document.getElementById("chat-status");
const askForm = document.getElementById("ask-form");
const questionInput = document.getElementById("question");

let paperId = null;
let hasSummary = false;
let asking = false;

async function loadPaper(id) {
  showLoading(statusEl, "Loading paper...");
  try {
    const paper = await getPaper(id);
    document.title = `${paper.title} · AI Research Assistant`;
    clearStatus(statusEl);
    paperEl.replaceChildren(renderPaperCard(paper));
  } catch (err) {
    showError(statusEl, `Could not load the paper: ${err.message}`);
    return;
  }
  paperId = id;
  assistantEl.hidden = false;
  loadSummary();
  loadChat();
}

// --- Summary ---------------------------------------------------------------

// Shows the cached summary, if any, and says what text the LLM gets to see. No LLM call.
async function loadSummary() {
  showLoading(summaryStatusEl, "Loading...");
  try {
    const info = await getSummary(paperId);
    clearStatus(summaryStatusEl);
    showContextNote(info);
    showSummary(info.summary);
  } catch (err) {
    showError(summaryStatusEl, `Could not load the summary: ${err.message}`);
  }
}

function showContextNote({ based_on: basedOn, truncated }) {
  let note = null;
  if (basedOn === "none") {
    note = "This paper has no PDF text and no abstract, so there is nothing to summarize or ask about.";
    // Nothing would work, so don't offer it.
    summaryButton.hidden = true;
    quickQuestionsEl.hidden = true;
    askForm.hidden = true;
  } else if (basedOn === "abstract") {
    note = "No full text is available for this paper, so the summary and answers are based on its abstract only.";
  } else if (truncated) {
    note = "This paper is very long, so only its first part is sent to the LLM. Answers about its later sections may be incomplete.";
  }
  contextNoteEl.hidden = note === null;
  contextNoteEl.textContent = note ?? "";
}

function showSummary(summary) {
  hasSummary = Boolean(summary);
  summaryButton.textContent = hasSummary ? "Regenerate" : "Summarize";
  if (hasSummary) {
    renderMarkdown(summaryEl, summary);
  } else {
    summaryEl.replaceChildren(el("p", "muted", "No summary yet. Click Summarize to generate one from the paper's text."));
  }
}

summaryButton.addEventListener("click", async () => {
  summaryButton.disabled = true;
  summaryButton.setAttribute("aria-busy", "true");
  showLoading(summaryStatusEl, "Generating the summary. This can take up to a minute...");
  try {
    const result = await generateSummary(paperId, { refresh: hasSummary });
    clearStatus(summaryStatusEl);
    showSummary(result.summary);
  } catch (err) {
    showError(summaryStatusEl, `Could not generate the summary: ${err.message}`);
  } finally {
    summaryButton.disabled = false;
    summaryButton.removeAttribute("aria-busy");
  }
});

// --- Questions and answers -------------------------------------------------

async function loadChat() {
  try {
    const messages = await getMessages(paperId);
    chatLogEl.replaceChildren(...messages.map(renderMessage));
  } catch (err) {
    showError(chatStatusEl, `Could not load the earlier questions: ${err.message}`);
  }
}

// One chat bubble. Questions are plain text; answers are Markdown from the LLM.
function renderMessage({ role, content }) {
  const item = el("div", `chat-message ${role}`);
  item.append(el("p", "chat-role", role === "user" ? "You" : "Assistant"));
  if (role === "user") {
    item.append(el("p", "chat-question", content));
  } else {
    const body = el("div", "markdown");
    renderMarkdown(body, content);
    item.append(body);
  }
  return item;
}

function setAsking(value) {
  asking = value;
  const submit = askForm.querySelector("button[type=submit]");
  submit.disabled = value;
  if (value) submit.setAttribute("aria-busy", "true");
  else submit.removeAttribute("aria-busy");
  for (const button of quickQuestionsEl.querySelectorAll("button")) button.disabled = value;
}

// fromForm: the question was typed, so the input is cleared once it's answered (and kept on errors).
async function ask(question, { fromForm = false } = {}) {
  question = question.trim();
  if (!question || asking) return;
  setAsking(true);
  const questionEl = renderMessage({ role: "user", content: question });
  chatLogEl.append(questionEl);
  showLoading(chatStatusEl, "Thinking. This can take up to a minute...");
  try {
    const { answer } = await askQuestion(paperId, question);
    const answerEl = renderMessage({ role: "assistant", content: answer });
    chatLogEl.append(answerEl);
    clearStatus(chatStatusEl);
    if (fromForm) questionInput.value = "";
    answerEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) {
    questionEl.remove(); // it wasn't saved; the question stays in the input to retry
    showError(chatStatusEl, `Could not answer the question: ${err.message}`);
  } finally {
    setAsking(false);
  }
}

quickQuestionsEl.append(
  el("span", "muted", "Try:"),
  ...QUICK_QUESTIONS.map((question) => {
    const button = el("button", "secondary outline", question);
    button.type = "button";
    button.addEventListener("click", () => ask(question));
    return button;
  }),
);

askForm.addEventListener("submit", (event) => {
  event.preventDefault();
  ask(questionInput.value, { fromForm: true });
});

// Enter sends the question; Shift+Enter starts a new line.
questionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    askForm.requestSubmit();
  }
});

const id = new URLSearchParams(window.location.search).get("id");
if (id && /^\d+$/.test(id)) {
  loadPaper(id);
} else {
  showError(statusEl, "No paper selected. Open one from your library.");
}
