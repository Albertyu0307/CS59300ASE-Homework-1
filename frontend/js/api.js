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

export function listPapers() {
  return request("/api/papers");
}

export function getPaper(id) {
  return request(`/api/papers/${encodeURIComponent(id)}`);
}

export function deletePaper(id) {
  return request(`/api/papers/${encodeURIComponent(id)}`, { method: "DELETE" });
}
