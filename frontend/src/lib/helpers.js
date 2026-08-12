// Ported 1:1 from legacy-vanilla-js/js/01-data.js (helpers + seeded RNG section).
export const nf = (n) => (n == null ? '—' : Number(n).toLocaleString('en-US'));
export const pc = (a, b) => (b ? Math.round((a / b) * 100) : 0);
export const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function fdate(d) {
  if (!d) return '—';
  const x = new Date(d);
  if (isNaN(x)) return '—';
  return String(x.getDate()).padStart(2, '0') + ' ' + MON[x.getMonth()] + ' ' + x.getFullYear();
}
export function fmy(d) {
  if (!d) return '—';
  const x = new Date(d);
  if (isNaN(x)) return '—';
  return MON[x.getMonth()] + ' ' + x.getFullYear();
}
export function ftime(d) {
  const x = new Date(d);
  if (isNaN(x)) return '—';
  return String(x.getHours()).padStart(2, '0') + ':' + String(x.getMinutes()).padStart(2, '0');
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
