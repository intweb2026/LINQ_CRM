import { useEffect, useRef, useState } from 'react';
import { normalizeEditorHtml, renderInto, sanitizeHtml } from '../lib/richText';

/**
 * A form field for a value that is stored as HTML.
 *
 * Replaces the textarea on the agenda copy, which showed the stored markup
 * rather than the formatting it describes; see lib/richText.js for where that
 * markup comes from and what is allowed through. Editing stays in place, because
 * the field is still a field, and the markup itself stays reachable behind the
 * HTML toggle for the rows Word pasted a mess into.
 */
export default function RichTextField({ value, onChange, minHeight = 200, placeholder = '', note }) {
  const [source, setSource] = useState(false);
  const box = useRef(null);
  /**
   * The value last written into the node, or last read out of it.
   *
   * React re-renders with the value an edit just produced, and writing innerHTML
   * again drops the caret back to the top of the field, so the write below has
   * to happen only when the value changed somewhere other than in here. What is
   * compared is the VALUE, not the rendered markup, so the check never rests on
   * the sanitiser being idempotent.
   */
  const mine = useRef(null);

  useEffect(() => {
    if (source || !box.current) return;
    const v = String(value ?? '');
    if (v === mine.current) return;
    mine.current = v;
    renderInto(box.current, v);
  }, [value, source]);

  function emit() {
    if (!box.current) return;
    const next = normalizeEditorHtml(box.current.innerHTML);
    mine.current = next;
    onChange(next);
  }

  function show(asSource) {
    // The editable node is unmounted while the markup pane is open, so the next
    // one mounts empty. Forgetting what was written into the old one is what
    // makes the effect above write again; without it the value would match what
    // this ref still remembers and the pane would come back blank.
    mine.current = null;
    setSource(asSource);
  }

  function onPaste(e) {
    const html = e.clipboardData?.getData('text/html');
    // Plain text needs nothing; the browser's own insert is already text.
    if (!html) return;
    // A clipboard from Word or from a web page carries a stylesheet's worth of
    // markup. Inserting the sanitised form keeps the node holding only what the
    // field can store, so what is on screen is what will save. execCommand is
    // the only insert that preserves the caret and the undo stack, and it is
    // deprecated in name only; every browser this app supports implements it.
    e.preventDefault();
    document.execCommand('insertHTML', false, sanitizeHtml(html));
    emit();
  }

  return (
    <div className="rt">
      <div className="rt-b">
        <span className="rt-n">{note ?? (source ? 'Editing the stored markup directly.' : 'Formatted view of the stored rich text.')}</span>
        <div className="rt-sw" role="group" aria-label="Editing mode">
          <button type="button" className={source ? '' : 'on'} aria-pressed={!source} onClick={() => show(false)}>Formatted</button>
          <button type="button" className={source ? 'on' : ''} aria-pressed={source} onClick={() => show(true)}>HTML</button>
        </div>
      </div>
      {source ? (
        /* Verbatim, deliberately. Someone who opened this pane is here to fix
           the markup, and rewriting it under them while they type would fight
           them; the formatted view sanitises on the way back out. */
        <textarea className="in rt-s" style={{ minHeight }} value={String(value ?? '')}
          placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
      ) : (
        <div ref={box} className="rt-e" style={{ minHeight, maxHeight: 420 }}
          contentEditable suppressContentEditableWarning role="textbox" aria-multiline="true"
          data-ph={placeholder} onInput={emit} onBlur={emit} onPaste={onPaste} />
      )}
    </div>
  );
}
