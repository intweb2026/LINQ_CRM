// The Market Research columns are not cosmetic: backend paper_review/access.py
// reads market_research_senior / market_research_junior to decide which events a
// reviewer's public paper review form offers. If they ever drop out of the event
// forms' editable set again, a reviewer assigned to the wrong event has no way to
// be corrected and their form goes empty, with nothing reporting a fault.
import { OWNER_EDIT_KEYS, OWNER_EDIT_FIELDS, OWNER_KEYS, ownerPool } from './owners';

test('the event forms can assign both Market Research columns', () => {
  expect(OWNER_EDIT_KEYS).toContain('mr_senior');
  expect(OWNER_EDIT_KEYS).toContain('mr_junior');
});

test('every editable key is a real owner column with a label', () => {
  OWNER_EDIT_KEYS.forEach((k) => expect(OWNER_KEYS).toContain(k));
  OWNER_EDIT_FIELDS.forEach((f) => expect(f.label).toBeTruthy());
  expect(OWNER_EDIT_FIELDS).toHaveLength(OWNER_EDIT_KEYS.length);
});

// The selects used to offer every active user for every column, so Sales team
// leader listed all 25 salespeople and the MR columns listed the whole company.
const USERS = [
  { id: 1, name: 'Sales Lead',    role: 'sales',           is_lead: true,  status: 'active' },
  { id: 2, name: 'Sales Rep',     role: 'sales',           is_lead: false, status: 'active' },
  { id: 3, name: 'MRE',           role: 'market_research', is_lead: false, status: 'active' },
  { id: 4, name: 'Tele Lead',     role: 'telemarketing',   is_lead: true,  status: 'active' },
  { id: 5, name: 'Tele Rep',      role: 'telemarketing',   is_lead: false, status: 'active' },
  { id: 6, name: 'SpEx',          role: 'spex',            is_lead: false, status: 'active' },
  { id: 7, name: 'Ex-sales lead', role: 'sales',           is_lead: true,  status: 'inactive' },
];
const names = (key) => ownerPool(USERS, key).map((u) => u.name);

test('the sales lead select offers only active sales leads', () => {
  expect(names('sales_lead')).toEqual(['Sales Lead']);
});

test('the Market Research selects offer only Market Research', () => {
  expect(names('mr_senior')).toEqual(['MRE']);
  expect(names('mr_junior')).toEqual(['MRE']);
});

// The three team columns take the whole team, lead included: only Sales team
// leader is a leads-only column.
test('SCA, Telemarketing and SpEx offer their whole active team', () => {
  expect(names('sales_team')).toEqual(['Sales Lead', 'Sales Rep']);
  expect(names('tele_team')).toEqual(['Tele Lead', 'Tele Rep']);
  expect(names('spex_lead')).toEqual(['SpEx']);
});

// A column added to the editable set before it has a rule must not ship empty.
test('a column with no rule still offers every active user', () => {
  expect(ownerPool(USERS, 'future_column')).toHaveLength(6);
});
