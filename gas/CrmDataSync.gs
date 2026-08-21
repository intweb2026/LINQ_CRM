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
 * what makes that safe — the cursor is absolute, so no row is fetched twice and
 * none is skipped.
 */

const RESOURCES = [
  { name: "bookings",  sheetTab: "Bookings" },
  { name: "delegates", sheetTab: "Delegates" },
  { name: "events",    sheetTab: "Events" },
];

const PAGE_SIZE = 500;                   // The API's max_page_size.
const RUN_LIMIT_MS = 6 * 60 * 1000;      // Apps Script hard limit.
const SAFETY_MARGIN_MS = 30 * 1000;      // Hand over 30s before that.

function syncAll() {
  const props = PropertiesService.getScriptProperties();
  const apiKey = props.getProperty("CRM_API_KEY");
  const baseUrl = props.getProperty("CRM_BASE_URL") || "https://app.iq-hub.com";

  if (!apiKey) {
    throw new Error("CRM_API_KEY not set in Script Properties.");
  }

  const savedState = JSON.parse(props.getProperty("_SYNC_STATE") || "null");
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
        props.setProperty("_SYNC_STATE", JSON.stringify({
          resourceIdx: i,
          nextUrl: url,
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

    Logger.log(resource.name + " sync complete.");
  }

  props.deleteProperty("_SYNC_STATE");
  _clearContinuationTriggers();
  Logger.log("Full sync complete.");
}


// -- Helpers -----------------------------------------------------------------

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
