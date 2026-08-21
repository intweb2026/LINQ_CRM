// The seven team-ownership columns on an event, and how to read one.
//
// These live in five places — the Events table, the drawer's Teams tab, the New
// and Edit event forms, and the booking modals' owner chips — and each one used
// to inline its own list and its own blank handling. Six of the seven are empty
// on every event in the live data, so all five rendered six blank rows and there
// was no single place to fix it.
//
// The backend now answers the blank ones from the Teams module (see
// events/serializers.py OWNER_ROLE_SOURCES) and reports what it filled in under
// `owner_resolution`, mapped to `owner_src` by api/events.js. This module is the
// one reader of that.

/** Canonical order and labels. Every consumer renders these, in this order. */
export const OWNER_FIELDS = [
  { key: 'sales_team', label: 'SCA' },
  { key: 'sales_lead', label: 'Sales team leader' },
  { key: 'tele_team',  label: 'Telemarketing' },
  { key: 'mr_senior',  label: 'Market Research Sr.' },
  { key: 'mr_junior',  label: 'Market Research Jr.' },
  { key: 'spex_lead',  label: 'SpEx lead' },
  { key: 'event_mgmt', label: 'Event management' },
];

export const OWNER_KEYS = OWNER_FIELDS.map((f) => f.key);

/**
 * Who owns `key` on this event, and where that answer came from.
 *
 * Returns `{ name, inherited, team }`. `name` is '' when nothing owns it.
 * `inherited` means the name belongs to the TEAM, not to this event — callers
 * must render that difference, and must never write an inherited name back:
 * saving it would turn "whoever leads Telemarketing" into "this one person,
 * frozen", silently, on the next unrelated edit of the form.
 *
 * '—' counts as blank. NewEventModal writes it for unassigned display-only
 * columns, so it is a real stored value that means nothing is assigned.
 */
export function ownerOf(ev, key) {
  const own = String((ev && ev[key]) || '').trim();
  if (own && own !== '—') return { name: own, inherited: false, team: '' };

  const src = ((ev && ev.owner_src) || {})[key];
  if (src && src.name) return { name: src.name, inherited: true, team: src.team || '' };

  return { name: '', inherited: false, team: '' };
}
