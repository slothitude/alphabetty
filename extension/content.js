// Content script - page context extraction and DOM command execution.
// Injected by background.js via chrome.scripting.executeScript().
// No inline <script> injection - handshake and evaluate are handled by
// background.js using world:MAIN which bypasses CSP.

(function () {
  if (window.__alphabettyContentLoaded) return;
  window.__alphabettyContentLoaded = true;

  chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
    if (message.type === "getPageContext") {
      sendResponse(getPageContext());
      return false;
    }

    if (message.type === "executeCommand") {
      var result = executeCommand(message.command);
      sendResponse(result);
      return false;
    }
  });

  function getPageContext() {
    var meta = document.querySelector('meta[name="description"]');
    return {
      url: location.href,
      title: document.title || "",
      metaDescription: meta ? meta.content : "",
      headings: Array.prototype.slice.call(document.querySelectorAll("h1, h2, h3"), 0, 10)
        .map(function (el) { return el.textContent.trim(); })
        .filter(Boolean),
      selectedText: window.getSelection ? window.getSelection().toString() : "",
    };
  }

  function executeCommand(command) {
    var action = command.action;
    var params = command.params || {};
    try {
      switch (action) {
        case "click": return clickElement(params.selector);
        case "type": return typeText(params.selector, params.text);
        case "navigate": return navigateTo(params.url);
        case "getDOM": return getDOM(params.depth || 3);
        case "getText": return getTextContent();
        default: return { error: "Unknown action: " + action };
      }
    } catch (e) {
      return { error: e.message };
    }
  }

  function clickElement(selector) {
    var el = document.querySelector(selector);
    if (!el) return { error: "Element not found", selector: selector };
    el.scrollIntoView({ block: "center", behavior: "instant" });
    el.click();
    return { success: true };
  }

  function typeText(selector, text) {
    var el = document.querySelector(selector);
    if (!el) return { error: "Element not found", selector: selector };
    el.focus();
    if (el.isContentEditable) {
      document.execCommand("selectAll", false, null);
      document.execCommand("insertText", false, text);
    } else {
      el.value = text;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    }
    return { success: true };
  }

  function navigateTo(url) {
    window.location.href = url;
    return { success: true };
  }

  function getDOM(depth) {
    function serialize(el, d) {
      if (d <= 0 || !el) return el ? el.tagName : "";
      var tag = el.tagName;
      if (!tag) return "";
      var obj = { tag: tag };
      if (el.id) obj.id = el.id;
      if (el.className && typeof el.className === "string") obj.classes = el.className;
      var text = el.childNodes.length === 1 && el.childNodes[0].nodeType === 3
        ? el.childNodes[0].textContent.trim().slice(0, 200) : "";
      if (text) obj.text = text;
      obj.children = Array.prototype.slice.call(el.children, 0, 30)
        .map(function (c) { return serialize(c, d - 1); });
      return obj;
    }
    return { result: serialize(document.body, depth) };
  }

  function getTextContent() {
    return { result: (document.body.innerText || "").slice(0, 50000) };
  }
})();
