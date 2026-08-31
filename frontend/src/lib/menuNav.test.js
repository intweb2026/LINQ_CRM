/**
 * lib/menuNav.test.js
 * ───────────────────
 * The arrow-key highlight for the custom dropdown menus.
 *
 * The menus replaced native <select>, and nothing reimplemented the arrow
 * behaviour the OS used to give us, so Up/Down did nothing in Select, in the
 * column filter's value picker and in the inline cell editor. What is pinned
 * here is the part that decides whether Enter does the right thing: where the
 * highlight starts, and that it survives the list shrinking under it.
 */
import { nextNavIdx, NONE } from './menuNav';

test('Down from nothing lands on the first row, Up from nothing on the last', () => {
  expect(nextNavIdx('ArrowDown', NONE, 3)).toBe(0);
  expect(nextNavIdx('ArrowUp', NONE, 3)).toBe(2);
});

test('both ends clamp rather than wrap', () => {
  expect(nextNavIdx('ArrowDown', 2, 3)).toBe(2);
  expect(nextNavIdx('ArrowUp', 0, 3)).toBe(0);
});

test('it steps one row at a time', () => {
  expect(nextNavIdx('ArrowDown', 0, 3)).toBe(1);
  expect(nextNavIdx('ArrowUp', 2, 3)).toBe(1);
});

test('an empty list has nothing to highlight', () => {
  expect(nextNavIdx('ArrowDown', NONE, 0)).toBe(NONE);
  expect(nextNavIdx('ArrowUp', 1, 0)).toBe(NONE);
});

test('other keys leave the highlight alone', () => {
  // Enter and plain typing must not move it — in OptsPicker, Enter with no
  // highlight commits the typed text instead.
  expect(nextNavIdx('Enter', 1, 3)).toBe(1);
  expect(nextNavIdx('a', NONE, 3)).toBe(NONE);
  expect(nextNavIdx('Escape', 2, 3)).toBe(2);
});
