import { createContext, useCallback, useContext, useRef, useState } from 'react';
import { Icon } from '../lib/icons';

const ToastContext = createContext(null);
const TYPE_ICON = { nf: 'info', ok: 'check', wn: 'warn', er: 'x' };

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const idRef = useRef(1);

  const remove = useCallback((id) => {
    setToasts((list) => list.map((t) => (t.id === id ? { ...t, dying: true } : t)));
    setTimeout(() => setToasts((list) => list.filter((t) => t.id !== id)), 250);
  }, []);

  const toast = useCallback((msg, type = 'nf', dur = 3600) => {
    const id = idRef.current++;
    setToasts((list) => [...list, { id, msg, type }]);
    setTimeout(() => remove(id), dur);
  }, [remove]);

  return (
    <ToastContext.Provider value={toast}>
      {children}
      <div className="tst" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={'ts ' + t.type} style={t.dying ? { opacity: 0, transform: 'translateX(17px)' } : undefined}>
            <span className="ts-i"><Icon name={TYPE_ICON[t.type] || 'info'} size={14} /></span>
            <span className="ts-m">{t.msg}</span>
            <button className="ts-x" aria-label="Dismiss" onClick={() => remove(t.id)}><Icon name="x" size={12} /></button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within ToastProvider');
  return ctx;
}
