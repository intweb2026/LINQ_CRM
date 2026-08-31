/**
 * The inactivity clock.
 *
 * Kept as pure functions over a TIMESTAMP rather than as a `setTimeout(logout,
 * SIX_HOURS)`, for two reasons that both matter at this duration:
 *
 *  - A background tab's timers are throttled by every modern browser, and a
 *    suspended laptop stops firing them altogether. A six-hour setTimeout is
 *    therefore not a six-hour timeout; it is "six hours of the tab being awake".
 *    Comparing wall clocks means closing the lid at 17:00 and opening it at
 *    09:00 the next morning reads as sixteen idle hours, which is the whole
 *    point of the feature.
 *  - The stamp lives in localStorage, so it is SHARED BY EVERY TAB on the
 *    origin. Working in one tab keeps the others alive; without that, a second
 *    tab left open on the Dashboard would sign the user out from underneath the
 *    tab they were actually typing in.
 *
 * WARN_BEFORE_MS must stay comfortably longer than the caller's stamp throttle
 * (30s in components/IdleLogout.jsx), or an active user could be shown the
 * warning in the gap between two stamps.
 */
export const ACTIVITY_KEY = 'auth_last_active';
export const IDLE_LIMIT_MS = 6 * 60 * 60 * 1000;
export const WARN_BEFORE_MS = 2 * 60 * 1000;

/**
 * 'active' | 'warn' | 'expired' for a session last active at `lastActive`.
 *
 * A negative gap, from a clock correction or a stamp written by a tab whose
 * clock runs ahead, reads as 'active'; the only safe direction for a bad clock
 * is to keep the user signed in and let the next honest stamp settle it.
 */
export function idlePhase(lastActive, now) {
  const idleFor = now - lastActive;
  if (idleFor >= IDLE_LIMIT_MS) return 'expired';
  if (idleFor >= IDLE_LIMIT_MS - WARN_BEFORE_MS) return 'warn';
  return 'active';
}

/** The shared stamp, or null when storage is unavailable or holds junk. */
export function readLastActive() {
  try {
    const v = parseInt(localStorage.getItem(ACTIVITY_KEY), 10);
    return Number.isFinite(v) ? v : null;
  } catch {
    return null;
  }
}

export function stampActive(at) {
  try { localStorage.setItem(ACTIVITY_KEY, String(at)); } catch {}
}

export function clearActivity() {
  try { localStorage.removeItem(ACTIVITY_KEY); } catch {}
}
