// Real-backend entry point. Every src/api/*.js resource module is written
// against this client, which talks to the LINQ CRM Django/DRF backend
// (see backend/config/urls.py — everything lives under /api/).
import axios from 'axios';
import { emitDataChanged, normalisePath } from '../lib/liveData';

/**
 * Read env through `process.env.<NAME>` member access, one variable at a time —
 * never via an intermediate `const ENV = process.env`.
 *
 * react-scripts substitutes these at build time with webpack's DefinePlugin,
 * which matches the literal text `process.env.REACT_APP_FOO`. Aliasing the
 * object first defeats that match, and the browser has no real `process`, so
 * the alias would be `undefined` at runtime and every value would silently fall
 * back to its default.
 *
 * Written this way it also works unchanged under plain Node, where
 * `process.env` is real — which is what lets backend/accounts/wire_probe.mjs
 * import THIS module rather than a rewritten copy of it.
 *
 * NODE_ENV is used for the dev-only flood check rather than a DEV flag:
 * react-scripts sets it to 'development'/'production', and under bare Node it
 * is undefined, so the probe keeps the detector off.
 */
const BASE_URL = process.env.REACT_APP_API_BASE_URL || '/api/';
const IS_DEV = process.env.NODE_ENV === 'development';

/**
 * Development-only request-flood detector.
 *
 * A render loop, or a fetchAllPages walk over a table large enough to be a
 * mistake, both present the same way from the outside: "the backend seems busy".
 * Neither fails a test suite. This names the offending URL on the console the
 * moment one resource is hit implausibly often in a short window, so the next
 * occurrence is diagnosed in seconds instead of by reading a Django log.
 *
 * The threshold is deliberately above what legitimate paging costs (a handful of
 * pages) and well below what a runaway costs (dozens to hundreds). It counts by
 * PATH, ignoring query params, so a page-walk and a repeated identical request
 * both accumulate.
 */
const FLOOD_WINDOW_MS = 10000;
const FLOOD_THRESHOLD = 40;
const floodHits = new Map();      // path -> number[] (timestamps)
const floodWarned = new Set();

function recordForFloodCheck(url) {
  if (!IS_DEV || !url) return;
  const path = String(url).split('?')[0];
  const now = Date.now();
  const hits = (floodHits.get(path) || []).filter((t) => now - t < FLOOD_WINDOW_MS);
  hits.push(now);
  floodHits.set(path, hits);
  if (hits.length >= FLOOD_THRESHOLD && !floodWarned.has(path)) {
    floodWarned.add(path);
    // console.error, not warn: this is a defect, and it should be impossible to
    // scroll past. Repeated once per path so the console stays readable.
    console.error(
      `[request flood] ${hits.length} requests to "${path}" in under ` +
      `${FLOOD_WINDOW_MS / 1000}s.\n` +
      'This is either a render loop (an unstable useEffect/useMemo dependency ' +
      'refiring a fetch) or a fetchAllPages walk over a table too large for it. ' +
      'Use DataTable\'s `server` prop for large tables, or a count endpoint if ' +
      'you only need a number — see api/bookings.js count().',
    );
  }
}

/** Test seam: lets a harness assert the detector fired without a real flood. */
export function __floodState() {
  return { threshold: FLOOD_THRESHOLD, windowMs: FLOOD_WINDOW_MS, warned: [...floodWarned] };
}

/**
 * Serialise params so array values become repeated bare keys:
 *   { payment_status: ["Paid", "Cancelled"] } -> payment_status=Paid&payment_status=Cancelled
 *
 * django-filter's MultipleChoiceFilter reads these with QueryDict.getlist(). Axios' default
 * array format is `payment_status[]=Paid`, which the backend does not recognise — and it
 * ignores the unknown key silently rather than erroring, so the request comes back
 * unfiltered. Empty strings and empty arrays are dropped so "no filter" sends nothing.
 */
export function serializeParams(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params || {})) {
    if (value === undefined || value === null || value === '') continue;
    if (Array.isArray(value)) {
      value.forEach((v) => {
        if (v !== undefined && v !== null && v !== '') search.append(key, v);
      });
      continue;
    }
    search.append(key, value);
  }
  return search.toString();
}

