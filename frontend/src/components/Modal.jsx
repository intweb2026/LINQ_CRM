import { useEffect } from 'react';
import { Icon } from '../lib/icons';

const SIZE_CLASS = { sm: 'sm', mdw: 'mdw', lg: 'lg', xl: 'xl', full: 'full' };

/**
 * `bodyFill` hands the vertical scroll to ONE child instead of the modal body.
 *
 * By default `.md-b` scrolls, which is right for a modal whose content is a form
 * that simply runs long. It is wrong for one whose content is itself a scrolling
 * table: the booking modals put a 420px-capped delegate grid inside a scrolling
 * body, so on any window under roughly 780px tall BOTH boxes scrolled, and the
 * grid's sticky header and pinned columns only tracked the inner one. With this
 * set, `.md-b` stops scrolling and lays its children out in a column; the child
 * marked `fs-fill` takes the leftover height and does the scrolling on its own.
 * See the `.md-b.fill` block in styles/overlays.css.
 */
export default function Modal({ title, sub, size = 'mdw', onClose, children, footer, header, footJustify, bodyFill = false }) {
  useEffect(() => {
    function onKey(e) { if (e.key === 'Escape') onClose(); }
    document.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [onClose]);

  return (
    <>
      <div className="modal-scrim show" onClick={onClose} />
      <div className="mw">
        <div className={'md ' + (SIZE_CLASS[size] || 'mdw')} role="dialog" aria-modal="true">
          {header ?? (
            <div className="md-h">
              <div className="md-h-b">
                <h2>{title}</h2>
                {sub ? <p>{sub}</p> : null}
              </div>
              <button className="dr-x" aria-label="Close" onClick={onClose}><Icon name="x" size={15} /></button>
            </div>
          )}
          <div className={'md-b' + (bodyFill ? ' fill' : '')}>{children}</div>
          {footer ? <div className="md-f" style={footJustify ? { justifyContent: footJustify } : undefined}>{footer}</div> : null}
        </div>
      </div>
    </>
  );
}
