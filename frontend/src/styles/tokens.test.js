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
