// One fetch() wrapper per backend endpoint (see PLAN.md section 8).

// Calls the backend and returns the parsed JSON body (null for 204 No Content).
// Throws an Error with the backend's message if the response is not 2xx; error.status is the HTTP status.
async function request(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body.detail) message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // Body wasn't JSON; keep the status text.
    }
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return response.status === 204 ? null : response.json();
}

function postJson(path, data) {
  return request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function getHealth() {
  return request("/api/health");
}

// source: "auto" (arXiv, with OpenAlex as the fallback) or "openalex" (OpenAlex only).
export function searchPapers(query, { source = "auto", limit = 20 } = {}) {
  const params = new URLSearchParams({ q: query, limit, source });
  return request(`/api/search?${params}`);
}

// Library. savePaper takes a whole search result; it fails with status 409 if it's already saved.
export function savePaper(searchResult) {
  return postJson("/api/papers", searchResult);
}

// Uploads a PDF (a File from an <input type="file">). The backend extracts its text and metadata.
export function uploadPaper(file) {
  const form = new FormData();
  form.append("file", file);
  return request("/api/papers/upload", { method: "POST", body: form });
}

// URL of a saved paper's stored PDF (only for papers with has_pdf), for a link rather than fetch().
export function paperPdfUrl(id) {
  return `/api/papers/${encodeURIComponent(id)}/pdf`;
}

export function listPapers() {
  return request("/api/papers");
}

export function getPaper(id) {
  return request(`/api/papers/${encodeURIComponent(id)}`);
}

export function deletePaper(id) {
  return request(`/api/papers/${encodeURIComponent(id)}`, { method: "DELETE" });
}

// LLM features of a saved paper. getSummary never calls the LLM: it returns the cached summary
// (null if none yet) plus based_on ("full_text" / "abstract" / "none") and truncated.
export function getSummary(id) {
  return request(`/api/papers/${encodeURIComponent(id)}/summary`);
}

// Generates the summary (or returns the cached one); refresh: true generates a new one.
export function generateSummary(id, { refresh = false } = {}) {
  return request(`/api/papers/${encodeURIComponent(id)}/summary?refresh=${refresh}`, { method: "POST" });
}

// Returns { answer } (Markdown). The question and answer are added to the paper's chat history.
export function askQuestion(id, question) {
  return postJson(`/api/papers/${encodeURIComponent(id)}/ask`, { question });
}

// The paper's chat history, oldest first: [{ id, role: "user" | "assistant", content, created_at }].
export function getMessages(id) {
  return request(`/api/papers/${encodeURIComponent(id)}/messages`);
}
