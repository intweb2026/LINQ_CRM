// Export a list of rows to a real file, and print one.
//
// EXCEL USES THE LIBRARY THAT IS ALREADY HERE. `xlsx` is a dependency because
// lib/importParse.js reads .xlsx uploads with it, so writing one costs no new
// package. This is genuine .xlsx, not a CSV with the extension changed, which
// Excel warns about on open.
//
// PDF IS THE BROWSER'S OWN PRINT DIALOG, not a PDF library. Save as PDF is in
// every print dialog on every platform the desk uses, it renders the real
// stylesheet rather than a second layout that would drift from it, and it is
// the same button somebody wanting paper reaches for anyway. A jspdf-shaped
// dependency would add a second definition of every one of these tables.
import * as XLSX from 'xlsx';

/**
 * Build the worksheet, laid out as the workbook the desk actually sends out.
 *
 * `cols` is [[key, header], ...], or [[key, header, group], ...], in the order
 * they should appear, so the sheet carries the columns the desk expects rather
 * than whatever order the API happened to serialise, and a key the row does not
 * have becomes a blank cell instead of the word undefined.
 *
 * THE SHAPE IS COPIED FROM THE REAL DELIVERABLES, the two files that go out two
 * days before an event, "WSE 26 - Checkin Sheet.xlsx" and "WSE 26 - Additional
 * Name Badge.xlsx":
 *
 *   rows 1-2   the title, merged over every column, "WSE - MP 2026: Check-In Sheet"
 *   row 3      an optional section band, merged the same way, "TO PRINT (...)"
 *   row 4      the headers; columns sharing a `group` have that label merged
 *              across them, with their own headers on the row below
 *   row 5+     the data, in the order the caller sorted it
 *
 * Split out from toExcel so the layout can be asserted without triggering a
 * download; see exportSheet.test.js.
 *
 * NO COLOURS, NO BOLD, NO BORDERS, and that is a limit rather than a choice.
 * The delivered files carry a navy title band, a pale header and zebra striped
 * rows. The community build of `xlsx` writes values, merges and widths, and
 * SILENTLY IGNORES cell styles, which are a paid feature of that library. What
 * the desk and the badge printer read off is the structure, so the structure is
 * matched exactly and the colour is left to whoever opens the file.
 * ponytail: plain cells. Colour needs xlsx-js-style in place of xlsx, or the
 * write moved to openpyxl on the server, and neither is worth a second
 * spreadsheet library until somebody asks for the branding.
 */
export function buildSheet(rows, cols, { title, section } = {}) {
  const lastCol = cols.length - 1;
  const grouped = cols.some(([, , group]) => group);
  const aoa = [];
  const merges = [];

  if (title) {
    // Two rows tall, as both delivered files have it.
    merges.push({ s: { r: 0, c: 0 }, e: { r: 1, c: lastCol } });
    aoa.push([title], []);
  }
  if (section) {
    merges.push({ s: { r: aoa.length, c: 0 }, e: { r: aoa.length, c: lastCol } });
    aoa.push([section]);
  }

  const headRow = aoa.length;
  const topRow = cols.map(([, header, group]) => group || header);
  if (grouped) {
    // A column with no group carries one label over both header rows.
    cols.forEach(([, , group], c) => {
      if (!group) merges.push({ s: { r: headRow, c }, e: { r: headRow + 1, c } });
    });
    // A run of adjacent columns sharing a group becomes one spanning cell, and
    // ONLY ITS TOP-LEFT HOLDS THE LABEL. That is what Excel itself keeps when a
    // range is merged by hand, and writing it into the covered cells too left
    // "Upcoming Events" sitting in G3 and H3 under the merge, where the real
    // sheet has them empty.
    for (let c = 0; c <= lastCol;) {
      const group = cols[c][2];
      let end = c;
      while (end < lastCol && cols[end + 1][2] === group) end += 1;
      if (group && end > c) {
        merges.push({ s: { r: headRow, c }, e: { r: headRow, c: end } });
        for (let k = c + 1; k <= end; k += 1) topRow[k] = '';
      }
      c = end + 1;
    }
    aoa.push(topRow, cols.map(([, header, group]) => (group ? header : '')));
  } else {
    aoa.push(topRow);
  }

  for (const row of rows) {
    aoa.push(cols.map(([key]) => {
      const v = row[key];
      return v === null || v === undefined ? '' : v;
    }));
  }

  const sheet = XLSX.utils.aoa_to_sheet(aoa);
  sheet['!merges'] = merges;
  // Column widths from the content, so nothing lands as ####. Capped, because
  // one long company name should not push every other column off the page. A
  // group label is not counted: it spans its columns rather than sitting in one.
  sheet['!cols'] = cols.map(([key, header]) => ({
    wch: Math.min(46, Math.max(
      String(header).length + 2,
      ...rows.map((r) => String(r[key] ?? '').length + 2),
    )),
  }));
  return sheet;
}

