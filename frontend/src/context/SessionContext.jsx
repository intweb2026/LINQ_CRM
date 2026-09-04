import { createContext, useContext, useState, useCallback, useEffect, useMemo } from 'react';
import * as authApi from '../api/auth';
import { myPermissions } from '../api/users';
import { markTokenFreshness } from '../api/client';
import { ROLE_FULL, ALL_MODULES } from '../lib/constants';
import { clearActivity } from '../lib/idle';

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

// The Google login response only carries username/email/role — the backend has
// no display-name field on this endpoint — so `name` falls back to whichever
// identifier is available.
function toUser(data) {
  const username = data.username || data.email;
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

  /**
   * The one and only login path: swap a Google ID token for a DRF token.
   *
   * Same body as the retired password/OTP callbacks — the backend returns an
   * identical payload, so toUser() and the perms resolution are untouched. The
   * ID token itself is never stored; only the DRF token that comes back is.
   */
  const loginWithGoogle = useCallback(async (credential) => {
    const data = await authApi.googleLogin(credential);
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

  /**
   * Emergency break-glass login. Same steps as loginWithGoogle — store token,
   * resolve permissions, set user — only the credential differs. Reachable
   * from the hidden /170405 gate, never from the main login page.
   */
  const loginWithFallback = useCallback(async (credentials) => {
    const data = await authApi.fallbackLogin(credentials);
    storageSet('auth_token', data.token);
    markTokenFreshness();
    const userInfo = toUser(data);
    const permsData = await resolvePerms(userInfo.role);
    storageSet('auth_user', JSON.stringify(userInfo));
    if (permsData) storageSet('auth_perms', JSON.stringify(permsData));
    setUser(userInfo);
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
    clearActivity();
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

  /**
   * Whether this session is an administrator, in the SAME sense the server means.
   *
   * backend/accounts/permissions.py:IsAdminRole passes on `is_admin` OR a team
   * flagged `has_all_access`, and that class guards whole surfaces which no
   * module grant can open — the Performance Matrix is one. A frontend gate reading
   * only `user.role === 'admin'` would hide those surfaces from an all-access
   * team the server would happily serve, so both halves are checked here, once,
   * rather than re-derived per page.
   *
   * This is NOT a module and must never be treated as one: it cannot be granted
   * or revoked from the Permissions grid. See `adminOnly` in lib/nav.js.
   */
  const isAdmin = useMemo(
    () => user?.role === 'admin' || !!perms?.is_all_access,
    [user, perms],
  );

  /**
   * The team this session MANAGES, or null.
   *
   * Comes off /api/users/my-permissions/ rather than off the user row, because
   * that endpoint is the one thing every session already fetches and it is the
   * server's own answer — the same helper that decides whether a write is
   * allowed decides what goes in this field, so the UI cannot show an affordance
   * the API will then refuse.
   *
   * Null for a super admin even when they hold the column: a super admin is not
   * restricted to one team, and treating them as a manager here would narrow
   * their Users page to it. Mirrors managed_team_id() in
   * backend/accounts/permissions.py.
   *
   * A GATE, NOT A GRANT. Nothing here decides what a manager may do — the module
   * matrix already carries that, granted server-side in
   * User.effective_permissions(). This only says WHICH TEAM, so the pages can
   * narrow their rows and pin their forms.
   */
  const managedTeam = useMemo(() => {
    if (isAdmin || !perms?.managed_team_id) return null;
    return { id: perms.managed_team_id, name: perms.managed_team_name || 'your team' };
  }, [isAdmin, perms]);

  const value = useMemo(() => ({
    user, perms, permsLoaded, loginWithGoogle, loginWithFallback, logout, canView, can, isAdmin,
    managedTeam,
    roleLabel: user ? ROLE_FULL[user.role] || user.role : '',
  }), [user, perms, permsLoaded, loginWithGoogle, loginWithFallback, logout, canView, can, isAdmin, managedTeam]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession() {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error('useSession must be used within SessionProvider');
  return ctx;
}
