import Popover from './Popover';
import { Icon } from '../lib/icons';

// Custom-styled dropdown replacing native <select> in forms — the OS-native
// option list can't be restyled with CSS (see DelegateTable.jsx), so this
// renders its own themed menu via the existing Popover primitive instead.
//
// `labelOf` separates what an option IS from what it READS AS: the callback gets
// the stored value and returns the wording to show, so a picklist can be relabelled
// without touching the value it writes (Paid → "Payable", see lib/constants.js).
// Omitted, the option is its own label, which is how every other caller uses this.
export default function Select({ value, options, onChange, className = 'in', placeholder = 'Select…', width, labelOf }) {
  const text = labelOf || ((o) => o);
  return (
    <Popover
      width={width}
      trigger={({ toggle, open }) => (
        <button type="button" className={className + ' sel-trigger' + (open ? ' open' : '')} onClick={toggle}>
          <span className="sel-v">{value ? text(value) : <span className="dim">{placeholder}</span>}</span>
          <Icon name="chevD" size={13} />
        </button>
      )}
    >
      {({ close }) => (
        <div className="pop-mx">
          {options.map((o) => (
            <button type="button" className="pop-i" key={o} onClick={() => { onChange(o); close(); }}>
              {o === value ? <Icon name="check" size={14} /> : <span style={{ width: 14, flexShrink: 0 }} />}
              {text(o)}
            </button>
          ))}
        </div>
      )}
    </Popover>
  );
}
