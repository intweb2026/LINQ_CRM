import { createContext, useCallback, useContext, useState } from 'react';
import Modal from '../components/Modal';

const ConfirmContext = createContext(null);

export function ConfirmProvider({ children }) {
  const [state, setState] = useState(null); // {title, sub, body, ok, danger, typed, resolve}
  const [typedValue, setTypedValue] = useState('');

  const confirm = useCallback((opts) => new Promise((resolve) => {
    setTypedValue('');
    setState({ ...opts, resolve });
  }), []);

  function finish(result) {
    state?.resolve(result);
    setState(null);
  }

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {state && (
        <Modal size="sm" title={state.title} sub={state.sub} onClose={() => finish(false)}
          footer={<>
            <button className="btn btn-s" onClick={() => finish(false)}>Cancel</button>
            <button
              className={'btn ' + (state.danger ? 'btn-d' : 'btn-p')}
              disabled={state.typed ? typedValue.trim() !== state.typed : false}
              onClick={() => finish(true)}
            >
              {state.ok || 'Confirm'}
            </button>
          </>}
        >
          {state.body}
          {state.typed && (
            <div className="fd" style={{ marginTop: 12 }}>
              <label className="fd-l">Type <b>{state.typed}</b> to confirm</label>
              <input className="in" autoComplete="off" value={typedValue} onChange={(e) => setTypedValue(e.target.value)} />
            </div>
          )}
        </Modal>
      )}
    </ConfirmContext.Provider>
  );
}

export function useConfirm() {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error('useConfirm must be used within ConfirmProvider');
  return ctx;
}
