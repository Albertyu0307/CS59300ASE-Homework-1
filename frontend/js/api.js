// One fetch() wrapper per backend endpoint (see PLAN.md section 8).

// Calls the backend and returns the parsed JSON body.
// Throws an Error with the backend's message if the response is not 2xx.
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
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

export function getHealth() {
  return request("/api/health");
}
