/**
 * components/IdleLogout.test.js
 * ─────────────────────────────
 * The whole inactivity flow, end to end: quiet, warning, reset, sign-out.
 *
 * WHY THIS ONE RENDERS, when the rest of the suite tests plain functions.
 * The boundaries live in lib/idle.test.js, and a reducer test would repeat them.
 * What cannot be proved that way is the wiring, which is where every real bug in
 * a feature like this hides: a `scroll` listener that misses because scroll does
 * not bubble, a throttle that swallows the reset, a warning that never clears, a
 * sign-out that fires twice. So the component is mounted for real and driven
 * with real DOM events.
 *
 * TIME IS MOVED WITH setSystemTime, NOT advanceTimersByTime, on purpose. Jumping
 * the clock and then letting ONE tick run is exactly what a laptop waking from
 * six hours of sleep does to this component, and it is the case a
 * setTimeout-based implementation gets wrong. It also keeps the test instant
 * instead of stepping a 1s interval through 21,600 iterations.
 */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { ACTIVITY_KEY, IDLE_LIMIT_MS, WARN_BEFORE_MS, readLastActive } from '../lib/idle';

const mockLogout = jest.fn(() => Promise.resolve());
jest.mock('../context/SessionContext', () => ({ useSession: () => ({ logout: mockLogout }) }));
jest.mock('react-router-dom', () => ({ useLocation: () => ({ pathname: '/bookings' }) }));

import IdleLogout from './IdleLogout';

global.IS_REACT_ACT_ENVIRONMENT = true;

const START = 1_700_000_000_000;

let container, root, replace;

/**
 * Put the wall clock at START + ms and let exactly one poll tick run there.
 *
 * The jump is one second short because advanceTimersByTime moves the clock too:
 * setSystemTime alone fires nothing, and the tick has to land ON the moment
 * under test, not a second past it — a second matters when the assertion is a
 * countdown reading '2:00'.
 */
function skipAhead(ms) {
  act(() => {
    jest.setSystemTime(START + ms - 1000);
    jest.advanceTimersByTime(1000);
  });
}

const warningText = () => container.textContent;
const isWarning = () => /signed out in/.test(container.textContent);

beforeEach(() => {
  jest.useFakeTimers();
  jest.setSystemTime(START);
  // CRA's jest config sets resetMocks, which drops the implementation as well
  // as the call log, so the promise has to be handed back every time.
  mockLogout.mockReset().mockImplementation(() => Promise.resolve());
  localStorage.clear();
  replace = jest.fn();
  Object.defineProperty(window, 'location', {
    value: { replace }, writable: true, configurable: true,
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  act(() => {
    root = createRoot(container);
    root.render(<IdleLogout />);
  });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  jest.useRealTimers();
});

test('mounting stamps the session, so a stamp left by an earlier one cannot expire it', () => {
  expect(readLastActive()).toBe(START);
});

test('nothing at all happens to a user who is working', () => {
  for (let minute = 1; minute <= 90; minute++) {
    skipAhead(minute * 60 * 1000);
    act(() => { window.dispatchEvent(new MouseEvent('mousemove')); });
  }
  expect(isWarning()).toBe(false);
  expect(mockLogout).not.toHaveBeenCalled();
  expect(readLastActive()).toBe(START + 90 * 60 * 1000);
});

test('the warning appears with two minutes left and counts down', () => {
  skipAhead(IDLE_LIMIT_MS - WARN_BEFORE_MS);
  expect(isWarning()).toBe(true);
  expect(warningText()).toMatch('2:00');

  skipAhead(IDLE_LIMIT_MS - WARN_BEFORE_MS + 61 * 1000);
  expect(warningText()).toMatch('0:59');
  expect(mockLogout).not.toHaveBeenCalled();
});

test('any activity during the warning cancels it and restarts the six hours', () => {
  skipAhead(IDLE_LIMIT_MS - WARN_BEFORE_MS);
  expect(isWarning()).toBe(true);

  act(() => { window.dispatchEvent(new MouseEvent('mousemove')); });
  expect(isWarning()).toBe(false);
  expect(readLastActive()).toBe(START + IDLE_LIMIT_MS - WARN_BEFORE_MS);

  // ...and the clock really did restart: the moment that used to be expiry now
  // sits well inside the new window.
  skipAhead(IDLE_LIMIT_MS);
  expect(mockLogout).not.toHaveBeenCalled();
});

test('scrolling counts, though a scroll event does not bubble', () => {
  skipAhead(IDLE_LIMIT_MS - WARN_BEFORE_MS);
  // Dispatched on a node inside the page, non-bubbling: only the capture-phase
  // window listener can see this. #main is the app's scroller, so a user reading
  // a long table produces exactly this and nothing else.
  act(() => { container.dispatchEvent(new Event('scroll')); });
  expect(isWarning()).toBe(false);
});

test('typing counts', () => {
  skipAhead(IDLE_LIMIT_MS - WARN_BEFORE_MS);
  act(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a' })); });
  expect(isWarning()).toBe(false);
});

test('another tab being used keeps this one signed in', () => {
  skipAhead(IDLE_LIMIT_MS - WARN_BEFORE_MS);
  expect(isWarning()).toBe(true);

  // The shared stamp is how tabs see each other. This is the other tab writing.
  act(() => { localStorage.setItem(ACTIVITY_KEY, String(Date.now())); });
  skipAhead(IDLE_LIMIT_MS - WARN_BEFORE_MS + 1000);
  expect(isWarning()).toBe(false);
  expect(mockLogout).not.toHaveBeenCalled();
});

test('six idle hours signs the user out and sends them to the login page', async () => {
  skipAhead(IDLE_LIMIT_MS);
  expect(mockLogout).toHaveBeenCalledTimes(1);
  // logout() revokes the token before the redirect; flush it.
  await act(async () => {});
  expect(replace).toHaveBeenCalledWith('/login');
});

test('signs out exactly once, however many ticks follow', async () => {
  skipAhead(IDLE_LIMIT_MS);
  skipAhead(IDLE_LIMIT_MS + 5 * 60 * 1000);
  await act(async () => {});
  expect(mockLogout).toHaveBeenCalledTimes(1);
  expect(replace).toHaveBeenCalledTimes(1);
});

describe('another tab signing out', () => {
  const tokenRemoved = () => new StorageEvent('storage', {
    key: 'auth_token', oldValue: 'abc123', newValue: null,
  });

  test('takes this tab to the login page too', () => {
    act(() => { window.dispatchEvent(tokenRemoved()); });
    expect(replace).toHaveBeenCalledWith('/login');
  });

  test('but an unrelated key does not', () => {
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'iqhub_theme', oldValue: 'light', newValue: 'dark',
      }));
      // A tab signing IN writes a token rather than clearing one.
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'auth_token', oldValue: null, newValue: 'fresh',
      }));
    });
    expect(replace).not.toHaveBeenCalled();
  });
});

test('a wiped stamp fails open: the working user stays signed in', () => {
  act(() => { localStorage.removeItem(ACTIVITY_KEY); });
  skipAhead(60 * 1000);
  expect(mockLogout).not.toHaveBeenCalled();
  expect(readLastActive()).toBe(START + 60 * 1000);
});
