/**
 * lib/exportSheet.print.test.js
 * ─────────────────────────────
 * printElement, and the two things it now does to the WHOLE DOCUMENT.
 *
 * WHY THIS IS TESTED
 * Both of the new behaviours reach outside the element being printed. Forcing
 * the light palette writes `data-theme` on the root, and landscape appends a
 * `@page` rule to the head, because neither a media query nor `@page` can be
 * scoped to a class. So a cleanup that misses anything does not print wrongly,
 * it leaves the APP wrong afterwards: a dark-mode user stranded on a light
 * palette, or every later print silently turned sideways. Nothing on the page
 * says so, and the print dialog has already closed by the time anybody looks.
 *
 * jsdom has no window.print and never fires afterprint, so print is stubbed and
 * the event is dispatched by hand; the 1000ms safety net is driven by fake
 * timers rather than waited for.
 */
import { printElement } from './exportSheet';

const root = () => document.documentElement;
const pageRules = () => Array.from(document.head.querySelectorAll('style'))
  .filter((s) => s.textContent.includes('@page'));

let target;
let printed;

beforeEach(() => {
  jest.useFakeTimers();
  printed = 0;
  window.print = () => { printed += 1; };
  root().removeAttribute('data-theme');
  document.title = 'LINQ CRM';
  target = document.createElement('div');
  document.body.appendChild(target);
});

afterEach(() => {
  jest.useRealTimers();
  target.remove();
  pageRules().forEach((s) => s.remove());
  root().removeAttribute('data-theme');
});

describe('what happens while the print is open', () => {
  test('the light palette is forced, whatever the user was on', () => {
    root().setAttribute('data-theme', 'dark');
    printElement(target, 'Check-In Sheet');
    expect(root().getAttribute('data-theme')).toBe('light');
    expect(printed).toBe(1);
  });

  test('the element is marked and the rest of the page is stood down', () => {
    printElement(target, 'Check-In Sheet');
    expect(target.classList.contains('printing-now')).toBe(true);
    expect(document.body.classList.contains('printing')).toBe(true);
  });

  test('the title is the one the file and the page header will carry', () => {
    printElement(target, 'ACU 2026 check-in sheet');
    expect(document.title).toBe('ACU 2026 check-in sheet');
  });

  test('landscape injects the @page rule, and only when asked for', () => {
    printElement(target, 'Check-In Sheet', { landscape: true });
    expect(pageRules()).toHaveLength(1);
    expect(pageRules()[0].textContent).toBe('@page{size:A4 landscape;margin:12mm}');
  });

  test('portrait injects nothing, so the two narrow badge lists are left alone', () => {
    printElement(target, 'Name Badges');
    expect(pageRules()).toHaveLength(0);
  });
});

describe('afterprint puts everything back', () => {
  // The whole point of the file. Anything missed here is a bug the user meets
  // AFTER the print dialog closes, on a page that looks like it was not touched.
  const finish = () => window.dispatchEvent(new Event('afterprint'));

  test('a dark-mode user is returned to dark', () => {
    root().setAttribute('data-theme', 'dark');
    printElement(target, 'Check-In Sheet');
    finish();
    expect(root().getAttribute('data-theme')).toBe('dark');
  });

  test('somebody on the system default is left with NO attribute, not "light"', () => {
    // The branch worth spelling out. Restoring the empty string, or leaving
    // "light" behind, pins a user who had made no choice at all onto a palette
    // that then stops following their operating system.
    expect(root().hasAttribute('data-theme')).toBe(false);
    printElement(target, 'Check-In Sheet');
    finish();
    expect(root().hasAttribute('data-theme')).toBe(false);
  });

  test('the marks, the title and the @page rule all come off', () => {
    printElement(target, 'ACU 2026 check-in sheet', { landscape: true });
    finish();
    expect(target.classList.contains('printing-now')).toBe(false);
    expect(document.body.classList.contains('printing')).toBe(false);
    expect(document.title).toBe('LINQ CRM');
    expect(pageRules()).toHaveLength(0);
  });

  test('a later portrait print is not still sideways', () => {
    printElement(target, 'Check-In Sheet', { landscape: true });
    finish();
    printElement(target, 'Name Badges');
    expect(pageRules()).toHaveLength(0);
  });
});

describe('the browsers that never fire afterprint', () => {
  test('the safety net restores the theme on its own', () => {
    root().setAttribute('data-theme', 'dark');
    printElement(target, 'Check-In Sheet', { landscape: true });
    expect(root().getAttribute('data-theme')).toBe('light');
    jest.advanceTimersByTime(1000);
    expect(root().getAttribute('data-theme')).toBe('dark');
    expect(pageRules()).toHaveLength(0);
    expect(target.classList.contains('printing-now')).toBe(false);
  });

  test('cleanup twice is harmless, which is what makes the net safe to keep', () => {
    // It runs on afterprint AND on the timeout in every browser that fires
    // both, so the second pass must not undo the first or throw on a style
    // element it has already removed.
    root().setAttribute('data-theme', 'dark');
    printElement(target, 'Check-In Sheet', { landscape: true });
    window.dispatchEvent(new Event('afterprint'));
    expect(() => jest.advanceTimersByTime(1000)).not.toThrow();
    expect(root().getAttribute('data-theme')).toBe('dark');
    expect(document.title).toBe('LINQ CRM');
    expect(pageRules()).toHaveLength(0);
  });

  test('a stale afterprint from a finished print does not disturb the page', () => {
    printElement(target, 'Check-In Sheet');
    window.dispatchEvent(new Event('afterprint'));
    jest.advanceTimersByTime(1000);
    root().setAttribute('data-theme', 'dark');
    window.dispatchEvent(new Event('afterprint'));
    expect(root().getAttribute('data-theme')).toBe('dark');
  });
});

test('no element is a no-op, and nothing is printed', () => {
  root().setAttribute('data-theme', 'dark');
  printElement(null, 'Check-In Sheet', { landscape: true });
  expect(printed).toBe(0);
  expect(root().getAttribute('data-theme')).toBe('dark');
  expect(pageRules()).toHaveLength(0);
  expect(document.title).toBe('LINQ CRM');
});
