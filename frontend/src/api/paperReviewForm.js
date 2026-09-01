// The public MRE paper review form — backend/paper_review/public_form.py.
//
// ITS OWN AXIOS INSTANCE, NOT api/client.js's `http`, and that is the whole
// reason this file exists rather than two more functions in api/paperReview.js.
// The shared client does two things that are correct everywhere else and wrong
// here:
//
//   it attaches the stored auth token to every request, which the reviewer does
//   not have and the endpoint does not read;
//
//   it treats a 401 as an expired session and calls window.location.replace(
//   '/login'). A reviewer whose link was revoked, regenerated or mistyped would
//   be thrown at a login page they have no account for, instead of being told
//   the link is not valid.
//
// No interceptors at all here, so a 401 comes back as a rejected promise the
// page renders as a message.
import axios from 'axios';

// Read through process.env member access, one variable at a time — see the note
// in api/client.js on why an intermediate alias breaks the build-time swap.
const BASE_URL = process.env.REACT_APP_API_BASE_URL || '/api/';

const publicHttp = axios.create({
  baseURL: BASE_URL,
  headers: { 'Content-Type': 'application/json' },
});

// `crm_key`, one of the backend's existing key aliases (webhooks/utils.py
// QUERY_KEY_ALIASES). The name is shared with the URL the reviewer opens, so the
// page forwards whatever it was given rather than renaming it in transit.
export const KEY_PARAM = 'crm_key';

/** What the form should render: the reviewer's name and their events. */
export const config = (key) =>
  publicHttp.get('paper-review-form/config/', { params: { [KEY_PARAM]: key } })
    .then((r) => r.data);

/** One review. 201 mints the proposal submission and queues the email. */
export const submit = (key, payload) =>
  publicHttp.post('paper-review-form/submit/', payload, { params: { [KEY_PARAM]: key } })
    .then((r) => r.data);
