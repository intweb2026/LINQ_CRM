/**
 * The inactivity boundaries. The whole feature turns on these three answers, and
 * they are the one part of it that can be checked without a browser, a clock or
 * six hours of waiting.
 */
import { idlePhase, IDLE_LIMIT_MS, WARN_BEFORE_MS, readLastActive, stampActive, clearActivity } from './idle';

const NOW = 1_700_000_000_000;
const at = (idleFor) => idlePhase(NOW - idleFor, NOW);

describe('idlePhase', () => {
  test('a user who just moved the mouse is active', () => {
    expect(at(0)).toBe('active');
    expect(at(60 * 1000)).toBe('active');
  });

  test('active right up to the moment the warning is due', () => {
    expect(at(IDLE_LIMIT_MS - WARN_BEFORE_MS - 1)).toBe('active');
  });

  test('warns for the last WARN_BEFORE_MS and no earlier', () => {
    expect(at(IDLE_LIMIT_MS - WARN_BEFORE_MS)).toBe('warn');
    expect(at(IDLE_LIMIT_MS - 1)).toBe('warn');
  });

  test('expires exactly at the limit, and stays expired', () => {
    expect(at(IDLE_LIMIT_MS)).toBe('expired');
    expect(at(IDLE_LIMIT_MS * 4)).toBe('expired');
  });

  // A stamp from the future (clock correction, or another tab running fast) must
  // not read as expired — see the note in idle.js.
  test('a future stamp is active, not expired', () => {
    expect(at(-60 * 60 * 1000)).toBe('active');
  });
});

describe('the shared stamp', () => {
  afterEach(clearActivity);

  test('round-trips', () => {
    stampActive(NOW);
    expect(readLastActive()).toBe(NOW);
  });

  test('junk and absence both read as null, so the caller re-stamps', () => {
    expect(readLastActive()).toBeNull();
    localStorage.setItem('auth_last_active', 'not-a-number');
    expect(readLastActive()).toBeNull();
  });
});
