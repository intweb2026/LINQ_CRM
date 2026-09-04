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
];

export const OWNER_KEYS = OWNER_FIELDS.map((f) => f.key);

/**
 * The owner columns the EVENT FORMS let you set.
 *
 * The SCA and the sales lead, because those are genuinely per-event, and the two
 * Market Research columns, because those DECIDE ACCESS. backend
 * paper_review/access.py reads market_research_senior / market_research_junior to
 * work out which events a reviewer's paper review form offers, so a reviewer who
 * cannot be named here is a reviewer whose form is empty, with no way to fix it
 * short of a CSV re-import.
 *
 * The other three stay display-only: nothing reads them for access, so an editor
 * would only invite re-typing what the Teams module already knows, and a value
 * typed on the event outranks the team's answer permanently.
 *
 * Display is unaffected: the drawer's Teams tab and the Events table still show
 * all seven, inherited where the event says nothing. An INHERITED name is never
 * written back — the selects read form values raw for exactly that reason — and
 * the backend grants access on the stored column alone, so the Teams module's MR
 * lead does not silently acquire every event with a blank column.
 */
export const OWNER_EDIT_KEYS = ['sales_team', 'sales_lead', 'mr_senior', 'mr_junior'];

export const OWNER_EDIT_FIELDS = OWNER_FIELDS.filter((f) => OWNER_EDIT_KEYS.includes(f.key));

/**
 * Placeholders that mean "nothing is assigned". Mirrors _BLANK_OWNER_VALUES in
 * backend/events/serializers.py — a column holding one of these must inherit,
 * not read as an answer.
 */
const BLANK = ['', '-', '–', '—'];

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
  if (!BLANK.includes(own)) return { names: [own], name: own, inherited: false, team: '' };

  const src = ((ev && ev.owner_src) || {})[key];
  const names = (src && src.names) || [];
  if (names.length) {
    // `name` is the joined form for the dense single-line callers (table cells).
    // `names` is the list, and callers with room render one element per lead —
    // NOTHING here picks a primary or truncates. A team with three leads shows
    // three.
    return { names, name: names.join(', '), inherited: true, team: (src && src.team) || '' };
  }

  return { names: [], name: '', inherited: false, team: '' };
}
