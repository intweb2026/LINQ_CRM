import Popover from './Popover';
import { Icon } from '../lib/icons';

// Custom-styled dropdown replacing native <select> in forms — the OS-native
// option list can't be restyled with CSS (see DelegateTable.jsx), so this
// renders its own themed menu via the existing Popover primitive instead.
export default function Select({ value, options, onChange, className = 'in', placeholder = 'Select…', width }) {
  return (
    <Popover
      width={width}
      trigger={({ toggle, open }) => (
        <button type="button" className={className + ' sel-trigger' + (open ? ' open' : '')} onClick={toggle}>
          <span className="sel-v">{value || <span className="dim">{placeholder}</span>}</span>
          <Icon name="chevD" size={13} />
        </button>
      )}
    >
      {({ close }) => (
        <div className="pop-mx">
          {options.map((o) => (
            <button type="button" className="pop-i" key={o} onClick={() => { onChange(o); close(); }}>
              {o === value ? <Icon name="check" size={14} /> : <span style={{ width: 14, flexShrink: 0 }} />}
              {o}
            </button>
          ))}
        </div>
      )}
    </Popover>
  );
}
