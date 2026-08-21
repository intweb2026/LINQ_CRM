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

/**
 * A person cell: name, with an optional second line under it.
 *
 * `avatar` is off in LISTING TABLES and on everywhere else, and the split is
 * deliberate rather than per-taste. A table row is already dense and is read
 * by scanning a column of names, so a coloured initials disc on every row is
 * decoration competing with the text beside it. In a card header, a drawer,
 * or an overlapping avatar stack the disc is the visual anchor — it is what
 * you recognise before you read anything — so it stays there.
 *
 * Defaulting to `true` keeps every existing caller rendering exactly as it
 * did; the table call sites opt out explicitly with `avatar={false}`.
 */
/**
 * One team-ownership cell, for the dense listing tables.
 *
 * Takes the result of lib/owners.js `ownerOf`. An INHERITED name — one that
 * belongs to the team that owns the role rather than to this row — is muted and
 * carries the attribution in its tooltip, because a table row has no space for a
 * second line and a column of identical names with nothing marking them would
 * read as real per-event data. The drawer shows the same fact as a sub-line,
 * where there is room for it.
 */
export function OwnerName({ owner }) {
  if (!owner || !owner.name) return <span className="dim">—</span>;
  if (!owner.inherited) return <Who name={owner.name} avatar={false} />;
  return (
    <span
      className="dim"
      style={{ fontStyle: 'italic' }}
      title={`Inherited from ${owner.team || 'the owning team'} — no value set on this event`}
    >
      {owner.name}
    </span>
  );
}

export function Who({ name, sub, size = 'sm', mono = false, avatar = true }) {
  return (
    <span className="who">
      {avatar ? <Av name={name} size={size} /> : null}
      <span className="who-t">
        <span className="who-n">{name}</span>
        {sub ? <span className={'who-s' + (mono ? ' mono' : '')}>{sub}</span> : null}
      </span>
    </span>
  );
}
