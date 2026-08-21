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

// Backend has no server-side logout endpoint (DRF token auth) — the token is
// simply discarded client-side.
export function logout() {
  return Promise.resolve();
}

/**
 * Emergency username/password login when Google Sign-In is unavailable.
 * Posts to the hidden fallback endpoint, which is reachable in the UI only
 * through the /170405 gate. Response shape is identical to googleLogin().
 */
export function fallbackLogin({ username, password }) {
  return http.post('auth/fallback/', { username, password }).then((r) => r.data);
}
