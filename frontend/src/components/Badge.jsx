// React equivalents of the legacy html-string badge/avatar helpers in 01-data.js.
import { avc, ini } from '../lib/helpers';
import {
  STATUS_TONE, ATT_TONE, EV_TONE, TK_STATUS, TK_PRIORITY, ROLE_TONE, ROLE_FULL, WH_STATUS, GSYNC_STATUS_TONE,
} from '../lib/constants';

export function Dot({ tone = 'neutral', children }) {
  return (
    <span className={'bg bg-' + tone}>
      <i />
      {children}
    </span>
  );
}

export const StatusBadge = ({ value }) => <Dot tone={STATUS_TONE[value] || 'neutral'}>{value}</Dot>;
export const AttBadge = ({ value }) => <Dot tone={ATT_TONE[value] || 'neutral'}>{value}</Dot>;
export const EvBadge = ({ value }) => <Dot tone={EV_TONE[value] || 'neutral'}>{value}</Dot>;
export const TkBadge = ({ value }) => {
  const c = TK_STATUS[value] || TK_STATUS.draft;
  return <Dot tone={c.t}>{c.l}</Dot>;
};
export const PriBadge = ({ value }) =>
  !value ? <span className="dim">—</span> : <span className={'tg bg-' + (TK_PRIORITY[value] || 'neutral')}>{value}</span>;
export const RoleBadge = ({ value }) => <span className={'tg bg-' + (ROLE_TONE[value] || 'neutral')}>{ROLE_FULL[value] || value}</span>;
export const WhBadge = ({ value }) => <Dot tone={WH_STATUS[value] || 'neutral'}>{value}</Dot>;
export const GsBadge = ({ value }) => (
  <Dot tone={GSYNC_STATUS_TONE[value] || 'neutral'}>{value === 'partial_success' ? 'Partial' : value.charAt(0).toUpperCase() + value.slice(1)}</Dot>
);
export const StatusPill = ({ value, positive = 'active' }) => <Dot tone={value === positive ? 'green' : 'neutral'}>{value}</Dot>;

export function Av({ name, size = 'md' }) {
  return (
    <span className={'av av-' + size} style={{ background: avc(name) }} aria-hidden="true">
      {ini(name)}
    </span>
  );
}

export function Who({ name, sub, size = 'sm', mono = false }) {
  return (
    <span className="who">
      <Av name={name} size={size} />
      <span className="who-t">
        <span className="who-n">{name}</span>
        {sub ? <span className={'who-s' + (mono ? ' mono' : '')}>{sub}</span> : null}
      </span>
    </span>
  );
}
