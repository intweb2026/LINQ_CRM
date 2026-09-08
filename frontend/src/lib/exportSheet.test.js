/**
 * lib/exportSheet.test.js
 * ───────────────────────
 * The layout of the workbooks Pre-Event Docs sends out.
 *
 * WHY THIS IS TESTED
 * These files leave the building. They go to the badge printer and to the desk
 * two days before an event, and the only way anybody finds out that a column
 * moved, that the title band swallowed the first delegate, or that "Upcoming
 * Events" stopped spanning its three columns, is by opening the file that was
 * already emailed. Nothing on screen changes when a merge is wrong.
 *
 * The expected shape is taken from the real deliverables, "WSE 26 - Checkin
 * Sheet.xlsx" and "WSE 26 - Additional Name Badge.xlsx": title over rows 1-2,
 * an optional band on row 3, the header, then the data.
 */
import { buildSheet, sheetTitle, fileName } from './exportSheet';

// The check-in sheet's columns, as PreEventDocsPage declares them.
const CHECKIN_COLS = [
  ['name', 'Full Name'], ['company', 'Company Name'], ['_in', 'IN?'],
  ['booking_code_label', 'Booking Code'], ['payment_status_label', 'Payment Status'],
  ['_up0', 'WTTE 26', 'Upcoming Events'],
  ['_up1', 'WSU 27', 'Upcoming Events'],
  ['_up2', 'WSE 27', 'Upcoming Events'],
];
const BADGE_COLS = [['name', 'Full Name'], ['company', 'Company Name']];

const cell = (sheet, ref) => (sheet[ref] === undefined ? undefined : sheet[ref].v);
const merged = (sheet, from, to) => sheet['!merges'].some(
  (m) => `${m.s.r},${m.s.c},${m.e.r},${m.e.c}` === `${from},${to}`,
);

describe('the title band', () => {
  test('the title spans every column over rows 1 and 2, and the data starts below it', () => {
    const sheet = buildSheet(
      [{ name: 'Tom Simon', company: 'ABT Drains' }],
      BADGE_COLS,
      { title: 'WSE - MP 2026: Name Badges' },
    );
    expect(cell(sheet, 'A1')).toBe('WSE - MP 2026: Name Badges');
    // Rows 1-2 merged across both columns, so nothing is hidden under it.
    expect(merged(sheet, '0,0', '1,1')).toBe(true);
    expect(cell(sheet, 'A3')).toBe('Full Name');
    expect(cell(sheet, 'A4')).toBe('Tom Simon');
    expect(cell(sheet, 'B4')).toBe('ABT Drains');
  });

  test('no title means no band, and the header is row 1', () => {
    const sheet = buildSheet([{ name: 'Tom Simon' }], BADGE_COLS);
    expect(cell(sheet, 'A1')).toBe('Full Name');
    expect(cell(sheet, 'A2')).toBe('Tom Simon');
    expect(sheet['!merges']).toEqual([]);
  });
});

describe('the section band', () => {
  test('TO PRINT sits on row 3, merged, with the header under it', () => {
    const sheet = buildSheet(
      [{ name: 'Adrie Robbeson', company: 'Wioniq Benelux B.V', remark: 'New Badge' }],
      [...BADGE_COLS, ['remark', 'Remarks']],
      { title: 'WSE - MP 2026: Additional Name Badges', section: 'TO PRINT (New, Name Changes, Company Changes)' },
    );
    expect(cell(sheet, 'A3')).toBe('TO PRINT (New, Name Changes, Company Changes)');
    expect(merged(sheet, '2,0', '2,2')).toBe(true);
    expect(cell(sheet, 'A4')).toBe('Full Name');
    expect(cell(sheet, 'C4')).toBe('Remarks');
    expect(cell(sheet, 'C5')).toBe('New Badge');
  });
});

describe('the two-row header', () => {
  const sheet = buildSheet(
    [{
      name: 'Sam Briscoe', company: 'Stomor Ltd.', booking_code_label: 'Speaker',
      payment_status_label: 'Payment to collect on-site',
    }],
    CHECKIN_COLS,
    { title: 'WSE - MP 2026: Check-In Sheet' },
  );

  test('Upcoming Events spans its three columns and the codes sit under it', () => {
    expect(cell(sheet, 'F3')).toBe('Upcoming Events');
    expect(merged(sheet, '2,5', '2,7')).toBe(true);
    expect(cell(sheet, 'F4')).toBe('WTTE 26');
    expect(cell(sheet, 'G4')).toBe('WSU 27');
    expect(cell(sheet, 'H4')).toBe('WSE 27');
    // Only the top-left of a merge holds the label, as the delivered file has
    // it. Repeating it across the covered cells is what Excel discards.
    expect(cell(sheet, 'G3')).toBe('');
    expect(cell(sheet, 'H3')).toBe('');
  });

  test('an ungrouped column carries one label over both header rows', () => {
    expect(cell(sheet, 'A3')).toBe('Full Name');
    expect(cell(sheet, 'A4')).toBe('');
    expect(merged(sheet, '2,0', '3,0')).toBe(true);
    expect(merged(sheet, '2,4', '3,4')).toBe(true);
  });

  test('the data starts on row 5, and a key the row lacks is a blank cell', () => {
    expect(cell(sheet, 'A5')).toBe('Sam Briscoe');
    expect(cell(sheet, 'D5')).toBe('Speaker');
    expect(cell(sheet, 'E5')).toBe('Payment to collect on-site');
    // IN? and the three upcoming columns are the desk's to fill in by hand.
    expect(cell(sheet, 'C5')).toBe('');
    expect(cell(sheet, 'F5')).toBe('');
    expect(cell(sheet, 'H5')).toBe('');
  });

  test('an unnamed upcoming event still gets its column', () => {
    // WSE - MP names none in the catalogue. The header goes blank, the column
    // does not disappear, or the desk has nowhere to write.
    const bare = buildSheet([{ name: 'Tom Simon' }], [
      ...BADGE_COLS,
      ['_up0', '', 'Upcoming Events'], ['_up1', '', 'Upcoming Events'],
      ['_up2', '', 'Upcoming Events'],
    ]);
    expect(cell(sheet, 'H4')).toBe('WSE 27');
    expect(merged(bare, '0,2', '0,4')).toBe(true);
    expect(cell(bare, 'E2')).toBe('');
  });
});

describe('the names on the file and in the band', () => {
  test('the event name after the em dash is dropped from both', () => {
    const label = 'WSE - MP 2026 — Stormwater Europe 2026';
    expect(sheetTitle(label, 'Check-In Sheet')).toBe('WSE - MP 2026: Check-In Sheet');
    expect(fileName(label, 'check-in sheet')).toMatch(/^WSE - MP 2026 check-in sheet \d{4}-\d\d-\d\d$/);
  });

  test('no event still names the file something openable', () => {
    expect(sheetTitle(null, 'Name Badges')).toBe('Event: Name Badges');
    expect(fileName(null, 'name badges')).toMatch(/^event name badges /);
  });
});
