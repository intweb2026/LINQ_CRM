/**
 * api/auth.logout.test.js
 * ───────────────────────
 * Sign-out has to reach the server, at the path the server actually serves.
 *
 * WHY THIS SEAM NEEDS PINNING. logout() swallows its own errors on purpose — the
 * client is dropping the token either way, and a backend hiccup must not strand
 * someone in a shell they asked to leave. That same catch makes a WRONG URL
 * completely silent: a typo here answers 404, the promise resolves, the user is
 * signed out of the browser, and the never-expiring token stays valid on the
 * server forever with nothing anywhere reporting it. The string is checked here
 * against the one Django routes in config/urls.py, and in
 * accounts/tests_logout.py from the other side.
 */
const mockPost = jest.fn();

jest.mock('./client', () => ({
  http: { post: (...args) => mockPost(...args) },
}));

const { logout } = require('./auth');

beforeEach(() => {
  // mockReset, not mockClear: create-react-app sets resetMocks:true, which
  // strips the implementation as well as the call log.
  mockPost.mockReset().mockResolvedValue({ status: 204 });
});

test('posts to the endpoint that revokes the token', async () => {
  await logout();
  expect(mockPost).toHaveBeenCalledTimes(1);
  expect(mockPost.mock.calls[0][0]).toBe('auth/logout/');
});

test('a failed revoke still resolves, so the client can finish signing out', async () => {
  mockPost.mockRejectedValue(new Error('backend down'));
  await expect(logout()).resolves.toBeUndefined();
});

test('cannot be held open by a dead backend', async () => {
  await logout();
  const config = mockPost.mock.calls[0][2];
  expect(config.timeout).toBe(5000);
  // client.js retries network failures twice, 1.2s apart. On a sign-out that
  // would delay the redirect by up to twenty seconds; _retried opts out.
  expect(config._retried).toBe(true);
});
