/**
 * components/Popover.fitX.test.js
 * ───────────────────────────────
 * A popover panel that opens off the side of the screen.
 *
 * The panel is `position: fixed`, so anything past the viewport edge cannot be
 * scrolled back into view; it is the same trap the vertical maxH cap already
 * covered, only sideways. It bit the column-header filters. The funnel on the
 * last columns of a wide table sits hard against the right edge, so the filter
 * opened with its operator select and value box outside the window, and no user
 * could fill it in.
 */
import { fitX } from './Popover';

const VW = 1000;
const W = 240;

test('a trigger in the middle of the screen is left exactly where it asked', () => {
  expect(fitX(300, W, VW)).toBe(300);
});

test('a trigger at the right edge pulls the panel back inside the viewport', () => {
  // Unclamped this would be 960, putting 200px of the panel off screen.
  expect(fitX(960, W, VW)).toBe(VW - W - 8);
  expect(fitX(960, W, VW) + W).toBeLessThanOrEqual(VW);
});

test('the last position that still fits is not disturbed', () => {
  expect(fitX(VW - W - 8, W, VW)).toBe(VW - W - 8);
});

test('a negative offset is pulled back off the near edge', () => {
  expect(fitX(-40, W, VW)).toBe(8);
});

test('a panel wider than the viewport still starts on screen', () => {
  expect(fitX(50, 1200, VW)).toBe(8);
});
