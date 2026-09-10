/**
 * styles/tokens.test.js
 * ─────────────────────
 * Every var(--x) in the CSS has to resolve to a token that base.css defines.
 *
 * WHY THIS IS TESTED
 * An undefined custom property fails SILENTLY. `background:var(--bg-1)` with no
 * --bg-1 anywhere is not an error, it is no background at all — which is how
 * the Pre-Event Docs event picker shipped as a transparent panel with the
 * delegates table showing through it. Nothing in the console, nothing in the
 * build, and on a white page a missing white background looks fine until
 * something is behind it. The whole .ped-* block had been written against a
 * --bg-1/2/3 + --border-2 palette that this app never had.
 *
 * A var() with a fallback — var(--maybe,#fff) — is fine and is skipped.
 */
const fs = require('fs');
const path = require('path');

const dir = path.join(__dirname);
const files = [
  ...fs.readdirSync(dir).filter((f) => f.endsWith('.css')).map((f) => path.join(dir, f)),
  path.join(__dirname, '..', 'index.css'),
];

test('no CSS variable is used without being defined', () => {
  const defined = new Set();
  const used = [];

  for (const file of files) {
    const css = fs.readFileSync(file, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    for (const m of css.matchAll(/(--[a-zA-Z0-9-]+)\s*:/g)) defined.add(m[1]);
    // var(--name) with no comma before the closing paren, i.e. no fallback.
    for (const m of css.matchAll(/var\(\s*(--[a-zA-Z0-9-]+)\s*\)/g)) {
      used.push({ name: m[1], file: path.basename(file) });
    }
  }

  const missing = [...new Set(used.filter((u) => !defined.has(u.name))
    .map((u) => `${u.name} (${u.file})`))];
  expect(missing).toEqual([]);
});

/**
 * Every named theme restates the whole ground-dependent contract.
 *
 * WHY THIS IS A SECOND TEST AND NOT PART OF THE FIRST
 * The test above catches a token that is used and never defined. This one
 * catches the opposite and quieter failure: a token that IS defined — in
 * :root, for a white ground — and that a dark or warm theme forgot to
 * restate. Nothing is undefined, so nothing is silently transparent; instead
 * the value is silently WRONG. A theme missing --n-50 puts a near-white hover
 * on Bastion's deep teal, and --n-50 appears in no palette swatch and no
 * screenshot taken with the pointer away from the rail.
 *
 * THE DARK THEME IS THE REFERENCE, NOT A HARDCODED LIST
 * The required set is read off whatever html[data-theme=dark] redefines,
 * because that block already IS the answer to "what has to change when the
 * ground changes". So adding a token to the dark theme automatically requires
 * it of every named theme, and this test needs no editing to keep up.
 *
 * --bd-* is excluded: the Mining Matrix band palette is per-ground, not
 * per-theme. Bastion takes the dark one by sharing dark's selector, and the
 * three light themes take :root's, which is already mixed for a light ground.
 */
const NAMED = ['bastion', 'provenance', 'atrium', 'folio'];

/** [selector, body] for every rule in base.css, comments stripped. */
function rules() {
  const css = fs.readFileSync(path.join(dir, 'base.css'), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '');
  return [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => [m[1].trim(), m[2]]);
}

/** Tokens a theme defines, across every rule whose selector list names it. */
function tokensFor(theme) {
  const found = new Set();
  for (const [sel, body] of rules()) {
    if (!sel.split(',').some((s) => s.trim() === `html[data-theme=${theme}]`)) continue;
    for (const m of body.matchAll(/(--[a-zA-Z0-9-]+)\s*:/g)) found.add(m[1]);
  }
  return found;
}

test.each(NAMED)('the %s theme restates every ground-dependent token', (theme) => {
  const required = [...tokensFor('dark')].filter((t) => !t.startsWith('--bd-'));
  expect(required.length).toBeGreaterThan(20);   // the reference set was found
  const missing = required.filter((t) => !tokensFor(theme).has(t)).sort();
  expect(missing).toEqual([]);
});

test('the only dark named theme shares the dark band palette', () => {
  // Bastion is the one named theme on a dark ground, so it is the one that
  // must NOT inherit :root's band colours — those are deep tones mixed to sit
  // on white and would be near-black on #04262F.
  const shared = rules().some(([sel, body]) =>
    sel.includes('html[data-theme=bastion]')
    && sel.includes('html[data-theme=dark]')
    && body.includes('--bd-'));
  expect(shared).toBe(true);
});

test('every theme in the picker has a CSS block', () => {
  // A picker entry with no block is a menu item that does nothing.
  const listed = fs.readFileSync(path.join(__dirname, '..', 'lib', 'constants.js'), 'utf8')
    .match(/export const THEMES = \[([\s\S]*?)\n\];/)[1]
    .matchAll(/\{\s*id:\s*'([a-z]+)'/g);
  const inPicker = [...listed].map((m) => m[1]);
  expect(inPicker).toEqual(['light', ...NAMED]);

  const styled = new Set(rules().flatMap(([sel]) =>
    [...sel.matchAll(/html\[data-theme=([a-z]+)\]/g)].map((m) => m[1])));
  // 'light' is :root itself and has no data-theme block, by design.
  expect(inPicker.filter((t) => t !== 'light').filter((t) => !styled.has(t))).toEqual([]);
  // The reverse does NOT hold, deliberately: 'dark' is still styled and is no
  // longer offered. See the note on THEMES in lib/constants.js — Bastion is
  // built on the dark-ground rules, so they stay.
  expect([...styled].filter((t) => !inPicker.includes(t)).sort()).toEqual(['dark']);
});

/**
 * Bastion carries a twin of every dark-ground COMPONENT rule.
 *
 * The per-theme tests above prove Bastion restates the TOKEN contract. These
 * are the rules no token can express — the bulk bar's own background, the
 * inverted tooltip, .btn-p:hover reversing direction, the login wordmark swap
 * — and every one was written as `html[data-theme=dark] .x` back when dark was
 * the only dark theme. Bastion is that theme now, so a rule without a twin is
 * a light-ground style left showing on a dark ground.
 */
test('no dark-ground component rule is missing its Bastion twin', () => {
  const orphans = [];
  for (const f of ['base.css', 'components.css', 'overlays.css']) {
    const css = fs.readFileSync(path.join(dir, f), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '');
    for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const parts = m[1].split(',').map((x) => x.trim());
      for (const sel of parts) {
        if (!sel.includes('html[data-theme=dark]')) continue;
        // The bare token block is the exception: Bastion replaces it wholesale
        // rather than inheriting it, which the per-theme tests already cover.
        if (sel === 'html[data-theme=dark]') continue;
        const twin = sel.replace('=dark]', '=bastion]');
        if (!parts.includes(twin)) orphans.push(`${f}: ${sel}`);
      }
    }
  }
  expect(orphans).toEqual([]);
});
