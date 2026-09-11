/**
 * TicketEntryRows.atEdge.test.js
 * ──────────────────────────────
 * Left and right move the caret through a cell's text, and only leave the cell
 * once there is no text left to move through.
 *
 * WHY THIS FILE EXISTS
 * These two keys have a job before the grid gets them, since they are how a caret is
 * moved, so taking them unconditionally would make a typo in the middle of a
 * 90-character link unfixable without the mouse. Taking them NEVER is what the
 * band did, which is the complaint this answers. The edge test is what separates
 * the two, and it has to hold for a number and a month box as well, where asking
 * for the caret at all throws rather than answering.
 */
import { atEdge } from './TicketEntryRows';

/** Anything with the two properties an input answers with. */
const caret = (value, from, to = from) => ({ value, selectionStart: from, selectionEnd: to });

/** A number or month box, where reading the caret throws the way Chrome's does. */
const noCaret = (value) => ({
  value,
  get selectionStart() { throw new Error('does not support selection'); },
  get selectionEnd() { throw new Error('does not support selection'); },
});

test('mid-value, neither direction leaves the cell', () => {
  const el = caret('https://example.com', 8);
  expect(atEdge(el, true)).toBe(false);
  expect(atEdge(el, false)).toBe(false);
});

test('at the start, left leaves and right does not', () => {
  const el = caret('Informa', 0);
  expect(atEdge(el, true)).toBe(true);
  expect(atEdge(el, false)).toBe(false);
});

test('at the end, right leaves and left does not', () => {
  const el = caret('Informa', 7);
  expect(atEdge(el, false)).toBe(true);
  expect(atEdge(el, true)).toBe(false);
});

test('an empty cell is at both edges, so either key moves on', () => {
  const el = caret('', 0);
  expect(atEdge(el, true)).toBe(true);
  expect(atEdge(el, false)).toBe(true);
});

test('a selection is never at an edge; the arrow collapses it first', () => {
  const el = caret('Informa', 0, 7);
  expect(atEdge(el, true)).toBe(false);
  expect(atEdge(el, false)).toBe(false);
});

test('a box with no caret to protect counts as the edge', () => {
  expect(atEdge(noCaret('2026-09'), true)).toBe(true);
  expect(atEdge(noCaret('250'), false)).toBe(true);
  expect(atEdge({ value: '250', selectionStart: null, selectionEnd: null }, false)).toBe(true);
});
