import Papa from 'papaparse';
import * as XLSX from 'xlsx';

// Parses a File into { headers: string[], rows: Array<Record<string,string>> }.
// Supports .csv, .xlsx/.xls, and .json (array of flat objects).
export function parseFile(file) {
  const name = file.name.toLowerCase();
  if (name.endsWith('.json')) return parseJson(file);
  if (name.endsWith('.xlsx') || name.endsWith('.xls')) return parseXlsx(file);
  return parseCsv(file);
}

function parseCsv(file) {
  return new Promise((resolve, reject) => {
    Papa.parse(file, {
      header: true,
      skipEmptyLines: true,
      complete: (result) => resolve({ headers: result.meta.fields || [], rows: result.data }),
      error: reject,
    });
  });
}

function parseXlsx(file) {
  return file.arrayBuffer().then((buf) => {
    const wb = XLSX.read(buf, { type: 'array' });
    const sheet = wb.Sheets[wb.SheetNames[0]];
    // DIVERGENCE, DELIBERATE AND DOCUMENTED — read this with the matching note
    // on accounts/import_common.py:parse_import_date.
    //
    // `raw: false` makes SheetJS return each cell as its DISPLAYED text, so a
    // date arrives as "15/01/2026" and an Excel serial (45678) never reaches the
    // server through this path at all. The server's parse_import_date handles
    // BOTH representations, so this path is safe — but it is not the same input
    // the server sees when it reads a workbook itself.
    //
    // Three parsers exist and they do NOT agree:
    //   this file            -> formatted strings only, no serials
    //   parse_import_date    -> serials (bounded, phantom 60 rejected) + strings
    //   the legacy _parse_date in events/ and book_event/ views
    //                        -> six string formats, NO serial support, and
    //                           returns None on failure instead of erroring
    //
    // Consequence worth knowing: `manage.py load_zoho_export` reads the .xlsx
    // SERVER-side via openpyxl, so it DOES see raw serials, and parse_import_date
    // is what makes that safe. Uploading the same workbook through the browser
    // and loading it with the command are therefore not identical operations —
    // the command sees more of the truth. Prefer the command for the Zoho load.
    //
    // Not switched to `raw: true` here: that would change the input shape for the
    // live paper-review and proposal-submission importers, which is a behaviour
    // change this round has no real file to validate against.
    const rows = XLSX.utils.sheet_to_json(sheet, { defval: '', raw: false });
    const headers = rows.length ? Object.keys(rows[0]) : [];
    return { headers, rows };
  });
}

function parseJson(file) {
  return file.text().then((text) => {
    const data = JSON.parse(text);
    const rows = Array.isArray(data) ? data : [];
    const headers = rows.length ? Object.keys(rows[0]) : [];
    return { headers, rows };
  });
}
