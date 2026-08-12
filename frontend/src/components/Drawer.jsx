import { useEffect, useState } from 'react';
import { Icon } from '../lib/icons';

export default function Drawer({ wide, onClose, head, tabs, foot, children }) {
  const [show, setShow] = useState(false);
  useEffect(() => {
    const raf = requestAnimationFrame(() => setShow(true));
    function onKey(e) { if (e.key === 'Escape') close(); }
    document.addEventListener('keydown', onKey);
    return () => { cancelAnimationFrame(raf); document.removeEventListener('keydown', onKey); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function close() {
    setShow(false);
    setTimeout(onClose, 320);
  }

  return (
    <>
      <div className={'scrim' + (show ? ' show' : '')} onClick={close} />
      <div className={'dr' + (wide ? ' wide' : '') + (show ? ' show' : '')}>
        <div className="dr-h">
          <div className="dr-h-b">{head}</div>
          <button className="dr-x" aria-label="Close" onClick={close}><Icon name="x" size={15} /></button>
        </div>
        {tabs ? <div className="dr-tabs">{tabs}</div> : null}
        <div className="dr-b">{children}</div>
        {foot ? <div className="dr-f">{foot}</div> : null}
      </div>
    </>
  );
}
