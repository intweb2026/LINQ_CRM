/**
 * The invalidation bus behind live updates.
 *
 * WHAT WAS WRONG
 * Every page fetched on mount and then never again. Three separate symptoms came
 * out of that one fact, and all three read to the user as "the record did not
 * save":
 *
 *   1. A write made ELSEWHERE never arrived. A booking that came in by webhook, a
 *      ticket a colleague moved, a row the Google Sheets sync wrote, the CRM open
 *      in a second tab — none of it appeared until F5.
 *   2. A write made HERE only appeared because the page happened to call its own
 *      refresh afterwards. Any path that forgot to (the import wizard, the
 *      dashboard's quick actions) left the screen showing the old data.
 *   3. A write made on ANOTHER page of the same app left this one stale for as
 *      long as it stayed mounted.
 *
 * HOW THIS FIXES IT
 * Every successful non-GET request publishes the path it wrote to. The emit lives
 * in the response interceptor in api/client.js, ONE place, rather than at the ~40
 * mutation call sites — so an endpoint added tomorrow is live the day it is added
 * and cannot be forgotten. Anything rendering a resource subscribes through
 * hooks/useLiveData.js and refetches when its resource is touched.
 *
 * Cross-tab delivery rides on BroadcastChannel. Where that is unavailable the
 * localStorage `storage` event carries the same message: it fires only in OTHER
 * tabs, which is exactly the half a fallback is needed for, the local half being
 * delivered in-process by deliver() either way.
 *
 * WHY NOT WEBSOCKETS
 * They would be the right answer to "another user just changed this" if the stack
 * could serve them. It cannot: the backend runs under gunicorn's WSGI workers
 * (requirements.txt has no channels/daphne/uvicorn), and WSGI has no socket to
 * upgrade. Adding Channels plus a layer backend is a deployment change, not a
 * frontend one. Polling on a visible tab covers the same ground for a CRM this
 * size, and the write-triggered path above is instant regardless — so the poll is
 * only ever the safety net for changes this browser did not make.
 *
 * Everything here is guarded for a non-browser global scope on purpose:
 * api/client.js imports this module, and backend/accounts/wire_probe.mjs imports
 * api/client.js under bare Node.
 */

/**
 * Default background cadence. Deliberately slower than the 15s the webhook log
 * page uses for its explicit "Go live" mode: that is a page someone is actively
 * watching a delivery on, this is every table in the app at rest.
 */
export const LIVE_POLL_MS = 30000;

/** Pass as `resources` to hear about every write, whatever it touched. */
export const ANY_RESOURCE = '*';

const CHANNEL_NAME = 'linq-crm-data';
const STORAGE_KEY = 'linq_crm_data_change';

const listeners = new Set();

function deliver(path) {
  // A throwing listener must not stop the others being told, and must not
  // propagate into the axios interceptor that started the emit.
  listeners.forEach((fn) => {
    try { fn(path); } catch { /* a subscriber's own problem */ }
  });
}

/**
 * Gated on `window`, not merely on BroadcastChannel being defined.
 *
 * Node has had a global BroadcastChannel since v18, and an OPEN channel is a
 * handle that holds the event loop open — so under bare Node this module made the
 * process refuse to exit. backend/accounts/wire_probe.mjs imports api/client.js,
 * which imports this file, and `node wire_probe.mjs` hung forever instead of
 * printing its JSON; tests_wire_probe.py reads a non-zero exit as "probe
 * unavailable" and SKIPS, so the failure mode was a suite that quietly stopped
 * asserting anything about the frontend. Cross-tab messaging is meaningless
 * without tabs; requiring a window says that, and fixes it.
 */
let channel = null;
if (typeof window !== 'undefined' && typeof BroadcastChannel !== 'undefined') {
  try {
    channel = new BroadcastChannel(CHANNEL_NAME);
    channel.onmessage = (ev) => {
      const path = ev && ev.data && ev.data.path;
      if (path) deliver(path);
    };
  } catch {
    channel = null;
  }
}

// Only wired when BroadcastChannel is missing — with both live, every cross-tab
// write would be delivered twice.
if (!channel && typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
  window.addEventListener('storage', (ev) => {
    if (!ev || ev.key !== STORAGE_KEY || !ev.newValue) return;
    try {
      const msg = JSON.parse(ev.newValue);
      if (msg && msg.path) deliver(msg.path);
    } catch { /* not ours, or truncated */ }
  });
}

/**
 * A request URL reduced to the resource path it addresses.
 *
 *   'delegates/1234/'                    -> 'delegates/1234'
 *   '/api/webhooks/keys/7/regenerate/'   -> 'webhooks/keys/7/regenerate'
 *   'tickets/?page=2'                    -> 'tickets'
 *
 * The query string goes because it says nothing about WHAT was written, and the
 * `/api/` prefix goes because callers write both forms (relative against the
 * axios baseURL, or absolute) and they must compare equal.
 */
export function normalisePath(url) {
  if (!url) return '';
  return String(url)
    .split('?')[0]
    .split('#')[0]
    .replace(/^[a-z]+:\/\/[^/]+/i, '')
    .replace(/^\/*api\//, '')
    .replace(/^\/+|\/+$/g, '');
}

/**
 * Does a written path concern a subscriber watching `resources`?
 *
 * Matching runs in BOTH directions, which is the part that is easy to get wrong.
 * A write to 'delegates/1234' must reach a page watching 'delegates' (the write
 * is deeper than the resource), and a write to 'webhooks/keys' must reach a page
 * watching 'webhooks/keys/7' if one existed (the resource is deeper than the
 * write). One-directional prefix matching silently drops one of those.
 *
 * `resources` of null — or ANY_RESOURCE — means "anything", which is what the
 * dashboard wants: its aggregates move when almost any table does.
 */
export function pathTouches(path, resources) {
  if (!resources || resources === ANY_RESOURCE) return true;
  const p = normalisePath(path);
  if (!p) return false;
  const list = Array.isArray(resources) ? resources : [resources];
  return list.some((raw) => {
    if (raw === ANY_RESOURCE) return true;
    const res = normalisePath(raw);
    if (!res) return false;
    return p === res || p.startsWith(res + '/') || res.startsWith(p + '/');
  });
}

/**
 * Announce that `url` was written to — in this tab and in every other one.
 *
 * Called from api/client.js's response interceptor, not from resource modules.
 */
export function emitDataChanged(url) {
  const path = normalisePath(url);
  if (!path) return;
  deliver(path);
  if (channel) {
    try { channel.postMessage({ path }); } catch { /* channel closed with the page */ }
    return;
  }
  try {
    // `at` is what makes each write a distinct VALUE: the storage event only
    // fires when the stored string actually changes, so two consecutive writes
    // to the same path would otherwise be one event in the other tab.
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ path, at: Date.now() }));
  } catch { /* private mode, or quota */ }
}

/** Subscribe to writes. Returns the unsubscribe function. */
export function subscribeDataChanged(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Test seam: how many subscribers are attached right now. */
export function __listenerCount() {
  return listeners.size;
}
