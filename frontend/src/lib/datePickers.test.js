/**
 * datePickers.test.js
 * ───────────────────
 * A click or a keyboard arrival in a date box opens the calendar, and nothing
 * else in the app notices the listener is there.
 *
 * WHY THIS FILE EXISTS
 * Two document-level listeners run over EVERY click and every focus change in
 * the CRM, so the predicate deciding what they act on has to be right. A text
 * box, a locked date box or a browser without showPicker must come out
 * untouched, and a showPicker that throws, which it does without a user
 * activation, must not take the click with it.
 */
import './datePickers';

/** An input in the document, so a bubbling event reaches the listener. */
function field(type, attrs = {}) {
  const el = document.createElement('input');
  el.setAttribute('type', type);
  Object.assign(el, attrs);
  el.showPicker = jest.fn();
  document.body.appendChild(el);
  return el;
}

afterEach(() => { document.body.innerHTML = ''; });

const click = (el) => el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
const focus = (el) => el.dispatchEvent(new FocusEvent('focusin', { bubbles: true }));

test('a click anywhere in a date box opens the picker', () => {
  const el = field('date');
  click(el);
  expect(el.showPicker).toHaveBeenCalled();
});

test('arriving on the cell from the keyboard opens it too', () => {
  const el = field('month');
  focus(el);
  expect(el.showPicker).toHaveBeenCalled();
});

test('a plain text box is left alone', () => {
  const el = field('text');
  click(el);
  focus(el);
  expect(el.showPicker).not.toHaveBeenCalled();
});

test('a disabled or read-only date box is left alone', () => {
  const off = field('date', { disabled: true });
  const ro = field('date', { readOnly: true });
  click(off); click(ro);
  expect(off.showPicker).not.toHaveBeenCalled();
  expect(ro.showPicker).not.toHaveBeenCalled();
});

test('no showPicker at all is not an error', () => {
  const el = field('date');
  delete el.showPicker;
  expect(() => click(el)).not.toThrow();
});

test('a showPicker that throws does not take the click with it', () => {
  const el = field('date');
  el.showPicker = jest.fn(() => { throw new Error('NotAllowedError'); });
  expect(() => click(el)).not.toThrow();
  expect(el.showPicker).toHaveBeenCalled();
});
