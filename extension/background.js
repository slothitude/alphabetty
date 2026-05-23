// Background service worker - routing, auth, health, context menus, auto-connect
// Self-contained, no module imports. Uses world:MAIN to bypass CSP.

var STORAGE_KEYS = {
  SERVER_URL: "serverUrl",
  API_KEY: "apiKey",
  CONNECTION_STATE: "connectionState",
};
var CONNECTION_STATES = {
  CONNECTED: "connected",
  DISCONNECTED: "disconnected",
  AUTH_FAILED: "auth_failed",
};

function _getConfig() {
  return new Promise(function (resolve) {
    chrome.storage.local.get(
      [STORAGE_KEYS.SERVER_URL, STORAGE_KEYS.API_KEY],
      function (result) {
        resolve({
          serverUrl: (result[STORAGE_KEYS.SERVER_URL] || "").replace(/\/+$/, ""),
          apiKey: result[STORAGE_KEYS.API_KEY] || "",
        });
      }
    );
  });
}

function _apiFetch(path, options) {
  options = options || {};
  return _getConfig().then(function (cfg) {
    if (!cfg.serverUrl || !cfg.apiKey) throw new Error("Not configured");
    var headers = Object.assign({}, options.headers || {}, {
      Authorization: "Bearer " + cfg.apiKey,
      "Content-Type": "application/json",
    });
    return fetch(cfg.serverUrl + path, Object.assign({}, options, { headers: headers })).then(
      function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp;
      }
    );
  });
}

// Context Menu
chrome.runtime.onInstalled.addListener(function () {
  chrome.contextMenus.create({
    id: "ask-alphabetty",
    title: "Ask Alphabetty about '%s'",
    contexts: ["selection"],
  });
});

chrome.contextMenus.onClicked.addListener(function (info, tab) {
  if (info.menuItemId === "ask-alphabetty") {
    chrome.sidePanel.open({ tabId: tab.id });
    setTimeout(function () {
      chrome.runtime.sendMessage({ type: "openSidebar", query: info.selectionText });
    }, 500);
  }
});

// Health Check
chrome.alarms.create("healthCheck", { periodInMinutes: 1 });

chrome.alarms.onAlarm.addListener(function (alarm) {
  if (alarm.name === "healthCheck") checkHealth();
});

function checkHealth() {
  _getConfig().then(function (cfg) {
    if (!cfg.serverUrl || !cfg.apiKey) {
      _setConnectionState(CONNECTION_STATES.DISCONNECTED);
      return;
    }
    return _apiFetch("/api/v1/ext/health")
      .then(function (resp) { return resp.json(); })
      .then(function (data) {
        _setConnectionState(
          data.status === "ok" ? CONNECTION_STATES.CONNECTED : CONNECTION_STATES.AUTH_FAILED
        );
        // Version check — notify if server updated
        if (data.version) {
          chrome.storage.local.get("lastServerVersion", function (stored) {
            var last = stored.lastServerVersion;
            if (last && last !== data.version) {
              chrome.notifications.create("version-update", {
                type: "basic",
                iconUrl: "icons/icon128.png",
                title: "Alphabetty Updated",
                message: "Server updated to v" + data.version + ". Reload the extension for best experience.",
              });
            }
            chrome.storage.local.set({ lastServerVersion: data.version });
          });
        }
      });
  }).catch(function (e) {
    _setConnectionState(
      String(e.message).indexOf("401") >= 0
        ? CONNECTION_STATES.AUTH_FAILED
        : CONNECTION_STATES.DISCONNECTED
    );
  });
}

function _setConnectionState(state) {
  chrome.storage.local.set({ connectionState: state });
  var badges = {};
  badges[CONNECTION_STATES.CONNECTED] = { text: "", color: "#22c55e" };
  badges[CONNECTION_STATES.DISCONNECTED] = { text: "!", color: "#6b7280" };
  badges[CONNECTION_STATES.AUTH_FAILED] = { text: "!", color: "#ef4444" };
  var b = badges[state] || badges[CONNECTION_STATES.DISCONNECTED];
  chrome.action.setBadgeText({ text: b.text });
  chrome.action.setBadgeBackgroundColor({ color: b.color });
}

