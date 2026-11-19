// Per-browser UI preferences. Storage may be blocked (private mode), so every access is guarded.
const SIDEBAR_KEY = 'relay.sidebar.collapsed';

export function readSidebarCollapsed(storage = globalThis.localStorage) {
  try { return storage?.getItem(SIDEBAR_KEY) === 'true'; } catch { return false; }
}

export function writeSidebarCollapsed(value, storage = globalThis.localStorage) {
  try { storage?.setItem(SIDEBAR_KEY, value ? 'true' : 'false'); return true; } catch { return false; }
}

// Inbox customer-details panel: shown by default; hiding it gives the chat more room.
const DETAILS_KEY = 'relay.inbox.details.hidden';
export function readDetailsHidden(storage = globalThis.localStorage) {
  try { return storage?.getItem(DETAILS_KEY) === 'true'; } catch { return false; }
}
export function writeDetailsHidden(value, storage = globalThis.localStorage) {
  try { storage?.setItem(DETAILS_KEY, value ? 'true' : 'false'); return true; } catch { return false; }
}
