/**
 * components/DataTable.loading.test.js
 * ────────────────────────────────────
 * "No records" must mean the server said none, never that it has not answered.
 *
 * A client-mode table is handed `rows` and nothing else, so an unanswered fetch
 * and an empty result reach it as the same empty array — and it used to call
 * that empty. The Mining Matrix, the Performance Matrix and Credit Control each
 * rendered "No Events Found" for the length of their first request, and again
 * on every tab switch, because each view remounts. A reader who acts on that is
 * acting on a wrong answer, which is worse than waiting for a right one.
 *
 * THIS RENDERS rather than testing a function, because the defect is a BRANCH
 * ORDER and there is nothing else to point a test at: the empty state and the
 * skeleton are two arms of one ternary, and the only thing that keeps them
 * straight is which arm is written first. A reordering that reintroduces the
 * bug compiles, lints and passes every other test in this suite.
 */
import { act } from 'react';
import { createRoot } from 'react-dom/client';

jest.mock('../context/SessionContext', () => ({
  useSession: () => ({ user: { username: 'test' }, can: () => true, canView: () => true }),
}));
jest.mock('../context/ToastContext', () => ({ useToast: () => () => {} }));
jest.mock('../hooks/useLiveData', () => ({ __esModule: true, default: () => ({ refreshNow: () => {} }) }));

import DataTable from './DataTable';

global.IS_REACT_ACT_ENVIRONMENT = true;
// jsdom ships neither. The virtualiser measures the scroll box with one and the
// infinite-scroll sentinel watches with the other; both are height arithmetic
// this test has no opinion about, and a stub that observes nothing leaves the
// rows fully rendered, which is what the assertions read.
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
global.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };

const COLS = [
  { key: 'event_code', label: 'Event code' },
  { key: 'unmined_data', label: 'Unmined data', num: true },
];

let container, root;

function mount(props) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => { root.render(<DataTable cols={COLS} noun="events" {...props} />); });
}

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  localStorage.clear();
});

test('a table still waiting on its first fetch draws a skeleton, NOT "No Events Found"', () => {
  mount({ rows: [], loading: true });
  expect(container.querySelector('.dt-sk')).not.toBeNull();
  expect(container.textContent).not.toMatch(/No Events Found/i);
});

test('the skeleton says it is busy, so the wait is not silent to a screen reader', () => {
  mount({ rows: [], loading: true });
  const box = container.querySelector('.dt-sk');
  expect(box.getAttribute('aria-busy')).toBe('true');
  expect(box.getAttribute('aria-label')).toMatch(/events/);
});

test('an answered fetch that really is empty still says so', () => {
  mount({ rows: [], loading: false });
  expect(container.querySelector('.dt-sk')).toBeNull();
  expect(container.textContent).toMatch(/No Events Found/i);
});

test('rows already on screen are kept and marked stale, never replaced by the skeleton', () => {
  mount({ rows: [{ id: 1, event_code: 'AFS', unmined_data: 12 }], loading: true });
  expect(container.querySelector('.dt-sk')).toBeNull();
  expect(container.textContent).toMatch(/AFS/);
  // .dt-busy is the sweep line over live rows; it must not be the old dimming.
  expect(container.querySelector('.dt-busy')).not.toBeNull();
});

test('a settled table with rows carries no busy marker at all', () => {
  mount({ rows: [{ id: 1, event_code: 'AFS', unmined_data: 12 }], loading: false });
  expect(container.querySelector('.dt-busy')).toBeNull();
  expect(container.textContent).toMatch(/AFS/);
});
