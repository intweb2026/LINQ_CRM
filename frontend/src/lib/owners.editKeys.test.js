// The Market Research columns are not cosmetic: backend paper_review/access.py
// reads market_research_senior / market_research_junior to decide which events a
// reviewer's public paper review form offers. If they ever drop out of the event
// forms' editable set again, a reviewer assigned to the wrong event has no way to
// be corrected and their form goes empty, with nothing reporting a fault.
import { OWNER_EDIT_KEYS, OWNER_EDIT_FIELDS, OWNER_KEYS } from './owners';

test('the event forms can assign both Market Research columns', () => {
  expect(OWNER_EDIT_KEYS).toContain('mr_senior');
  expect(OWNER_EDIT_KEYS).toContain('mr_junior');
});

test('every editable key is a real owner column with a label', () => {
  OWNER_EDIT_KEYS.forEach((k) => expect(OWNER_KEYS).toContain(k));
  OWNER_EDIT_FIELDS.forEach((f) => expect(f.label).toBeTruthy());
  expect(OWNER_EDIT_FIELDS).toHaveLength(OWNER_EDIT_KEYS.length);
});
