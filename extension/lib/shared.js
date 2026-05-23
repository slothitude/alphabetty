/** Shared constants and message types for the Alphabetty extension. */

export const MSG_TYPES = {
  GET_PAGE_CONTEXT: "getPageContext",
  PAGE_CONTEXT: "pageContext",
  CHAT_REQUEST: "chatRequest",
  CHAT_TOKEN: "chatToken",
  CHAT_DONE: "chatDone",
  CHAT_ERROR: "chatError",
  HEALTH_CHECK: "healthCheck",
  HEALTH_RESULT: "healthResult",
  CONNECTION_STATE: "connectionState",
  OPEN_SIDEBAR: "openSidebar",
};

export const CONNECTION_STATES = {
  CONNECTED: "connected",
  DISCONNECTED: "disconnected",
  AUTH_FAILED: "auth_failed",
};

export const STORAGE_KEYS = {
  SERVER_URL: "serverUrl",
  API_KEY: "apiKey",
  CONNECTION_STATE: "connectionState",
};

export const MODES = ["concise", "detailed", "creative", "academic", "code"];
