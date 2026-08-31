/**
 * pages/tickets/TicketFormModal.readOut.test.js
 * ─────────────────────────────────────────────
 * A locked field must show its whole value, not the first line of it.
 *
 * THE BUG THIS PINS, AND WHY IT NEEDS PINNING AT ALL
 * Every Ticket Hub field is locked for Data Mining — that is the point of the
 * serializer guards. Locked used to mean a disabled <input>, and an <input> is a
 * single line that clips silently: no ellipsis, no scrollbar, no error. MR would
 * write "Board page only, skip advisors, the 2024 list is stale" into MR Comments
 * and the miner working the ticket would see "Board page only, skip adv" with no
 * indication there was more. The brief is the entire reason the ticket exists.
 *
 * Nothing about that failure is visible from the outside. The value is stored, the
 * API returns it, the field renders, and the form looks complete. It is caught
 * here or it is not caught.
 *
 * WHAT MUST NOT REGRESS ALONGSIDE IT: a field the viewer MAY write to has to stay
 * a real control. Turning an editable input into text would be a far louder bug,
 * but it is the same branch, so both directions are asserted.
 */
import { readOut } from './TicketFormModal';

const LONG = 'Board page only, skip advisors, and ignore the 2024 speaker list.';
const SHORT = '2026-08-31';

describe('a field the viewer may not write to', () => {
  test('renders as a value, not as an inert box', () => {
    const { ro, v } = readOut({ value: LONG, disabled: true });
    expect(ro).toBe(true);
    expect(v).toBe(LONG);
  });

  test('a value longer than a column takes the whole row, so it can wrap', () => {
    expect(readOut({ value: LONG, disabled: true }).wide).toBe(true);
  });

  test('a short value stays in its column rather than wasting a row', () => {
    expect(readOut({ value: SHORT, disabled: true }).wide).toBe(false);
  });

  test('an empty value still reads as a field, so a blank reads as blank', () => {
    const { ro, v, wide } = readOut({ value: '', disabled: true });
    expect(ro).toBe(true);
    expect(v).toBe('');
    expect(wide).toBe(false);
  });

  test('a number is shown, not swallowed by a falsy test', () => {
    // Estimate and Mined Count offer 0, and 0 is a real answer.
    expect(readOut({ value: 0, disabled: true })).toMatchObject({ ro: true, v: 0 });
  });
});

describe('a field the viewer may write to', () => {
  test('stays a control', () => {
    expect(readOut({ value: LONG, disabled: false }).ro).toBe(false);
  });

  test('stays a control when nothing says otherwise', () => {
    expect(readOut({ value: LONG }).ro).toBe(false);
  });

  test('is never widened by the length of what is typed into it', () => {
    // Otherwise the grid would reflow under the cursor as someone typed.
    expect(readOut({ value: LONG, disabled: false }).wide).toBe(false);
  });
});

describe('the shapes a child can be in', () => {
  test('a control wrapped in a layout div is widened by the caller instead', () => {
    // Link URL: props sit on the input inside .fd-lnk, not on the child element,
    // so Field cannot read them and the call site passes `full`.
    expect(readOut(undefined, true)).toMatchObject({ ro: false, wide: true });
  });

  test('a child with no value is left alone even when disabled', () => {
    expect(readOut({ disabled: true }).ro).toBe(false);
  });
});