// Handshake function injected into page's main world (bypasses CSP)
function _handshakeFn() {
  return fetch("/api/v1/ext/handshake")
    .then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    })
    .then(function (data) {
      return { apiKey: data.apiKey, user: data.user };
    })
    .catch(function () { return null; });
}

// Evaluate function injected into page's main world
function _evalFn(expression) {
  try {
    var r = eval(expression);
    return { result: r };
  } catch (e) {
    return { error: e.message };
  }
}

// Message Routing
chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
  if (message.type === "getPageContext") {
    chrome.scripting.executeScript(
      { target: { tabId: sender.tab ? sender.tab.id : _getActiveTabId() }, files: ["content.js"] },
      function () {
        // re-query since we don't have tab id in this context
      }
    );
    // Use executeScript to get page context directly
    chrome.tabs.query({ active: true, currentWindow: true }, function (tabs) {
      var tab = tabs[0];
      if (!tab || !tab.id) { sendResponse({ error: "No active tab" }); return; }
      chrome.scripting.executeScript(
        { target: { tabId: tab.id }, files: ["content.js"] },
        function () {
          chrome.tabs.sendMessage(tab.id, { type: "getPageContext" }, function (resp) {
            sendResponse(resp || { error: "No response" });
          });
        }
      );
    });
    return true;
  }

  // Auto-connect: inject handshake into page's main world (bypasses CSP)
  if (message.type === "autoConnect") {
    chrome.tabs.query({ active: true, currentWindow: true }, function (tabs) {
      var tab = tabs[0];
      if (!tab || !tab.id || !tab.url || tab.url.indexOf("http") !== 0) {
        sendResponse(null);
        return;
      }
      chrome.scripting.executeScript({
        target: { tabId: tab.id },
        world: "MAIN",
        func: _handshakeFn,
      }, function (results) {
        if (chrome.runtime.lastError) { sendResponse(null); return; }
        var resp = results && results[0] && results[0].result;
        if (resp && resp.apiKey) {
          var serverUrl = new URL(tab.url).origin;
          chrome.storage.local.set({
            serverUrl: serverUrl,
            apiKey: resp.apiKey,
            connectionState: CONNECTION_STATES.CONNECTED,
          }, function () {
            sendResponse({ serverUrl: serverUrl, apiKey: resp.apiKey });
          });
        } else {
          sendResponse(null);
        }
      });
    });
    return true;
  }

  // Command execution
  if (message.type === "executeCommand") {
    chrome.tabs.query({ active: true, currentWindow: true }, function (tabs) {
      var tab = tabs[0];
      if (!tab || !tab.id) { sendResponse({ error: "No active tab" }); return; }

      var cmd = message.command;

      // Evaluate runs in main world (page JS context)
      if (cmd.action === "evaluate") {
        chrome.scripting.executeScript({
          target: { tabId: tab.id },
          world: "MAIN",
          func: _evalFn,
          args: [cmd.params.expression],
        }, function (results) {
          if (chrome.runtime.lastError) {
            sendResponse({ error: chrome.runtime.lastError.message });
          } else {
            sendResponse((results && results[0] && results[0].result) || { error: "No result" });
          }
        });
        return;
      }

      // DOM actions run in content script (isolated world)
      chrome.scripting.executeScript(
        { target: { tabId: tab.id }, files: ["content.js"] },
        function () {
          chrome.tabs.sendMessage(
            tab.id,
            { type: "executeCommand", command: cmd },
            function (resp) {
              sendResponse(resp || { error: "No response" });
            }
          );
        }
      );
    });
    return true;
  }

  if (message.type === "healthCheck") {
    checkHealth();
    return false;
  }
});

// Side panel on action click
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });

// Initial health check
checkHealth();