/** Write `rows` to an .xlsx file and hand it to the browser. */
export function toExcel(rows, cols, filename, sheetName = 'Sheet1', opts = {}) {
  const book = XLSX.utils.book_new();
  // Excel refuses a sheet name over 31 characters or containing : \ / ? * [ ].
  XLSX.utils.book_append_sheet(book, buildSheet(rows, cols, opts),
    sheetName.replace(/[:\\/?*[\]]/g, '-').slice(0, 31));
  XLSX.writeFile(book, filename.endsWith('.xlsx') ? filename : `${filename}.xlsx`);
}

/**
 * Print one element, and nothing else on the page.
 *
 * Marks the element and lets the stylesheet do the rest — see the
 * [pre_event_docs_print] section in styles/components.css, which hides
 * everything outside `.printing-now` at print time. The class is removed on the
 * afterprint event rather than straight after window.print(), because print() is
 * synchronous in some browsers and deferred in others, and removing it too early
 * prints a blank page in the deferred ones.
 */
/**
 * `landscape` is per SHEET, not per module, and the split is on how many columns
 * the sheet has rather than on taste.
 *
 * The Check-In Sheet has six columns, three of them boxes somebody writes into
 * at the door, and Speed Networking has two text columns plus one per round. On
 * portrait A4 the long values in those wrap, and a wrapped cell makes its row
 * taller than the rest, which is what breaks the look of the printed page. The
 * extra 87mm of a turned page absorbs the ones that occur in practice.
 *
 * IT REDUCES WRAPPING, IT DOES NOT ABOLISH IT. A long enough company name wraps
 * at any width. The only way to guarantee equal rows is one line per cell, which
 * means truncating, and a desk that cannot read the whole company name is worse
 * off than a desk with one tall row. So this buys the common case and leaves the
 * rare one alone, on purpose.
 *
 * Name Badges and Additional Name Badges stay portrait. Two and three narrow
 * columns would leave half a turned page empty and cost a sheet of paper for it.
 *
 * @page cannot be scoped by a class, so the rule is injected for the duration of
 * the print and taken out again by the cleanup that is already running.
 */
export function printElement(el, title, { landscape = false } = {}) {
  if (!el) return;
  const previousTitle = document.title;
  // PAPER IS LIGHT. The theme tokens have a dark half, and the theme does not
  // change just because a page is being printed, so a dark-mode user printed
  // near-black column rules, a near-black header strip and colour-filled cells
  // as dark blocks. Every one of those is a token doing exactly what it is told
  // in the wrong medium. Forcing the light palette for the duration of the print
  // fixes all of them at once, and is more honest than re-stating each colour as
  // a literal inside the print stylesheet.
  const previousTheme = document.documentElement.getAttribute('data-theme');
  document.documentElement.setAttribute('data-theme', 'light');
  // The document title is what the browser puts in the PDF's filename and in
  // the page header, so this is the difference between a useful file name and
  // "LINQ CRM".
  if (title) document.title = title;
  el.classList.add('printing-now');
  document.body.classList.add('printing');
  let page = null;
  if (landscape) {
    page = document.createElement('style');
    // Margin narrows with the turn: the width is the reason for turning the
    // page, so spending it back on the margin would be self-defeating.
    page.textContent = '@page{size:A4 landscape;margin:12mm}';
    document.head.appendChild(page);
  }

  const cleanup = () => {
    el.classList.remove('printing-now');
    document.body.classList.remove('printing');
    document.title = previousTitle;
    if (previousTheme === null) document.documentElement.removeAttribute('data-theme');
    else document.documentElement.setAttribute('data-theme', previousTheme);
    if (page) { page.remove(); page = null; }
    window.removeEventListener('afterprint', cleanup);
  };
  window.addEventListener('afterprint', cleanup);
  window.print();
  // Safety net for a browser that never fires afterprint. Harmless where it
  // does, because cleanup removes its own listener and is idempotent.
  setTimeout(cleanup, 1000);
}

// "WSE - MP 2026" out of "WSE - MP 2026 — Stormwater Europe 2026". The em dash
// is eventLabel's own separator; see api/preEventDocs.js.
const codeAndEdition = (eventLabel) => String(eventLabel || '').split('—')[0].trim();

/** `WSE - MP 2026: Check-In Sheet`, the title band row 1 of a delivered file carries. */
export const sheetTitle = (eventLabel, what) => `${codeAndEdition(eventLabel) || 'Event'}: ${what}`;

/** `ACU 2026 name badges 2026-09-04.xlsx`, which is what a desk wants to find later. */
export function fileName(eventLabel, what) {
  const day = new Date().toISOString().slice(0, 10);
  const safe = (codeAndEdition(eventLabel) || 'event').replace(/[^\w\s-]/g, '');
  return `${safe} ${what} ${day}`;
}