/**
 * The first readable line out of a DRF error body, whatever shape it arrived in.
 *
 * THE BUG THIS FIXES
 * The Bookings modal used to GUESS at the reason for a 400:
 *
 *     err.response?.data?.invoice_number
 *       ? 'That invoice number already exists'
 *       : 'Could not create booking — check the form and try again'
 *
 * The server had already said exactly what was wrong. A delegate email with no
 * "@" comes back as {"delegates": ["Delegate #1 has an invalid email."]} — the
 * row AND the field, named — and the user was shown "check the form and try
 * again", which names neither. The booking looked impossible to save.
 *
 * Four shapes turn up on this backend and all four are handled:
 *   {"detail": "…"}              APIException, permission denial
 *   {"field": ["…", "…"], …}     serializer field errors
 *   {"non_field_errors": ["…"]}  serializer-level validate()
 *   ["…"] or "…"                 raise ValidationError("…") with a bare value
 */
const NAMES_ITSELF = new Set(['detail', 'delegates', 'non_field_errors', 'ids']);

// Django builds its uniqueness message from the MODEL's verbose name — "book
// event with this invoice number already exists." — naming an internal table
// nobody using the CRM has heard of. The field is named by the prefix already.
const UNIQUE_MESSAGE = /^.+ with this .+ already exists\.?$/i;

