/**
 * IQ-Hub CRM -> Google Sheets sync via the Data API.
 *
 * Setup:
 *   1. In the Apps Script editor, go to Project Settings -> Script Properties.
 *   2. Add: CRM_API_KEY  = <the dapi_ key printed by create_data_api_key>
 *   3. Add: CRM_BASE_URL = https://app.iq-hub.com
 *   4. Run syncAll() manually once, then set a time-driven trigger.
 *
 * The sync is resumable. Apps Script kills an execution at six minutes, so this
 * saves the cursor URL it was about to fetch and schedules a continuation
 * trigger; the next run picks up from exactly that row. Cursor pagination is
 * what makes that safe. The cursor is absolute, so the API hands out each row
 * exactly once and skips none.
 *
 * WHY ROWS APPEARED IN THE SHEET MORE THAN ONCE
 * The API was never the problem; invoice_number is unique in the database and
 * the cursor is keyed on the primary key. Rows repeated because of how THIS
 * SCRIPT resumed:
 *
 *   1. NO LOCK. A resume writes without clearing the tab, by design, because it
 *      is continuing what the previous execution started. The time-driven
 *      trigger and the continuation trigger could fire at once, both read the
 *      same saved cursor, and both append the same pages.
 *   2. STALE STATE NEVER EXPIRED. If an execution died between saving its
 *      cursor and writing its rows, or was killed by hand, the saved state
 *      survived. The next scheduled run resumed from it instead of starting
 *      clean, so it appended a partial dataset onto a full one and the tab grew
 *      every day.
 *
 * Three things stop that now, in order of how much they matter:
 *
 *   A LOCK. One execution writes at a time; a second exits immediately rather
 *     than queueing behind the first and repeating its work.
 *   A STATE TTL. A saved cursor older than STATE_TTL_MS is abandoned and the
 *     resource restarts from page 1, which clears the tab.
 *   A DEDUPE PASS. After a resource finishes, duplicate rows are removed by
 *     their id column, keeping the first of each. This is the net rather than
 *     the fix; if it ever removes anything, something above it failed and the
 *     log says so.
 */

const RESOURCES = [
  { name: "bookings",  sheetTab: "Bookings" },
  { name: "delegates", sheetTab: "Delegates" },
  { name: "events",    sheetTab: "Events" },
];

const PAGE_SIZE = 500;                   // The API's max_page_size.
const RUN_LIMIT_MS = 6 * 60 * 1000;      // Apps Script hard limit.
const SAFETY_MARGIN_MS = 30 * 1000;      // Hand over 30s before that.
const STATE_TTL_MS = 60 * 60 * 1000;     // Abandon a cursor older than an hour.
const DEDUPE_KEY = "id";                 // Unique per row on every resource.

function syncAll() {
  // One writer at a time. A continuation trigger fires 5 seconds after a
  // handover, which is easily inside a scheduled run, and two executions
  // appending the same cursor is what put each row in the sheet twice.
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) {
    Logger.log("Another sync is already running. Exiting without writing.");
    return;
  }

  try {
    _syncAllLocked();
  } finally {
    lock.releaseLock();
  }
}

function _syncAllLocked() {
  const props = PropertiesService.getScriptProperties();
  const apiKey = props.getProperty("CRM_API_KEY");
  const baseUrl = props.getProperty("CRM_BASE_URL") || "https://app.iq-hub.com";

  if (!apiKey) {
    throw new Error("CRM_API_KEY not set in Script Properties.");
  }

  const savedState = _readState(props);
  const startIdx = savedState ? savedState.resourceIdx : 0;
  const startUrl = savedState ? savedState.nextUrl : null;

  const startTime = Date.now();

  for (let i = startIdx; i < RESOURCES.length; i++) {
    const resource = RESOURCES[i];
    const resuming = (i === startIdx && !!startUrl);

    let url = resuming
      ? startUrl
      : baseUrl + "/api/data/" + resource.name + "/?page_size=" + PAGE_SIZE;

    // Only wipe the tab when starting this resource from its first page. A
    // continuation must append to what the previous run already wrote.
    if (!resuming) {
      _clearSheet(resource.sheetTab);
    }

    let headersDone = resuming;
    let pageCount = 0;

    while (url) {
      if (Date.now() - startTime > RUN_LIMIT_MS - SAFETY_MARGIN_MS) {
        // savedAt is what makes this cursor expire. Without it a state written
        // by an execution that then died was resumed forever.
        props.setProperty("_SYNC_STATE", JSON.stringify({
          resourceIdx: i,
          nextUrl: url,
          savedAt: Date.now(),
        }));
        _scheduleContinuation();
        Logger.log("Time limit approaching. Saved cursor and scheduled continuation.");
        return;
      }

      const response = UrlFetchApp.fetch(url, {
        method: "GET",
        headers: { "X-DATA-API-KEY": apiKey },
        muteHttpExceptions: true,
      });

      if (response.getResponseCode() !== 200) {
        // Clear the state before throwing. A half-written tab plus a saved
        // cursor is the combination that accumulated rows: the next run resumed
        // instead of starting clean, so it never cleared what was already there.
        props.deleteProperty("_SYNC_STATE");
        throw new Error(
          "API returned " + response.getResponseCode() + ": " +
          response.getContentText().substring(0, 500)
        );
      }

      const data = JSON.parse(response.getContentText());
      const rows = data.results || [];

      if (rows.length === 0) break;

      const headers = Object.keys(rows[0]);

      if (!headersDone) {
        _appendRows(resource.sheetTab, [headers]);
        headersDone = true;
      }

      const dataRows = rows.map(function (row) {
        return headers.map(function (h) {
          var val = row[h];
          if (val === null || val === undefined) return "";
          if (typeof val === "object") return JSON.stringify(val);
          return val;
        });
      });
      _appendRows(resource.sheetTab, dataRows);

      pageCount++;
      Logger.log(resource.name + " - page " + pageCount + " (" + rows.length + " rows)");

      url = data.next || null;
    }

    const removed = _dedupeTab(resource.sheetTab, DEDUPE_KEY);
    if (removed > 0) {
      // Not routine tidying. Every row the API hands out is unique, so a
      // duplicate here means two executions wrote the same page and the lock or
      // the TTL above did not prevent it. Worth reading the log for.
      Logger.log("WARNING: removed " + removed + " duplicate rows from " +
                 resource.sheetTab + ". Two executions wrote the same pages.");
    }
    Logger.log(resource.name + " sync complete.");
  }

  props.deleteProperty("_SYNC_STATE");
  _clearContinuationTriggers();
  Logger.log("Full sync complete.");
}


