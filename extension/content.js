/** Content script — extracts page context on demand.
 *  Injected by background.js via chrome.scripting.executeScript(). */

(() => {
  // Avoid double-injection
  if (window.__alphabettyContentLoaded) return;
  window.__alphabettyContentLoaded = true;

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === "getPageContext") {
      const context = getPageContext();
      sendResponse(context);
    }
    return false;
  });

  function getPageContext() {
    const meta = document.querySelector('meta[name="description"]');
    return {
      url: location.href,
      title: document.title || "",
      metaDescription: meta?.content || "",
      headings: Array.from(document.querySelectorAll("h1, h2, h3"))
        .slice(0, 10)
        .map((el) => el.textContent?.trim())
        .filter(Boolean),
      selectedText: window.getSelection()?.toString() || "",
    };
  }
})();
