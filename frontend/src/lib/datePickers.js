/**
 * Native date boxes, opened from anywhere in the field.  [date_picker_open]
 *
 * A bare <input type="date"> only drops its calendar when the small glyph at
 * the end of the box is clicked. Clicking the text, or arriving on the field
 * from the keyboard, leaves the reader typing digits into something that looks
 * like a picker and behaves like a text box. showPicker() is the platform's own
 * way to open it, so ONE delegated pair of listeners does this for every date,
 * month and time box in the app; the ticket entry band, the ticket form, the
 * paper review, events, credit control, the bulk-update modal, rather than
 * each form remembering to wire it up.
 *
 * Registered on the document at import, once, for the life of the page; nothing
 * to clean up and nothing for a component to forget.
 *
 * showPicker() throws without a transient user activation and on a browser that
 * has not implemented it. Both mean "no picker", which is exactly what the field
 * did before, so the throw is swallowed rather than reported.
 */
const PICKABLE = new Set(['date', 'month', 'week', 'time', 'datetime-local']);

function openPicker(e) {
  const el = e.target;
  if (!el || el.tagName !== 'INPUT' || !PICKABLE.has(el.type)) return;
  if (el.disabled || el.readOnly || typeof el.showPicker !== 'function') return;
  try {
    el.showPicker();
  } catch {
    /* no user activation, or a browser without it */
  }
}

document.addEventListener('click', openPicker);
// focusin, not focus, because focus does not bubble and this listens for every field in
// the app at once. It is also the half a click handler misses, arriving on the
// cell with Tab or an arrow key, which is how the entry band is filled in.
document.addEventListener('focusin', openPicker);
