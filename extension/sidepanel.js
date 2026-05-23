/** Sidebar panel — chat UI, SSE streaming, page context injection. */

import { MSG_TYPES, STORAGE_KEYS, CONNECTION_STATES } from "./lib/shared.js";
import { getConfig, streamChat, apiFetch } from "./lib/api.js";

// ── DOM refs ──
const messagesEl = document.getElementById("messages");
const chatInput = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");
const modeSelect = document.getElementById("mode-select");
const searchToggle = document.getElementById("search-toggle");
const contextBtn = document.getElementById("context-btn");
const connectionDot = document.getElementById("connection-dot");
const connectionBar = document.getElementById("connection-bar");
const reconnectBtn = document.getElementById("reconnect-btn");
const settingsBtn = document.getElementById("settings-btn");

// ── State ──
let conversationId = null;
let includeContext = false;
let pageContext = null;
let isStreaming = false;
let pendingQuery = null; // from context menu

// ── Init ──
async function init() {
  const { serverUrl, apiKey } = await getConfig();
  if (!serverUrl || !apiKey) {
    location.href = "setup.html";
    return;
  }
  await updateConnectionState();
}

// ── Connection State ──
async function updateConnectionState() {
  const state = await new Promise((resolve) => {
    chrome.storage.local.get(STORAGE_KEYS.CONNECTION_STATE, (r) => {
      resolve(r[STORAGE_KEYS.CONNECTION_STATE]);
    });
  });

  connectionDot.className = `status-dot ${state || CONNECTION_STATES.DISCONNECTED}`;
  const connected = state === CONNECTION_STATES.CONNECTED;
  connectionBar.hidden = connected;
  sendBtn.disabled = !connected;
}

chrome.storage.onChanged.addListener((changes) => {
  if (changes[STORAGE_KEYS.CONNECTION_STATE]) {
    updateConnectionState();
  }
});

// ── Settings / Reconnect ──
settingsBtn.addEventListener("click", () => { location.href = "setup.html"; });
reconnectBtn.addEventListener("click", () => { location.href = "setup.html"; });

// ── Page Context ──
contextBtn.addEventListener("click", async () => {
  includeContext = !includeContext;
  contextBtn.classList.toggle("active", includeContext);
  if (includeContext) {
    pageContext = await fetchPageContext();
  }
});

async function fetchPageContext() {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type: MSG_TYPES.GET_PAGE_CONTEXT }, (resp) => {
      if (chrome.runtime.lastError || resp?.error) {
        resolve(null);
      } else {
        resolve(resp);
      }
    });
  });
}

// ── Quick Actions ──
document.querySelectorAll(".quick-btn").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const action = btn.dataset.action;
    if (action === "ask-page") {
      pageContext = await fetchPageContext();
      includeContext = !!pageContext;
      contextBtn.classList.toggle("active", includeContext);
      chatInput.value = "What is this page about?";
      chatInput.focus();
    } else if (action === "search-graph") {
      pageContext = await fetchPageContext();
      includeContext = !!pageContext;
      contextBtn.classList.toggle("active", includeContext);
      chatInput.value = "Search my knowledge graph";
      chatInput.focus();
    }
  });
});

// ── Chat ──
chatInput.addEventListener("input", () => {
  chatInput.style.height = "auto";
  chatInput.style.height = Math.min(chatInput.scrollHeight, 100) + "px";
});

chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

sendBtn.addEventListener("click", sendMessage);

async function sendMessage() {
  const query = chatInput.value.trim();
  if (!query || isStreaming) return;

  // Add user message to UI
  appendMessage("user", query);
  chatInput.value = "";
  chatInput.style.height = "auto";

  // Fetch fresh context if needed
  let ctx = includeContext ? pageContext : null;
  if (includeContext && !ctx) {
    ctx = await fetchPageContext();
  }

  // Show typing indicator
  const typingEl = appendTyping();
  isStreaming = true;
  sendBtn.disabled = true;

  let assistantEl = null;
  let fullText = "";

  await streamChat({
    query,
    conversationId,
    mode: modeSelect.value,
    searchEnabled: searchToggle.checked,
    pageContext: ctx,
    onToken: (token) => {
      if (typingEl) { typingEl.remove(); }
      if (!assistantEl) {
        assistantEl = appendMessage("assistant", "");
      }
      fullText += token;
      assistantEl.textContent = fullText;
      scrollToBottom();
    },
    onDone: (data) => {
      conversationId = data.conversation_id;
      isStreaming = false;
      sendBtn.disabled = false;

      // Add sources
      if (data.sources?.length && assistantEl) {
        const srcDiv = document.createElement("div");
        srcDiv.className = "msg-sources";
        srcDiv.innerHTML = "<strong>Sources:</strong><br>" +
          data.sources.map((s) => `<a href="${s.url}" target="_blank">${s.title || s.url}</a>`).join(" | ");
        assistantEl.appendChild(srcDiv);
      }

      // Add follow-ups
      if (data.follow_ups?.length && assistantEl) {
        const fuDiv = document.createElement("div");
        fuDiv.className = "follow-ups";
        data.follow_ups.forEach((fu) => {
          const btn = document.createElement("button");
          btn.className = "follow-up-btn";
          btn.textContent = fu;
          btn.addEventListener("click", () => {
            chatInput.value = fu;
            sendMessage();
          });
          fuDiv.appendChild(btn);
        });
        assistantEl.appendChild(fuDiv);
      }

      scrollToBottom();
    },
    onError: (error) => {
      if (typingEl) typingEl.remove();
      appendMessage("error", error);
      isStreaming = false;
      sendBtn.disabled = false;
    },
  });
}

// ── DOM Helpers ──
function appendMessage(role, content) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.textContent = content;
  messagesEl.appendChild(el);
  scrollToBottom();
  return el;
}

function appendTyping() {
  const el = document.createElement("div");
  el.className = "msg assistant";
  el.innerHTML = '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>';
  messagesEl.appendChild(el);
  scrollToBottom();
  return el;
}

function scrollToBottom() {
  const area = document.getElementById("chat-area");
  area.scrollTop = area.scrollHeight;
}

// ── Context Menu Query ──
chrome.runtime.onMessage.addListener((message) => {
  if (message.type === MSG_TYPES.OPEN_SIDEBAR && message.query) {
    chatInput.value = message.query;
    chatInput.focus();
  }
});

// ── Start ──
init();
