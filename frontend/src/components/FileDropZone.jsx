import { useRef, useState } from 'react';
import { Icon } from '../lib/icons';

/**
 * The upload target for every import in the app: click to browse, OR drag a file
 * onto it.
 *
 * THE BUG THIS FIXES
 * All three import modals rendered `<label className="dz">` around a hidden file
 * input, under a heading reading "Drop a file, or click to browse". The label made
 * CLICKING work. Nothing implemented the drop — no onDragOver, no onDrop, no
 * dataTransfer anywhere in the tree. So a dragged file did what a dragged file does
 * on a page that ignores it: the browser navigated away to display it, discarding
 * the open modal and any column mapping in it. The UI advertised a feature it had
 * never had.
 *
 * WHY A SHARED COMPONENT
 * Three copies of that markup existed, so there would have been three copies of the
 * drag handling — each free to forget one of the four things below.
 *
 * IT IS STILL A <label>, DELIBERATELY
 * The obvious alternative — a div with onClick calling inputRef.click() — recurses:
 * a programmatic .click() dispatches a real event that bubbles back to the div,
 * whose onClick calls .click() again. A label activates its input natively, with no
 * JS and nothing to bubble, and it keeps keyboard access working through the input
 * itself (visually hidden rather than display:none, which would make it unfocusable).
 *
 * FOUR THINGS DRAG-AND-DROP NEEDS, none of them optional:
 *   1. preventDefault on dragOver AND on drop. Without it the browser opens the file
 *      and the modal is gone. This is the whole bug.
 *   2. A depth counter for the highlight. dragenter/dragleave fire for every child
 *      element the pointer crosses, so a boolean flickers off the moment the cursor
 *      passes over the icon or heading inside the zone.
 *   3. Extension checking on drop. `accept` filters the BROWSE dialog only; a drop
 *      bypasses it entirely, so without this a dropped .pdf reaches the parser and
 *      comes back as "could not read that file" instead of naming the real problem.
 *   4. Clearing input.value after a pick. Choosing the same file twice fires no
 *      change event otherwise, which reads as the second attempt being ignored — and
 *      re-picking a file you have just corrected is a normal thing to do.
 */
export default function FileDropZone({
  accept = '.xlsx,.xls,.csv,.json',
  onFile,
  onReject,
  title = 'Drop a file, or click to browse',
  hint,
  disabled = false,
}) {
  // A counter, not a boolean — see (2) above.
  const depth = useRef(0);
  const [over, setOver] = useState(false);

  const allowed = accept.split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);

  function accepted(file) {
    if (!allowed.length) return true;
    const name = (file.name || '').toLowerCase();
    return allowed.some((ext) => name.endsWith(ext));
  }

  function hand(file) {
    if (!file) return;
    if (!accepted(file)) {
      // Names the file, so the message is about THIS file rather than leaving the
      // parser to report something unreadable.
      onReject && onReject(`${file.name} is not a supported file — use ${allowed.join(', ')}`);
      return;
    }
    onFile(file);
  }

  return (
    <label
      className={'dz' + (over ? ' dz-over' : '') + (disabled ? ' dz-off' : '')}
      style={{ display: 'block' }}
      onDragEnter={(e) => { e.preventDefault(); depth.current += 1; if (!disabled) setOver(true); }}
      onDragOver={(e) => { e.preventDefault(); }}
      onDragLeave={(e) => {
        e.preventDefault();
        depth.current = Math.max(0, depth.current - 1);
        if (depth.current === 0) setOver(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        depth.current = 0;
        setOver(false);
        if (disabled) return;
        // Absent when what was dragged is a link or selected text, not a file.
        hand(e.dataTransfer?.files?.[0]);
      }}
    >
      <input
        type="file" accept={accept} disabled={disabled}
        // Visually hidden, NOT display:none — it stays focusable, so the zone can
        // still be reached and opened from the keyboard.
        style={{ position: 'absolute', width: 1, height: 1, opacity: 0, pointerEvents: 'none' }}
        onChange={(e) => {
          const file = e.target.files && e.target.files[0];
          e.target.value = '';   // see (4) — lets the same file be picked again
          hand(file);
        }}
      />
      <div className="dz-i"><Icon name="upload" size={20} /></div>
      <h3>{over ? 'Release to add this file' : title}</h3>
      <p>{hint || `${allowed.join(', ')} — Zoho column names are matched automatically`}</p>
    </label>
  );
}
