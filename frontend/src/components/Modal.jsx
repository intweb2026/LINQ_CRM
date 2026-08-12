import { useEffect } from 'react';
import { Icon } from '../lib/icons';

const SIZE_CLASS = { sm: 'sm', mdw: 'mdw', lg: 'lg', xl: 'xl', full: 'full' };

export default function Modal({ title, sub, size = 'mdw', onClose, children, footer, header, footJustify }) {
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
          <div className="md-b">{children}</div>
          {footer ? <div className="md-f" style={footJustify ? { justifyContent: footJustify } : undefined}>{footer}</div> : null}
        </div>
      </div>
    </>
  );
}
