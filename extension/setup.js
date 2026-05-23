/** Setup page logic — validates connection, stores credentials. */

import { STORAGE_KEYS, CONNECTION_STATES } from "./lib/shared.js";

const form = document.getElementById("setup-form");
const serverInput = document.getElementById("server-url");
const keyInput = document.getElementById("api-key");
const errorEl = document.getElementById("setup-error");
const connectBtn = document.getElementById("connect-btn");

// Pre-fill saved values
chrome.storage.local.get([STORAGE_KEYS.SERVER_URL, STORAGE_KEYS.API_KEY], (result) => {
  if (result[STORAGE_KEYS.SERVER_URL]) serverInput.value = result[STORAGE_KEYS.SERVER_URL];
  if (result[STORAGE_KEYS.API_KEY]) keyInput.value = result[STORAGE_KEYS.API_KEY];
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  errorEl.hidden = true;
  connectBtn.disabled = true;
  connectBtn.textContent = "Connecting...";

  const serverUrl = serverInput.value.trim().replace(/\/+$/, "");
  const apiKey = keyInput.value.trim();

  if (!serverUrl || !apiKey) {
    showError("Both fields are required");
    return;
  }

  try {
    const resp = await fetch(`${serverUrl}/api/v1/ext/health`, {
      headers: { "Authorization": `Bearer ${apiKey}` },
    });

    if (!resp.ok) {
      const body = await resp.text().catch(() => "");
      if (resp.status === 401) {
        showError("Invalid API key");
      } else {
        showError(`Server error: ${resp.status} ${body}`);
      }
      return;
    }

    const data = await resp.json();
    if (data.status !== "ok") {
      showError("Unexpected response from server");
      return;
    }

    // Save credentials
    await chrome.storage.local.set({
      [STORAGE_KEYS.SERVER_URL]: serverUrl,
      [STORAGE_KEYS.API_KEY]: apiKey,
      [STORAGE_KEYS.CONNECTION_STATE]: CONNECTION_STATES.CONNECTED,
    });

    // Notify background to update badge
    chrome.runtime.sendMessage({ type: "healthCheck" });

    // Switch to sidepanel
    location.href = "sidepanel.html";
  } catch (e) {
    showError(`Connection failed: ${e.message}`);
  } finally {
    connectBtn.disabled = false;
    connectBtn.textContent = "Connect";
  }
});

function showError(msg) {
  errorEl.textContent = msg;
  errorEl.hidden = false;
  connectBtn.disabled = false;
  connectBtn.textContent = "Connect";
}
