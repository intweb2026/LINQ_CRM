import { rememberEvent, recallEvent } from './preEventDocs';

/**
 * The branch worth guarding is the STALE one. A remembered event that has since
 * lost every booking is gone from the list, and recall must fall back rather
 * than hand the page an event the server no longer reports — the page would ask
 * for its documents and get nothing, with a picker showing a real-looking event.
 */
const LIST = [
  { event_code: 'REU', edition: 2025, delegates: 199 },
  { event_code: 'CCC', edition: 2026, delegates: 93 },
  { event_code: 'WSE', edition: null, delegates: 12 },
];

beforeEach(() => window.localStorage.clear());

test('recalls the event that was remembered', () => {
  rememberEvent(LIST[1]);
  expect(recallEvent(LIST)).toBe(LIST[1]);
});

test('an edition of null is a real value, not a missing one', () => {
  rememberEvent(LIST[2]);
  expect(recallEvent(LIST)).toBe(LIST[2]);
});

test('falls back when the remembered event is no longer in the list', () => {
  rememberEvent(LIST[1]);
  expect(recallEvent([LIST[0]])).toBeNull();
});

test('falls back when nothing has been remembered', () => {
  expect(recallEvent(LIST)).toBeNull();
});

// Resolved against the live list, so the page never holds a stored copy of a
// row whose delegate count has moved on.
test('returns the live row, not the stored one', () => {
  rememberEvent({ event_code: 'REU', edition: 2025, delegates: 1 });
  expect(recallEvent(LIST).delegates).toBe(199);
});
