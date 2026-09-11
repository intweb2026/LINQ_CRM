import { http } from './client';

/** Whether the current user has a connected Gmail account, and which address. */
export const gmailStatus = () => http.get('gmail/status/').then((r) => r.data);

/**
 * {authorize_url}, the Google consent screen to send this user to.
 *
 * `returnTo` is where the browser should land when it comes back. The server
 * signs it into the OAuth state parameter and validates it as a site-relative
 * path, so it cannot be used to redirect anybody off-site.
 */
export const gmailConnectUrl = (returnTo = '') =>
  http.get('gmail/connect/', { params: returnTo ? { return_to: returnTo } : {} })
    .then((r) => r.data);

export const gmailDisconnect = () => http.post('gmail/disconnect/').then((r) => r.data);
