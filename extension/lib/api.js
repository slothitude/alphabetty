/** API fetch wrapper and SSE stream consumer for the Alphabetty extension. */

import { STORAGE_KEYS } from "./shared.js";

/**
 * Get stored config from chrome.storage.local.
 * @returns {Promise<{serverUrl: string, apiKey: string}>}
 */
export async function getConfig() {
  return new Promise((resolve) => {
    chrome.storage.local.get([STORAGE_KEYS.SERVER_URL, STORAGE_KEYS.API_KEY], (result) => {
      resolve({
        serverUrl: (result[STORAGE_KEYS.SERVER_URL] || "").replace(/\/+$/, ""),
        apiKey: result[STORAGE_KEYS.API_KEY] || "",
      });
    });
  });
}

/**
 * Authenticated fetch wrapper. Adds Authorization header.
 * @param {string} path — API path (e.g. "/api/v1/ext/health")
 * @param {RequestInit} [options]
 */
export async function apiFetch(path, options = {}) {
  const { serverUrl, apiKey } = await getConfig();
  if (!serverUrl || !apiKey) throw new Error("Not configured");

  const url = serverUrl + path;
  const headers = {
    ...options.headers,
    "Authorization": `Bearer ${apiKey}`,
    "Content-Type": "application/json",
  };

  const resp = await fetch(url, { ...options, headers });
  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    throw new Error(`HTTP ${resp.status}: ${body}`);
  }
  return resp;
}

/**
 * Send a chat message and consume the SSE stream.
 * Calls the onToken callback for each token, onDone when complete.
 * @param {object} params
 * @param {string} params.query
 * @param {number|null} params.conversationId
 * @param {string} params.mode
 * @param {boolean} params.searchEnabled
 * @param {string} [params.pageContext]
 * @param {(token: string) => void} params.onToken
 * @param {(data: object) => void} params.onDone
 * @param {(error: string) => void} params.onError
 */
export async function streamChat({ query, conversationId, mode, searchEnabled, pageContext, onToken, onDone, onError }) {
  const { serverUrl, apiKey } = await getConfig();
  if (!serverUrl || !apiKey) {
    onError("Not configured");
    return;
  }

  // Append page context as background if provided
  let fullQuery = query;
  if (pageContext) {
    fullQuery += `\n\n[Page Context]\nURL: ${pageContext.url}\nTitle: ${pageContext.title}`;
    if (pageContext.metaDescription) fullQuery += `\nDescription: ${pageContext.metaDescription}`;
    if (pageContext.selectedText) fullQuery += `\nSelected text: ${pageContext.selectedText}`;
    if (pageContext.headings?.length) fullQuery += `\nHeadings: ${pageContext.headings.join(" > ")}`;
  }

  const resp = await fetch(`${serverUrl}/api/v1/chat`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      query: fullQuery,
      conversation_id: conversationId,
      mode,
      search_enabled: searchEnabled,
    }),
  });

  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    onError(`HTTP ${resp.status}: ${body}`);
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      try {
        const data = JSON.parse(line.slice(6));
        if (data.type === "token") onToken(data.content);
        else if (data.type === "done") onDone(data);
        else if (data.type === "error") onError(data.error);
      } catch { /* skip malformed */ }
    }
  }
}
