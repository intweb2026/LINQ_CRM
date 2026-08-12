import { createContext, useContext, useState, useCallback, useEffect, useMemo } from 'react';
import * as authApi from '../api/auth';
import { myPermissions } from '../api/users';
import { markTokenFreshness } from '../api/client';
import { ROLE_FULL, ALL_MODULES } from '../lib/constants';

const SessionContext = createContext(null);

function storageGet(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function storageSet(key, value) {
  try { localStorage.setItem(key, value); } catch {}
}
function storageRemove(key) {
  try { localStorage.removeItem(key); } catch {}
}

function fullAccess() {
  const modules = {};
  ALL_MODULES.forEach((m) => { modules[m] = { view: true, create: true, update: true, delete: true }; });
  return { is_all_access: true, modules };
}

/**
 * The matrix used when permissions cannot be resolved at all.
 *
 * `permsLoaded` used to flip true while `perms` stayed null, because
 * resolvePerms() returns null on failure. RequireAuth gates only on
 * `permsLoaded`, so the route then rendered against a null matrix and any page
 * reading `perms.is_all_access` threw during render. Verified on a non-admin
 * session with my-permissions failing:
 *   "TypeError: Cannot read properties of null (reading 'is_all_access')"
 *   at DashboardPage
 * Admins never reach it because resolvePerms short-circuits to fullAccess() for
 * role === 'admin' without calling the endpoint — which is exactly why this
 * stayed invisible under admin testing.
 *
 * Denying is the right default on failure: the user gets the No Access page per
 * module and a reload retries, instead of the app dying. It is also the safe
 * direction — a failed permission lookup must never read as permission granted.
 */
function denyAll() {
  return { is_all_access: false, modules: {} };
}

/**
 * A well-shaped permission matrix, or null when the cache is unusable.
 *
 * `auth_perms` survives across deploys, so what comes back may predate the
 * current shape, or be a truncated write from a tab that was closed mid-save.
 * Returning null for anything malformed makes the app REFETCH rather than run on
 * it — which is the difference between a one-request recovery and a session
 * spent denying every module.
 */
function readCachedPerms() {
  let parsed;
  try {
    parsed = JSON.parse(storageGet('auth_perms') || 'null');
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== 'object') return null;
  // is_all_access alone is sufficient — such a matrix needs no module map.
  if (parsed.is_all_access === true) return parsed;
  if (!parsed.modules || typeof parsed.modules !== 'object') return null;
  return parsed;
}

// Login/OTP responses only carry username/email/role — the backend has no
// display-name field on these endpoints — so `name` falls back to whichever
// identifier is available.
function toUser(data, fallbackUsername) {
  const username = data.username || fallbackUsername || data.email;
  return {
    user_id: data.user_id,
    username,
    email: data.email,
    role: data.role || 'sales',
    name: username,
  };
}

export function SessionProvider({ children }) {
  const [user, setUser] = useState(() => {
    try { return JSON.parse(storageGet('auth_user') || 'null'); } catch { return null; }
  });
  // A cached matrix is only trusted if it is actually shaped like one. Otherwise
  // permsLoaded would flip true off a malformed blob, skipping the refetch AND
  // feeding that blob to every canView() call for the rest of the session.
  const [perms, setPerms] = useState(() => readCachedPerms());
  const [permsLoaded, setPermsLoaded] = useState(() => readCachedPerms() !== null);

  const resolvePerms = useCallback(async (role) => {
    if (role === 'admin') return fullAccess();
    try {
      return (await myPermissions()) || null;
    } catch {
      return null;
    }
  }, []);

  // Page-refresh rehydration: re-fetch permissions for an already-logged-in
  // session (token + user are already in localStorage/state).
  useEffect(() => {
    if (!user || permsLoaded) return;
    let cancelled = false;
    resolvePerms(user.role).then((data) => {
      if (cancelled) return;
      if (data) {
        setPerms(data);
        storageSet('auth_perms', JSON.stringify(data));
      } else {
        // Never leave perms null once permsLoaded is true — see denyAll().
        // Not persisted: a transient failure must not cache "no access" for the
        // next reload, which would make the outage sticky.
        setPerms(denyAll());
      }
      setPermsLoaded(true);
    });
    return () => { cancelled = true; };
  }, [user, permsLoaded, resolvePerms]);

  const login = useCallback(async (credentials) => {
    const data = await authApi.login(credentials);
    storageSet('auth_token', data.token);
    markTokenFreshness();
    const userInfo = toUser(data, credentials.username);
    const permsData = await resolvePerms(userInfo.role);
    storageSet('auth_user', JSON.stringify(userInfo));
    if (permsData) storageSet('auth_perms', JSON.stringify(permsData));
    setUser(userInfo);
    // denyAll() rather than null: permsLoaded is about to become true, and every
    // page reads this matrix during render. See denyAll().
    setPerms(permsData || denyAll());
    setPermsLoaded(true);
    return userInfo;
  }, [resolvePerms]);

  const loginWithCode = useCallback(async (email, code) => {
    const data = await authApi.verifyCode(email, code);
    storageSet('auth_token', data.token);
    markTokenFreshness();
    const userInfo = toUser(data);
    const permsData = await resolvePerms(userInfo.role);
    storageSet('auth_user', JSON.stringify(userInfo));
    if (permsData) storageSet('auth_perms', JSON.stringify(permsData));
    setUser(userInfo);
    // denyAll() rather than null: permsLoaded is about to become true, and every
    // page reads this matrix during render. See denyAll().
    setPerms(permsData || denyAll());
    setPermsLoaded(true);
    return userInfo;
  }, [resolvePerms]);

  const logout = useCallback(async () => {
    await authApi.logout();
    storageRemove('auth_token');
    storageRemove('auth_user');
    storageRemove('auth_perms');
    storageRemove('auth_token_set_at');
    setUser(null);
    setPerms(null);
    setPermsLoaded(false);
  }, []);

  // `perms.modules` is read defensively rather than assumed. These two run during
  // render in Sidebar, AppShell and every page, so a throw here is not a degraded
  // permission check — it unmounts the whole tree and the app renders a blank
  // page with no shell. That is reachable from ordinary causes: `auth_perms` is
  // read back from localStorage, so any partially-written, truncated or
  // older-shape blob left by a previous deploy arrives here as a truthy object
  // with no `modules` key. Verified: a cached `{}` produced
  // "TypeError: Cannot read properties of undefined (reading 'bookings')" inside
  // Sidebar and left #root empty.
  const canView = useCallback((mod) => {
    if (!perms) return false;
    if (perms.is_all_access) return true;
    const m = perms.modules && perms.modules[mod];
    return !!(m && m.view);
  }, [perms]);

  const can = useCallback((action, mod) => {
    if (!perms) return false;
    if (perms.is_all_access) return true;
    const m = perms.modules && perms.modules[mod];
    return !!(m && m[action]);
  }, [perms]);

  const value = useMemo(() => ({
    user, perms, permsLoaded, login, loginWithCode, logout, canView, can,
    roleLabel: user ? ROLE_FULL[user.role] || user.role : '',
  }), [user, perms, permsLoaded, login, loginWithCode, logout, canView, can]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession() {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error('useSession must be used within SessionProvider');
  return ctx;
}
