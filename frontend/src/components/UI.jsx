import { Icon } from '../lib/icons';
import { extUrl, nf } from '../lib/helpers';

/**
 * A stored URL rendered as a link that actually goes there.
 *
 * Three things are deliberate. `href` carries the resolved absolute URL, so
 * ctrl/cmd-click, middle-click and the context menu's "open link in new tab" all
 * work — an href of "#" with a preventDefault handler (what this replaced) makes
 * every one of those reopen the CRM instead. The click is stopped from bubbling
 * because these links sit inside table rows whose own onClick opens a record, and
 * a plain click on the link must follow the link, not do both. And text that isn't
 * a URL renders as text rather than as a dead link.
 */
export function ExtLink({ value, className = 'lnk', children }) {
  const url = extUrl(value);
  if (!url) return value ? <span>{children ?? value}</span> : <span className="dim">—</span>;
  return (
    <a className={className} href={url} target="_blank" rel="noopener noreferrer" title={url}
      onClick={(e) => e.stopPropagation()}>
      {children ?? value}
    </a>
  );
}

export function PageHead({ title, sub, actions }) {
  return (
    <div className="ph">
      <div className="ph-row">
        <div>
          <h1>{title}</h1>
          {sub ? <p>{sub}</p> : null}
        </div>
        {actions ? <div className="ph-act">{actions}</div> : null}
      </div>
    </div>
  );
}

export function Kpi({ label, value, unit, sub, icon = 'target', tone, hi, pill, pillTone = 'flat', bar, barTone, onClick }) {
  return (
    <div className={'kpi' + (hi ? ' hi' : '') + (onClick ? ' clk' : '')} onClick={onClick}>
      <div className="kpi-t">
        <span className="kpi-i" style={tone && !hi ? { background: tone + '14', color: tone } : undefined}>
          <Icon name={icon} size={15} />
        </span>
        <span className="kpi-l">{label}</span>
        {pill ? <span className={'kpi-p pill-' + pillTone}>{pill}</span> : null}
      </div>
      <div className="kpi-v">{typeof value === 'number' ? nf(value) : value}{unit ? <small>{unit}</small> : null}</div>
      {sub ? <div className="kpi-s">{sub}</div> : null}
      {bar != null ? <div className="kpi-b"><i style={{ width: bar + '%', background: barTone || 'var(--t-500)' }} /></div> : null}
    </div>
  );
}

export function Tabs({ list, active, onPick, actions }) {
  return (
    <div className="tabs">
      <div className="tabs-list">
        {list.map((t) => (
          <button key={t.id} className={'tab' + (t.id === active ? ' on' : '')} onClick={() => onPick(t.id)}>
            {t.label}{t.count != null ? <span className="c">{nf(t.count)}</span> : null}
          </button>
        ))}
      </div>
      {actions ? <div className="tabs-act">{actions}</div> : null}
    </div>
  );
}

export function Sparkline({ v, w = 62, h = 21 }) {
  if (!v || !v.length) return null;
  const mx = Math.max(...v), mn = Math.min(...v), rg = mx - mn || 1;
  const pts = v.map((x, i) => [(i / (v.length - 1)) * w, h - 2 - ((x - mn) / rg) * (h - 4)]);
  const d = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
  return (
    <svg className="spk" viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      <path className="f" d={`${d} L${w} ${h} L0 ${h} Z`} />
      <path d={d} />
    </svg>
  );
}

export function Donut({ segs, size = 118 }) {
  const R = size / 2, sw = 13, r = R - sw / 2 - 1, C = 2 * Math.PI * r;
  const tot = segs.reduce((s, x) => s + x.v, 0) || 1;
  let off = 0;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <circle cx={R} cy={R} r={r} fill="none" stroke="var(--n-75)" strokeWidth={sw} />
      {segs.map((s, i) => {
        const len = (s.v / tot) * C;
        const el = (
          <circle key={i} cx={R} cy={R} r={r} fill="none" stroke={s.c} strokeWidth={sw} strokeLinecap="round"
            strokeDasharray={`${len.toFixed(2)} ${(C - len).toFixed(2)}`} strokeDashoffset={(-off).toFixed(2)} />
        );
        off += len;
        return el;
      })}
    </svg>
  );
}

export function EmptyState({ icon = 'filter', title, body, action }) {
  return (
    <div className="mt">
      <div className="mt-i"><Icon name={icon} size={21} /></div>
      <h3>{title}</h3>
      {body ? <p>{body}</p> : null}
      {action}
    </div>
  );
}

export function Seg({ options, value, onChange }) {
  return (
    <div className="seg">
      {options.map((o) => (
        <button key={o.value} className={o.value === value ? 'on' : ''} onClick={() => onChange(o.value)}>
          {o.icon ? <Icon name={o.icon} size={13} /> : null}{o.label}
        </button>
      ))}
    </div>
  );
}
