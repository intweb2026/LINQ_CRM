// Ported 1:1 from legacy-vanilla-js/js/01-data.js (helpers + seeded RNG section).
export const nf = (n) => (n == null ? '—' : Number(n).toLocaleString('en-US'));
export const pc = (a, b) => (b ? Math.round((a / b) * 100) : 0);
export const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// ── IST rendering ───────────────────────────────────────────────────────
/**
 * Timestamps are STORED as UTC and that does not change; see settings.TIME_ZONE
 * in backend/config/settings.py. An instant carries no timezone of its own, so
 * the zone belongs to the RENDER, and this is where the render happens.
 *
 * THE BUG THIS FIXES
 * These three read the value back through getDate()/getMonth()/getHours(), which
 * are the VIEWER'S machine timezone rather than the team's. On an IST laptop that
 * looked right by accident. Anywhere else the same row read as a different day,
 * and a plain 'YYYY-MM-DD' was the worst case: '2026-08-21' parses as UTC
 * midnight, so every viewer west of Greenwich saw 20 Aug for a date nobody had
 * disputed. Modified Time on Bookings made it visible, because that column is now
 * also the table's default sort and a timestamp that renders in one zone while it
 * sorts in another has no consistent reading at all.
 *
 * WHY A FIXED OFFSET AND NOT Intl.DateTimeFormat
 * The offset is exact here rather than an approximation: India has run a single
 * +05:30 nationwide with no DST since 1945, so shifting the instant and then
 * reading its UTC fields yields the IST calendar fields for every date this CRM
 * will ever hold. It also keeps ONE convention in the codebase, because
 * lib/dateFilter.js rowDateISO() reckons a row's day by the same shift; a filter
 * that disagreed with the cell beside it by 5h30m every night is exactly the
 * drift this is meant to close.
 */
export const IST_OFFSET_MS = 5.5 * 60 * 60 * 1000;

/** The instant shifted so that its UTC fields read as IST fields, or null. */
function istView(d) {
  if (!d) return null;
  const x = new Date(d);
  if (isNaN(x)) return null;
  return new Date(x.getTime() + IST_OFFSET_MS);
}

export function fdate(d) {
  const x = istView(d);
  if (!x) return '—';
  return String(x.getUTCDate()).padStart(2, '0') + ' ' + MON[x.getUTCMonth()] + ' ' + x.getUTCFullYear();
}
export function fmy(d) {
  const x = istView(d);
  if (!x) return '—';
  return MON[x.getUTCMonth()] + ' ' + x.getUTCFullYear();
}
export function ftime(d) {
  const x = istView(d);
  if (!x) return '—';
  return String(x.getUTCHours()).padStart(2, '0') + ':' + String(x.getUTCMinutes()).padStart(2, '0');
}
export function rel(d) {
  const ms = Date.now() - new Date(d).getTime();
  if (ms < 0) return 'scheduled';
  const h = ms / 36e5;
  if (h < 1) return Math.max(1, Math.round(ms / 6e4)) + 'm ago';
  if (h < 24) return Math.round(h) + 'h ago';
  const dd = Math.round(h / 24);
  return dd < 30 ? dd + 'd ago' : Math.round(dd / 30) + 'mo ago';
}
export function plur(n, a, b) {
  return n + ' ' + (n === 1 ? a : b || a + 's');
}
export function ord(n) {
  const t = n % 100;
  if (t >= 11 && t <= 13) return n + 'th';
  return n + ({ 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] || 'th');
}

// Deterministic hash of a string — used only to pick a stable avatar color
// per name below, not for data generation.
export function hstr(s) {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h >>> 0;
}

export const AV = ['#4F5D75', '#00819D', '#4B57A8', '#7A4E8C', '#137B8C', '#4F7A4A', '#8A6A4F', '#3F6B8C', '#6B5B95', '#5C7A6E', '#8C5F6B', '#456B8C'];
export function avc(n) {
  return AV[hstr(String(n)) % AV.length];
}
export function ini(n) {
  const p = String(n).trim().split(/\s+/);
  return ((p[0] || '')[0] + ((p[p.length - 1] || '')[0] || '')).toUpperCase();
}
export function uniq(a) {
  return Array.from(new Set(a)).filter((v) => v != null && v !== '').sort();
}

// Zoho writes some export columns as HTML, so a value can arrive — and, for
// anything imported before the importer started unwrapping it, can already be
// STORED — as `<a href="https://…" target="_blank">Eli Jasso</a>` rather than as
// the address alone. Put straight in an href that markup is a dead link, and put
// straight in a cell it renders as visible tag soup. The two helpers below read
// the address and the words back out of it, so a row that predates
// accounts/import_common.py:unwrap_anchor still renders as a working link.
const ANCHOR_HREF = /<a\b[^>]*?\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))/i;
const ANY_TAG = /<[^>]*>/g;
const ENTITY = { amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'", apos: "'", nbsp: ' ' };
const unesc = (s) => s.replace(/&(#39|amp|lt|gt|quot|apos|nbsp);/g, (m, k) => ENTITY[k] ?? m);

const stripTags = (s) => unesc(s.replace(ANY_TAG, ' ')).split(/\s+/).filter(Boolean).join(' ');

/**
 * A stored cell as visible text, with anchor markup unwrapped to the words
 * inside it.
 *
 * Narrow on purpose, matching accounts/import_common.py:plain_text_cell — a cell
 * with no anchor in it comes back verbatim rather than through the tag stripper,
 * because "<not stated>" typed into a column is a value, and a blanket stripper
 * would render it as nothing at all.
 */
export function cellText(v) {
  if (v == null) return '';
  const s = String(v);
  return s.includes('<a') ? stripTags(s) || s.trim() : s;
}

/**
 * A stored link turned into something a browser can actually navigate to, or
 * null when the text isn't a link at all.
 *
 * Rendering the raw value in an href is not safe on three counts, all of which
 * this data hits. A value with no scheme ("google.com" — one such row in
 * tickets.link_url today) is a RELATIVE path, so the browser resolves it against
 * the CRM's own origin: clicking it, or "open link in new tab", reloads the CRM
 * instead of going anywhere. A value that is anchor markup makes the whole tag
 * the href, which goes nowhere at all. And these values arrive from imported
 * spreadsheets, so a `javascript:` payload in a cell — or inside the href of one
 * of those anchors — would otherwise be one click from running in the app's
 * origin. Scheme-less text gets https:// only when it plausibly names a host;
 * everything else comes back null and is rendered as plain text.
 */
export function extUrl(v) {
  if (v == null) return null;
  let s = String(v).trim();
  if (s.includes('<a')) {
    const m = ANCHOR_HREF.exec(s);
    // An anchor with no href at all falls through to its own visible text,
    // which is usually the address written out.
    s = (m ? unesc(m[1] ?? m[2] ?? m[3]) : stripTags(s)).trim();
  }
  if (!s || s === '—') return null;
  if (/^[a-z][a-z0-9+.-]*:/i.test(s)) {
    // Absolute already — allow only the schemes a CRM link should ever use.
    return /^(https?:|mailto:|tel:)/i.test(s) ? s : null;
  }
  if (s.startsWith('//')) return 'https:' + s;
  const host = s.split(/[/?#]/)[0];
  // "www.x.com/path" and "x.co.uk" pass; "delete", "N/A" and free-text notes
  // are not links and must not become https://<prose>.
  return /^[\w-]+(\.[\w-]+)+$/.test(host) ? 'https://' + s : null;
}
