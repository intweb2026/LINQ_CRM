/**
 * lib/helpers.test.js
 * ───────────────────
 * The IST renderers, and the one property that cannot be checked by looking.
 *
 * WHY THIS FILE EXISTS
 * fdate/fmy/ftime used to read a timestamp back with getDate()/getHours(), which
 * are the VIEWER'S machine timezone. On an IST machine — which is what the team
 * develops on — that is indistinguishable from correct, and stays
 * indistinguishable through any test written on that machine. The bug only
 * appears on someone else's clock, which is the worst possible place for it to
 * appear and the reason this is pinned rather than eyeballed.
 *
 * So every assertion runs under SEVERAL timezones, and the point is that the
 * answer does not move. Node re-reads process.env.TZ on the next Date operation
 * (v16+), so setting it mid-test is enough; the original is restored afterwards
 * so nothing downstream inherits a fake clock.
 */
import { fdate, fmy, ftime, IST_OFFSET_MS } from './helpers';

/** Every zone this must give the same answer in. */
const ZONES = [
  'Asia/Kolkata',        // the team's own, where the old code passed by accident
  'UTC',
  'America/Los_Angeles', // -07/-08, the direction that shifted dates BACKWARDS
  'Pacific/Kiritimati',  // +14, the largest positive offset there is
  'Australia/Adelaide',  // +09:30, a half-hour zone that is not IST
];

/** Run `fn` once per zone, restoring the real one afterwards. */
function inEveryZone(fn) {
  const real = process.env.TZ;
  try {
    for (const tz of ZONES) {
      process.env.TZ = tz;
      fn(tz);
    }
  } finally {
    if (real === undefined) delete process.env.TZ;
    else process.env.TZ = real;
  }
}

describe('the offset itself', () => {
  test('IST is +05:30 exactly, and never anything else', () => {
    // A fixed offset is only legitimate because India has no DST. If this is
    // ever "simplified" to whole hours, every boundary test below goes with it.
    expect(IST_OFFSET_MS).toBe(19800000);
    expect(IST_OFFSET_MS / 3600000).toBe(5.5);
  });
});

describe('fdate', () => {
  test('renders the IST day, in every timezone the viewer might be in', () => {
    inEveryZone((tz) => {
      // 20:15Z on the 27th is 01:45 IST on the 28th. Inside the 5h30m window
      // where the IST day and the UTC day disagree, which is the whole point.
      expect(fdate('2026-08-27T20:15:00Z')).toBe(`28 Aug 2026`);
      // And a plain UTC-morning timestamp, where they agree.
      expect(fdate('2026-08-27T06:00:00Z')).toBe(`27 Aug 2026`);
      expect(tz).toBeTruthy();
    });
  });

  test('the day boundary is 18:30Z, to the minute', () => {
    inEveryZone(() => {
      expect(fdate('2026-08-25T18:29:59Z')).toBe('25 Aug 2026');
      expect(fdate('2026-08-25T18:30:00Z')).toBe('26 Aug 2026');
    });
  });

  test('a plain calendar date is not dragged into the previous day', () => {
    // THE BUG A NEGATIVE-OFFSET VIEWER SAW. '2026-08-21' parses as UTC midnight,
    // so getDate() in Los Angeles reported the 20th for a date nobody disputed.
    // Shifting to IST puts it at 05:30 on the 21st, which cannot underflow.
    inEveryZone(() => {
      expect(fdate('2026-08-21')).toBe('21 Aug 2026');
      expect(fdate('2026-01-01')).toBe('01 Jan 2026');
      expect(fdate('2026-12-31')).toBe('31 Dec 2026');
    });
  });

  test('an empty or unparseable value is an em dash, not a wrong date', () => {
    inEveryZone(() => {
      expect(fdate(null)).toBe('—');
      expect(fdate(undefined)).toBe('—');
      expect(fdate('')).toBe('—');
      expect(fdate('not a date')).toBe('—');
    });
  });
});

describe('ftime', () => {
  test('renders the IST clock, in every timezone', () => {
    inEveryZone(() => {
      expect(ftime('2026-08-27T20:15:00Z')).toBe('01:45');
      expect(ftime('2026-08-27T00:00:00Z')).toBe('05:30');
      // Midnight IST reads as 00:00 and never as 24:00.
      expect(ftime('2026-08-27T18:30:00Z')).toBe('00:00');
    });
  });

  test('fdate and ftime agree about which day it is', () => {
    // They are rendered side by side in the Modified Time cell
    // (pages/BookingsPage.jsx), so a disagreement would print a visible lie.
    inEveryZone(() => {
      expect(fdate('2026-08-27T20:15:00Z')).toBe('28 Aug 2026');
      expect(ftime('2026-08-27T20:15:00Z')).toBe('01:45');
    });
  });

  test('an unparseable value is an em dash', () => {
    inEveryZone(() => {
      expect(ftime(null)).toBe('—');
      expect(ftime('nonsense')).toBe('—');
    });
  });
});

describe('fmy', () => {
  test('a timestamp late on the last of the month belongs to the NEXT month', () => {
    // 20:00Z on 31 Jul is 01:30 IST on 1 Aug. Reading this as July would put a
    // row in the wrong month's bucket, which is the kind of off-by-one nobody
    // notices until two screens are reconciled.
    inEveryZone(() => {
      expect(fmy('2026-07-31T20:00:00Z')).toBe('Aug 2026');
      expect(fmy('2026-07-31T12:00:00Z')).toBe('Jul 2026');
      expect(fmy('2026-12-31T20:00:00Z')).toBe('Jan 2027');
    });
  });

  test('an unparseable value is an em dash', () => {
    inEveryZone(() => {
      expect(fmy(null)).toBe('—');
      expect(fmy('nonsense')).toBe('—');
    });
  });
});
