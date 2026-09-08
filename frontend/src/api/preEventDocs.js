// Real backend: /api/pre-event-docs/ (see backend/pre_event_docs/views.py).
//
// ONE REQUEST FOR THE WHOLE PAGE. All five tabs read the same filtered rows, so
// `docs()` returns every list at once rather than the page making five calls
// that would run the same query five times and give five chances for the tabs to
// disagree about who is attending. That disagreement is exactly what went wrong
// in the workbook this replaces, where one tab's formula was edited and the
// others were not.
//
// Not paginated. An event's delegate list is hundreds of rows, not thousands,
// and every list here is meant to be read, printed or exported whole.
//
// NO ATTENDANCE WRITE LIVES HERE, deliberately. Ticking somebody in already has
// a purpose-built endpoint, PATCH /api/delegates/{id}/update_attendance/ (see
// BookDelegateViewSet.update_attendance), which validates against the
// Attendance choices and saves update_fields. A generic PATCH from this module
// was written here once and removed: it bypassed that validation and gave the
// CRM two ways to record the same fact. The Check-in tab shows the tick and
// leaves writing it to the code that owns it.
import { http } from './client';

export const TABS = {
  REGISTERED: '',
  BADGES: 'badges',
  CHANGES: 'changes',
  CHECKIN: 'check-in',
  NETWORKING: 'networking',
};

// The band the delivered "Additional Name Badge" workbook carries over its
// header, word for word. It lives here rather than in either caller because
// BOTH the tab's Excel button and the freeze modal write that same list, and
// two spellings of it would go out to the same printer in the same week.
export const TO_PRINT = 'TO PRINT (New, Name Changes, Company Changes)';

/** The (event_code, edition) pairs the delegates table actually holds. */
export const events = () =>
  http.get('pre-event-docs/events/').then((r) => r.data);

/**
 * Everything for one event.
 *
 * `edition` is sent only when it has a value: most delegate rows carry none, and
 * sending an empty one would filter on it rather than ignoring it.
 */
export const docs = (eventCode, edition) =>
  http
    .get('pre-event-docs/docs/', {
      params: { event_code: eventCode, ...(edition ? { edition } : {}) },
    })
    .then((r) => r.data);

/**
 * Log a badge run.
 *
 * `badges` carries the name and company AS PRINTED, taken from what the page was
 * showing rather than re-read on the server. The change lists are a diff against
 * what went on the badge, so recording anything else would compare a value with
 * itself and report no changes for ever.
 */
export const badgeRun = (eventCode, edition, badges) =>
  http
    .post('pre-event-docs/badge-run/', {
      event_code: eventCode,
      edition: edition || null,
      badges: badges.map((b) => ({
        delegate_id: b.delegate_id,
        name: b.name,
        company: b.company,
      })),
    })
    .then((r) => r.data);

/**
 * The FROZEN SNAPSHOT of one badge run, row by row.
 *
 * This is how a run is verified rather than trusted. Each row carries the name
 * and company as they were FROZEN, beside what the booking says today, and a
 * drift label. A row reading "Name changed" is the log doing its job, not a
 * storage fault; see services.badge_run_detail.
 */
export const runDetail = (runId) =>
  http.get(`pre-event-docs/run/${encodeURIComponent(runId)}/`).then((r) => r.data);

/** Undo one run. The reason run_id exists; see models.BadgeIssue. */
export const undoBadgeRun = (runId) =>
  http.delete(`pre-event-docs/badge-run/${encodeURIComponent(runId)}/`).then((r) => r.data);

/**
 * Draw a networking plan and store it.
 *
 * A draw is random, so it is stored rather than recomputed; the table cards
 * printed at nine o'clock have to match the screen at ten. Each draw writes a
 * new row, so an already printed one stays readable after somebody redraws.
 */
export const draw = (eventCode, edition, tables, rounds, perTable) =>
  http
    .post('pre-event-docs/draw/', {
      event_code: eventCode,
      edition: edition || null,
      // HOW MANY PEOPLE CAN SIT AT ONE TABLE, a maximum. The two are sent
      // together and the server checks them against the head count, so a room
      // that cannot hold the list is refused with both numbers that would work
      // rather than being silently reseated.
      per_table: perTable || null,
      // HOW MANY TABLES THE ROOM HAS, honoured exactly by the server. This was
      // per_table, a target number of people per table, which meant asking for
      // six produced 28 tables on a 170 person event. A room has the tables it
      // has; the number of people at each is the consequence.
      tables,
      rounds,
    })
    .then((r) => r.data);

/** The label an event reads as in the picker and on a printout. */
export const eventLabel = (e) =>
  !e ? '' : [e.event_code, e.edition].filter(Boolean).join(' ') +
    (e.event_name ? ` — ${e.event_name}` : '');
