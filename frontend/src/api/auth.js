import { http } from './client';

/**
 * Exchange a Google ID token for a DRF auth token.
 *
 * The primary login path. POST /api/auth/google/ returns exactly what the
 * retired token/OTP endpoints returned — token, user_id, email, username, role —
 * so SessionContext's toUser() reads it unchanged.
 *
 * @param {string} credential the JWT handed to us by Google Identity Services
 */
export function googleLogin(credential) {
  return http.post('auth/google/', { credential }).then((r) => r.data);
}

/**
 * Revoke the DRF token server-side, then let the caller clear it locally.
 *
 * Discarding the token client-side alone used to be the whole of logout, which
 * left a credential that NEVER EXPIRES valid on the server for as long as the
 * row lived. Both sign-out paths — the Topbar button and the inactivity timer in
 * components/IdleLogout.jsx — come through here, so neither can drift from the
 * other.
 *
 * Swallowing the error is deliberate: the client is dropping the token either
 * way, and a backend hiccup must not strand someone in a shell they have asked
 * to leave. The explicit timeout exists because `http` has no default one, and
 * `_retried` opts out of the client's network-error retry loop (see
 * client.js) — both so a dead backend cannot hold the sign-out open for
 * twenty seconds.
 */
export function logout() {
  return http.post('auth/logout/', null, { timeout: 5000, _retried: true })
    .catch(() => {});
}

/**
 * Emergency username/password login when Google Sign-In is unavailable.
 * Posts to the hidden fallback endpoint, which is reachable in the UI only
 * through the /170405 gate. Response shape is identical to googleLogin().
 */
export function fallbackLogin({ username, password }) {
  return http.post('auth/fallback/', { username, password }).then((r) => r.data);
}
