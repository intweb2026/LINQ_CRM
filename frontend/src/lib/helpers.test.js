/**
 * lib/helpers.test.js
 * ───────────────────
 * The Pacific renderers, and the properties that cannot be checked by looking.
 *
 * WHY THIS FILE EXISTS
 * fdate/fmy/ftime used to read a timestamp back with getDate()/getHours(), which
 * are the VIEWER'S machine timezone, so the same row read as a different day per
 * viewer. The bug only appears on someone else's clock, which is the worst
 * possible place for it to appear and the reason this is pinned rather than
 * eyeballed.
 *
 * So every assertion runs under SEVERAL timezones, and the point is that the
 * answer does not move. Node re-reads process.env.TZ on the next Date operation
 * (v16+), so setting it mid-test is enough; the original is restored afterwards
 * so nothing downstream inherits a fake clock.
 *
 * WHY THE EXPECTED VALUES CHANGED
 * The zone was Asia/Kolkata, a flat +05:30 with no DST, and the boundary was
 * 18:30Z all year. It is now America/Los_Angeles, which observes DST, so the
 * boundary is 07:00Z under PDT and 08:00Z under PST. Both are asserted below,
 * because a "simplification" back to a fixed offset passes one and fails the
 * other, which is exactly what should happen.
 */
import { fdate, fmy, ftime, zoneView, APP_TZ } from './helpers';

/** Every zone this must give the same answer in. */
const ZONES = [
  'America/Los_Angeles', // the app's own, where a naive reading passes by accident
  'UTC',
  'Asia/Kolkata',        // +05:30, the direction that shifted dates FORWARDS
  'Pacific/Kiritimati',  // +14, the largest positive offset there is
  'Australia/Adelaide',  // +09:30, a half-hour zone
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

describe('the zone itself', () => {
  test('is Pacific, named, so the DST rules come with it', () => {
    // A fixed offset would be wrong for half the year. If this is ever
    // "simplified" to a constant, the two boundary tests below go with it.
    expect(APP_TZ).toBe('America/Los_Angeles');
  });

  test('DST is actually observed, not approximated', () => {
    inEveryZone(() => {
      // Same wall-clock hour of the day, six months apart. August is PDT
      // (UTC-7), January is PST (UTC-8), so 15:00Z is 08:00 in one and 07:00
      // in the other. One offset cannot produce both.
      expect(ftime('2026-08-15T15:00:00Z')).toBe('08:00');
      expect(ftime('2026-01-15T15:00:00Z')).toBe('07:00');
    });
  });
});

describe('fdate', () => {
  test('renders the Pacific day, in every timezone the viewer might be in', () => {
    inEveryZone((tz) => {
      // 06:00Z on the 27th is 23:00 on the 26th in PDT. Inside the seven-hour
      // window where the Pacific day and the UTC day disagree, which is the
      // whole point.
      expect(fdate('2026-08-27T06:00:00Z')).toBe('26 Aug 2026');
      // And a UTC afternoon, where they agree.
      expect(fdate('2026-08-27T20:15:00Z')).toBe('27 Aug 2026');
      expect(tz).toBeTruthy();
    });
  });

  test('the day boundary is 07:00Z in summer and 08:00Z in winter', () => {
    inEveryZone(() => {
      // PDT, UTC-7.
      expect(fdate('2026-08-25T06:59:00Z')).toBe('24 Aug 2026');
      expect(fdate('2026-08-25T07:00:00Z')).toBe('25 Aug 2026');
      // PST, UTC-8. A fixed -07:00 would put the first of these on the 15th.
      expect(fdate('2026-01-15T07:59:00Z')).toBe('14 Jan 2026');
      expect(fdate('2026-01-15T08:00:00Z')).toBe('15 Jan 2026');
    });
  });

  test('a plain calendar date is not dragged into the previous day', () => {
    // THE BUG A WESTWARD ZONE WOULD OTHERWISE REINTRODUCE. '2026-08-21' is a
    // DateField: a calendar day with no instant behind it. Parsed as UTC
    // midnight and then converted to Pacific it would read 17:00 on the 20th,
    // so every date column in the CRM would render a day early. zoneView
    // returns a zone-free date untouched, which is what this pins.
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
  test('renders the Pacific clock, in every timezone', () => {
    inEveryZone(() => {
      expect(ftime('2026-08-27T20:15:00Z')).toBe('13:15');
      expect(ftime('2026-08-27T00:00:00Z')).toBe('17:00');
      // Midnight Pacific reads as 00:00 and never as 24:00 — the reason the
      // formatter asks for hourCycle 'h23' rather than hour12: false.
      expect(ftime('2026-08-27T07:00:00Z')).toBe('00:00');
      expect(ftime('2026-01-15T08:00:00Z')).toBe('00:00');
    });
  });

  test('fdate and ftime agree about which day it is', () => {
    // They are rendered side by side in the Modified Time cell
    // (pages/BookingsPage.jsx), so a disagreement would print a visible lie.
    inEveryZone(() => {
      expect(fdate('2026-08-27T06:00:00Z')).toBe('26 Aug 2026');
      expect(ftime('2026-08-27T06:00:00Z')).toBe('23:00');
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
  test('a timestamp early on the first of the month belongs to the PREVIOUS month', () => {
    // 04:00Z on 1 Aug is 21:00 on 31 Jul in PDT. Reading this as August would
    // put a row in the wrong month's bucket, which is the kind of off-by-one
    // nobody notices until two screens are reconciled.
    inEveryZone(() => {
      expect(fmy('2026-08-01T04:00:00Z')).toBe('Jul 2026');
      expect(fmy('2026-08-01T20:00:00Z')).toBe('Aug 2026');
      expect(fmy('2026-01-01T04:00:00Z')).toBe('Dec 2025');
    });
  });

  test('an unparseable value is an em dash', () => {
    inEveryZone(() => {
      expect(fmy(null)).toBe('—');
      expect(fmy('nonsense')).toBe('—');
    });
  });
});

describe('zoneView', () => {
  test('a Date and its ISO string are the same instant', () => {
    inEveryZone(() => {
      const s = '2026-08-27T06:00:00Z';
      expect(zoneView(new Date(s)).toISOString())
        .toBe(zoneView(s).toISOString());
    });
  });

  test('nothing parseable comes back as null rather than as Invalid Date', () => {
    expect(zoneView(null)).toBeNull();
    expect(zoneView('')).toBeNull();
    expect(zoneView('nonsense')).toBeNull();
  });
});
