import { http } from './client';

// Real backend endpoints — see backend/config/urls.py and accounts/views.py.
// Token responses do not include a display name, only username/email/role;
// SessionContext derives `name` from whatever identifier is available.

export function login({ username, password }) {
  return http.post('auth/token/', { username, password }).then((r) => r.data);
}

export function sendCode(email) {
  return http.post('auth/request-otp/', { email }).then((r) => r.data);
}

export function verifyCode(email, code) {
  return http.post('auth/verify-otp/', { email, otp: code }).then((r) => r.data);
}

// Backend has no server-side logout endpoint (DRF token auth) — token is
// simply discarded client-side.
export function logout() {
  return Promise.resolve();
}
