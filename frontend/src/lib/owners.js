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
 * The owner columns the EVENT FORMS let you set — ALL OF THEM.
 *
 * Two are load-bearing rather than cosmetic: backend paper_review/access.py reads
 * market_research_senior / market_research_junior to work out which events a
 * reviewer's paper review form offers, so a reviewer who cannot be named here is
 * a reviewer whose form is empty, with no way to fix it short of a CSV re-import.
 * Dropping those two from this set breaks that silently.
 *
 * Telemarketing and SpEx lead were display-only, on the argument that the Teams
 * module already knows them and a blank column inherits its answer. It does — but
 * there was no way to name a DIFFERENT person on one event, which is what these
 * two editors are for. A value typed here outranks the team's answer permanently,
 * so leaving a column Unassigned stays the right move whenever the team's own lead
 * is the correct answer.
 *
 * An INHERITED name is never written back — the selects read form values raw for
 * exactly that reason — and the backend grants access on the stored column alone,
 * so the Teams module's MR lead does not silently acquire every event with a blank
 * column.
 */
export const OWNER_EDIT_KEYS = OWNER_KEYS;

export const OWNER_EDIT_FIELDS = OWNER_FIELDS;

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

/**
 * The active users who may be NAMED in `key`'s select, in the event forms.
 *
 * The owner columns are free text and the selects offered every active user, so
 * "Sales team leader" listed all 25 salespeople and the two Market Research
 * columns listed the whole company. The rule here mirrors what the backend
 * already infers when a column is blank — events/serializers.py
 * OWNER_ROLE_SOURCES reads the sales lead off the leads of the Sales team and
 * the MR columns off Market Research — so the value a human can type matches the
 * one that gets inherited.
 *
 * Only sales_lead carries the lead flag; every other column takes its whole team,
 * leads included, because the person doing the work on one event is not usually
 * the team's lead.
 *
 * A key with no rule falls through to every active user, so adding a column to
 * OWNER_EDIT_KEYS never silently ships an empty select.
 */
const OWNER_POOL_RULES = {
  sales_team: (u) => u.role === 'sales',
  sales_lead: (u) => u.role === 'sales' && u.is_lead,
  tele_team:  (u) => u.role === 'telemarketing',
  mr_senior:  (u) => u.role === 'market_research',
  mr_junior:  (u) => u.role === 'market_research',
  spex_lead:  (u) => u.role === 'spex',
};

export function ownerPool(users, key) {
  const rule = OWNER_POOL_RULES[key];
  return (users || []).filter((u) => u.status === 'active' && (!rule || rule(u)));
}