/** invoice_number -> "Invoice number". */
function humanizeField(key) {
  const words = String(key).replace(/_/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Unwrap DRF's nested lists down to the first message it actually holds. */
function firstMessage(value) {
  if (Array.isArray(value)) return value.length ? firstMessage(value[0]) : null;
  if (value === null || value === undefined || value === '') return null;
  // A per-item error map ({"0": {"email": [...]}}) — descend rather than render
  // it as "[object Object]".
  if (typeof value === 'object') {
    const nested = Object.values(value).map(firstMessage).find(Boolean);
    return nested || null;
  }
  return String(value);
}

export function apiErrorMessage(err, fallback = 'Something went wrong.') {
  const data = err?.response?.data;
  if (data === undefined || data === null) return err?.message || fallback;
  if (typeof data === 'string') return data.trim() || fallback;
  if (Array.isArray(data)) return firstMessage(data) || fallback;
  if (typeof data !== 'object') return fallback;

  // `detail` wins when it is present: DRF uses it for the whole-request reason
  // (permission, throttle, parse error), which outranks any field.
  if (firstMessage(data.detail)) return firstMessage(data.detail);

  const entry = Object.entries(data).find(([, v]) => firstMessage(v));
  if (!entry) return fallback;
  const [key, raw] = entry;
  const message = firstMessage(raw);

  if (NAMES_ITSELF.has(key)) return message;
  if (UNIQUE_MESSAGE.test(message)) return `${humanizeField(key)} already exists.`;
  return `${humanizeField(key)}: ${message}`;
}

/**
 * Fail loudly when an ID collection is not a real Array.
 *
 * Two bugs have already shipped at this seam and both were invisible to green
 * test suites: a Set serialises to {} through JSON.stringify (it has no
 * enumerable own properties), so the backend saw {"ids": {}} and answered
 * "ids list required"; and a pre-encoded spec string got encoded twice. Silent
 * coercion is the enemy here — a wrong type must throw at the call site with
 * the actual type named, not travel to the server as an empty object.
 */
export function assertIdArray(ids, method) {
  if (!Array.isArray(ids)) {
    throw new Error(
      `${method}: ids must be an Array, got ${Object.prototype.toString.call(ids)}`,
    );
  }
}

export const http = axios.create({
  baseURL: BASE_URL,
  headers: { 'Content-Type': 'application/json' },
  paramsSerializer: { serialize: serializeParams },
});

function safeStorageGet(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeStorageRemove(key) {
  try {
    localStorage.removeItem(key);
  } catch {}
}

// Attach token on every request. Backend uses DRF TokenAuthentication
// ("Authorization: Token <token>"), not Bearer/JWT.
http.interceptors.request.use((config) => {
  const token = safeStorageGet('auth_token');
  if (token) {
    config.headers.Authorization = `Token ${token}`;
  }
  recordForFloodCheck(config.url);
  return config;
});

// Mark the moment a token is stored so the interceptor can suppress
// spurious 401s that fire in the first few seconds after login.
export function markTokenFreshness() {
  try { localStorage.setItem('auth_token_set_at', String(Date.now())); } catch {}
}

function tokenIsFreshlySet() {
  try {
    const t = localStorage.getItem('auth_token_set_at');
    return t && (Date.now() - parseInt(t, 10)) < 5000;
  } catch { return false; }
}

/**
 * POSTs that write nothing.
 *
 * Both import wizards and the bulk-update modal PREVIEW through POST out of
 * necessity — the body is a mapped spreadsheet or a list of ids, far past what a
 * query string holds — but the server's answer is a plan, not a change. Treating
 * those as writes would refetch every open table on each step of a preview the
 * user has not committed yet, which is pure noise and, on the bookings table,
 * an expensive one.
 */
const NON_WRITING_POSTS = /(^|\/)(filter_schema|bulk_update_schema|list-worksheets|preview)$/;

/** commit=true is the bulk update that actually lands; commit=false is a plan. */
function isBulkCommit(config) {
  try {
    const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
    return !!(body && body.commit);
  } catch {
    // Unreadable body: assume it wrote, because a missed refresh is the bug being
    // fixed here and a redundant one costs a single request.
    return true;
  }
}

/**
 * Publish every successful write so open pages can refresh themselves.
 *
 * Here rather than in the ~40 resource-module functions that perform writes: an
 * endpoint added later is live without anyone remembering to wire it, and there
 * is exactly one place to read to know what counts as a change. See
 * lib/liveData.js for what listens.
 */
function announceWrite(config) {
  if (!config) return;
  const method = String(config.method || 'get').toLowerCase();
  if (method === 'get' || method === 'head' || method === 'options') return;

  const path = normalisePath(config.url);
  if (!path) return;
  // Signing in is not a data change, and it happens while nothing is mounted.
  if (path.startsWith('auth/')) return;
  if (NON_WRITING_POSTS.test(path)) return;
  if (/(^|\/)bulk_update$/.test(path) && !isBulkCommit(config)) return;

  emitDataChanged(path);
}

// Global error handling — retry on network/503, redirect on 401 (unless token was just set)
http.interceptors.response.use(
  (res) => {
    announceWrite(res.config);
    return res;
  },
  async (err) => {
    const config = err.config;
    const status = err.response?.status;
    const isBackendDown = !err.response || status === 503;

    // Retry up to 2 times (1.2s apart) when Django is restarting
    if (isBackendDown && config && !config._retried) {
      config._retried = true;
      for (let i = 0; i < 2; i++) {
        await new Promise((r) => setTimeout(r, 1200));
        try { return await http(config); } catch (_) {}
      }
    }

    if (status === 401) {
      // Suppress redirect if the token was just stored (login race window)
      if (tokenIsFreshlySet()) {
        return Promise.reject(err);
      }
      safeStorageRemove('auth_token');
      safeStorageRemove('auth_user');
      safeStorageRemove('auth_perms');
      window.location.replace('/login');
    }

    return Promise.reject(err);
  }
);

// Small helper retained for any not-yet-migrated mock resource functions.
export const delay = (value, ms = 120) => new Promise((resolve) => setTimeout(() => resolve(value), ms));

/**
 * Every list endpoint on this backend is wrapped in DRF's PageNumberPagination
 * (page_size=50 by default, page_size_query_param="page_size", max 500 — see
 * config/pagination.py). A plain `http.get(url).then(r => r.data.results)`
 * silently returns only the first page — on tables with thousands of rows
 * (delegates, tickets, ...) that reads as "there's only ~50 records" with no
 * indication more exist. This follows `next` until exhausted so callers get
 * the complete set. Endpoints that aren't paginated (bare array response)
 * are returned as-is.
 */
export async function fetchAllPages(url, params = {}) {
  const pageSize = 500;
  const all = [];
  let page = 1;
  while (true) {
    const { data } = await http.get(url, { params: { ...params, page, page_size: pageSize } });
    if (Array.isArray(data)) return data;
    all.push(...(data.results || []));
    if (!data.next) break;
    page += 1;
  }
  return all;
}

/**
 * One page of a list endpoint, filtered and ordered by the server.
 *
 * The counterpart to fetchAllPages: where that walks every page so a caller
 * gets the complete set, this fetches exactly the page asked for and lets the
 * backend do the filtering. Large tables (delegates is ~35k rows) must use this
 * one — fetchAllPages pulls all 35k into the browser before any filter runs.
 *
 * `filterSpec` is RAW JSON, not percent-encoded: serializeParams runs it through
 * URLSearchParams, which encodes exactly once. Encoding it here as well is the
 * double-encoding bug that already shipped — Django decodes once, sees the
 * literal text "%7B%22match%22…" and answers 400 "filter_spec is not valid JSON".
 */
export async function fetchPage(url, { page = 1, pageSize = 50, ordering, filterSpec, search, params } = {}) {
  const query = { ...(params || {}), page, page_size: pageSize };
  if (ordering) query.ordering = ordering;
  if (filterSpec) query.filter_spec = filterSpec;
  if (search) query.search = search;

  const { data } = await http.get(url, { params: query });
  // Endpoints that opt out of pagination answer with a bare array.
  if (Array.isArray(data)) {
    return { results: data, count: data.length, totalPages: 1, page: 1, paginated: false };
  }
  return {
    results: data.results || [],
    count: data.count ?? (data.results || []).length,
    totalPages: data.total_pages ?? 1,
    page: data.page ?? page,
    paginated: true,
  };
}

/**
 * GET {resource}/ids/ — every id the CURRENT filter matches, for select-all.
 *
 * The counterpart to fetchPage: same query, same server-side filtering, but the
 * whole matching set as bare ids instead of one page of rows. The header
 * checkbox used to tick only the rows on screen, so "select all" on a filter
 * matching 35,690 tickets selected 50.
 *
 * Params are built exactly as fetchPage builds them, and deliberately so — the
 * two must resolve the same rows or select-all would cover a different set than
 * the table displays. `filterSpec` is RAW JSON for the same reason it is there:
 * serializeParams encodes exactly once, and encoding here as well is the
 * double-encoding bug that already shipped.
 *
 * Resolves { ids, count, max }. Rejects with the server's 400 when the filter
 * matches more than the backend's select_all_max — that refusal is deliberate,
 * since a silently truncated select-all looks identical to a complete one.
 */
export async function fetchAllIds(resource, { filterSpec, search, params } = {}) {
  const query = { ...(params || {}) };
  if (filterSpec) query.filter_spec = filterSpec;
  if (search) query.search = search;

  const { data } = await http.get(`${resource}/ids/`, { params: query });
  const ids = Array.isArray(data) ? data : (data.ids || []);
  return { ids, count: data.count ?? ids.length, max: data.max ?? null };
}

/**
 * Split `items` into arrays of at most `size`.
 *
 * Every bulk endpoint on this backend caps at 1000 ids per request
 * (accounts/bulk_update.py, and the bulk_delete actions on delegates and
 * tickets). Select-all routinely produces more than that, so the callers that
 * post id collections batch through here rather than sending one oversized body
 * and getting a 400 the user reads as "the update did not work".
 */
export function chunk(items, size) {
  if (!(size > 0)) throw new Error(`chunk: size must be positive, got ${size}`);
  const out = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

/**
 * `items.map(fn)` with at most `limit` in flight, awaited in order.
 *
 * For the bulk helpers that have no batch endpoint and must issue one request
 * PER id (bookings.bulkMarkPaid, tickets.bulkSubmit). Those were written as
 * `Promise.all(ids.map(...))`, which was survivable while a selection could
 * only ever hold one page: 50 parallel requests. Against a select-all it is
 * 35,690 of them opened at once, which exhausts the browser's connection pool,
 * buries the API under a self-inflicted burst, and reports as the app hanging.
 *
 * Order of results matches order of input regardless of completion order.
 */
export async function mapLimit(items, limit, fn) {
  const results = new Array(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const i = next++;
      results[i] = await fn(items[i], i);
    }
  };
  await Promise.all(
    Array.from({ length: Math.max(1, Math.min(limit, items.length)) }, worker),
  );
  return results;
}

/**
 * GET {resource}/filter_schema/ — the server's registry of filterable fields
 * and the operators allowed on each. Fetched rather than hardcoded so the
 * frontend cannot drift from backend/accounts/filter_spec.py.
 */
export function fetchFilterSchema(resource) {
  return http.get(`${resource}/filter_schema/`).then((r) => r.data);
}

/** GET {resource}/bulk_update_schema/ — what may be mass-edited, and its max. */
export function fetchBulkUpdateSchema(resource) {
  return http.get(`${resource}/bulk_update_schema/`).then((r) => r.data);
}

/**
 * POST {resource}/bulk_update/ — preview (commit=false) or apply (commit=true).
 *
 * Two shapes matter here and both have bitten before:
 *
 *   ids   MUST be a real Array. A Set serialises to {} through JSON.stringify,
 *         so the backend saw {"ids": {}} and answered "ids list required".
 *         assertIdArray throws at the call site with the actual type named.
 *
 *   value KEY PRESENCE is the signal, not truthiness. Omitting it means "no
 *         target chosen yet" (a distribution-only preview); sending it as null
 *         means "clear this field", which is a real operation on a nullable
 *         column. Passing `undefined` here omits the key; passing null sends it.
 */
export function bulkUpdate(resource, { ids, field, value, commit = false, planHash }) {
  assertIdArray(ids, `bulkUpdate(${resource})`);
  const body = { ids, field, commit };
  if (value !== undefined) body.value = value;
  if (planHash) body.plan_hash = planHash;
  return http.post(`${resource}/bulk_update/`, body).then((r) => r.data);
}

export default http;