// -- Helpers -----------------------------------------------------------------

/**
 * The saved cursor, or null when there is nothing usable to resume from.
 *
 * A cursor older than STATE_TTL_MS is thrown away rather than resumed. The
 * alternative, which is what used to happen, is that one execution killed at
 * the wrong moment left the sync permanently mid-resource, appending to a tab
 * it would never again clear.
 */
function _readState(props) {
  const raw = props.getProperty("_SYNC_STATE");
  if (!raw) return null;

  var state;
  try {
    state = JSON.parse(raw);
  } catch (e) {
    props.deleteProperty("_SYNC_STATE");
    return null;
  }
  if (!state || !state.nextUrl) return null;

  const age = Date.now() - (state.savedAt || 0);
  if (age > STATE_TTL_MS) {
    Logger.log("Saved cursor is " + Math.round(age / 60000) +
               " minutes old. Starting a fresh sync instead of resuming.");
    props.deleteProperty("_SYNC_STATE");
    return null;
  }
  return state;
}

function _clearSheet(tabName) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(tabName);
  if (!sheet) {
    ss.insertSheet(tabName);
  } else {
    sheet.clearContents();
  }
}

function _appendRows(tabName, rows) {
  if (rows.length === 0) return;
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(tabName);
  if (!sheet) {
    sheet = ss.insertSheet(tabName);
  }
  sheet.getRange(
    sheet.getLastRow() + 1, 1, rows.length, rows[0].length
  ).setValues(rows);
}

/**
 * Remove rows whose key column repeats, keeping the first of each. Returns how
 * many were removed, so the caller can log the fact rather than swallow it.
 *
 * Rewrites the sheet in one setValues call rather than deleting rows one by
 * one; 35,000 deleteRow calls do not finish inside an Apps Script execution.
 */
function _dedupeTab(tabName, keyHeader) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(tabName);
  if (!sheet) return 0;

  var lastRow = sheet.getLastRow();
  var lastCol = sheet.getLastColumn();
  if (lastRow < 3 || lastCol < 1) return 0;   // header plus one row at most

  var values = sheet.getRange(1, 1, lastRow, lastCol).getValues();
  var header = values[0];
  var keyIdx = header.indexOf(keyHeader);
  if (keyIdx === -1) return 0;                // nothing to key on; leave it alone

  var seen = {};
  var kept = [header];
  for (var r = 1; r < values.length; r++) {
    var key = String(values[r][keyIdx]);
    if (key === "") continue;                 // a blank spacer row, drop it
    if (seen[key]) continue;
    seen[key] = true;
    kept.push(values[r]);
  }

  var removed = (values.length - 1) - (kept.length - 1);
  if (removed <= 0) return 0;

  sheet.clearContents();
  sheet.getRange(1, 1, kept.length, lastCol).setValues(kept);
  return removed;
}

function _scheduleContinuation() {
  _clearContinuationTriggers();
  ScriptApp.newTrigger("syncAll")
    .timeBased()
    .after(5 * 1000)
    .create();
}

function _clearContinuationTriggers() {
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === "syncAll" &&
        triggers[i].getTriggerSource() === ScriptApp.TriggerSource.CLOCK) {
      ScriptApp.deleteTrigger(triggers[i]);
    }
  }
}

/**
 * Throw away any saved cursor and any pending continuation, then sync from
 * scratch. Run this once by hand after pasting this version in, so a cursor
 * saved by the old script cannot resume into the new one.
 */
function resetAndSyncAll() {
  PropertiesService.getScriptProperties().deleteProperty("_SYNC_STATE");
  _clearContinuationTriggers();
  Logger.log("State cleared. Starting a full sync.");
  syncAll();
}
