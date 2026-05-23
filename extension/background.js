/** Background service worker — routing, auth, health checks, context menus. */

import { MSG_TYPES, CONNECTION_STATES, STORAGE_KEYS } from "./lib/shared.js";
import { getConfig, apiFetch } from "./lib/api.js";

// ── Context Menu ──
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "ask-alphabetty",
    title: "Ask Alphabetty about '%s'",
    contexts: ["selection"],
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "ask-alphabetty") {
    // Open side panel and send the selected text as a query
    chrome.sidePanel.open({ tabId: tab.id });
    // Small delay to let panel load, then send message
    setTimeout(() => {
      chrome.runtime.sendMessage({
        type: MSG_TYPES.OPEN_SIDEBAR,
        query: info.selectionText,
      });
    }, 500);
  }
});

// ── Health Check ──
chrome.alarms.create("healthCheck", { periodInMinutes: 1 });

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === "healthCheck") {
    await checkHealth();
  }
});

async function checkHealth() {
  const { serverUrl, apiKey } = await getConfig();
  if (!serverUrl || !apiKey) {
    setConnectionState(CONNECTION_STATES.DISCONNECTED);
    return;
  }

  try {
    const resp = await apiFetch("/api/v1/ext/health");
    const data = await resp.json();
    if (data.status === "ok") {
      setConnectionState(CONNECTION_STATES.CONNECTED);
    } else {
      setConnectionState(CONNECTION_STATES.AUTH_FAILED);
    }
  } catch (e) {
    const state = String(e.message).includes("401")
      ? CONNECTION_STATES.AUTH_FAILED
      : CONNECTION_STATES.DISCONNECTED;
    setConnectionState(state);
  }
}

function setConnectionState(state) {
  chrome.storage.local.set({ [STORAGE_KEYS.CONNECTION_STATE]: state });

  // Badge
  const badgeMap = {
    [CONNECTION_STATES.CONNECTED]: { text: "", color: "#22c55e" },
    [CONNECTION_STATES.DISCONNECTED]: { text: "!", color: "#6b7280" },
    [CONNECTION_STATES.AUTH_FAILED]: { text: "!", color: "#ef4444" },
  };
  const badge = badgeMap[state] || badgeMap[CONNECTION_STATES.DISCONNECTED];
  chrome.action.setBadgeText({ text: badge.text });
  chrome.action.setBadgeBackgroundColor({ color: badge.color });
}

// ── Message Routing ──
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === MSG_TYPES.GET_PAGE_CONTEXT) {
    // Inject content script into active tab and get context
    (async () => {
      try {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tab?.id) {
          sendResponse({ error: "No active tab" });
          return;
        }

        // Inject content script if not already there
        try {
          await chrome.scripting.executeScript({
            target: { tabId: tab.id },
            files: ["content.js"],
          });
        } catch { /* already injected */ }

        // Ask content script for page context
        const response = await chrome.tabs.sendMessage(tab.id, {
          type: MSG_TYPES.GET_PAGE_CONTEXT,
        });
        sendResponse(response);
      } catch (e) {
        sendResponse({ error: e.message });
      }
    })();
    return true; // async sendResponse
  }

  if (message.type === MSG_TYPES.HEALTH_CHECK) {
    checkHealth().then(() => {
      chrome.storage.local.get(STORAGE_KEYS.CONNECTION_STATE, (result) => {
        sendResponse({ state: result[STORAGE_KEYS.CONNECTION_STATE] });
      });
    });
    return true;
  }
});

// ── Side panel on action click ──
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });

// Initial health check
checkHealth();
