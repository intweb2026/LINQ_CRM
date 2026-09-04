// Real backend: /api/events/ (see backend/events/serializers.py for the
// exact field set — EventListSerializer / EventDetailSerializer / EventWriteSerializer).
//
// `event_status` is the server's computed Live/Completed by date. The stored
// `status`, `capacity` and the mocked web-booking count and campaign slug are
// gone from this contract: nothing on the Events screen shows them any more.
import { http, fetchAllPages } from './client';

// Backend column -> frontend key, for the owner columns the server can answer
// from the Teams module. Only these four are resolvable; see OWNER_ROLE_SOURCES
// in backend/events/serializers.py for why sales_team, market_research_junior
// and event_management_team are deliberately not.
const OWNER_SRC_KEYS = {
  team_leader: 'sales_lead',
  telemarketing_team: 'tele_team',
  market_research_senior: 'mr_senior',
  spex_team: 'spex_lead',
};

/**
 * The team-inherited owners for an event, keyed the way the UI keys them.
 *
 * Kept SEPARATE from the value fields below rather than merged into them. An
 * inherited name written into `sales_lead` would be indistinguishable from one
 * stored on the event, so the edit form would post it on the next save and
 * freeze "whoever leads Sales" into one person's name without anyone asking.
 * lib/owners.js reads this alongside the raw value and labels the difference.
 */
function ownerSrc(raw) {
  const out = {};
  Object.entries(raw || {}).forEach(([backendKey, v]) => {
    const key = OWNER_SRC_KEYS[backendKey];
    // `names` is a LIST and every entry survives — a team may have any number of
    // leads (Sales Team has two), and nothing here picks one.
    const names = (v && v.names) || [];
    if (key && names.length) out[key] = { names, team: (v && v.team) || '' };
  });
  return out;
}

function toFrontend(e) {
  return {
    id: e.id,
    event_code: e.event_code,
    name: e.official_event_name || e.name || e.event_code,
    event_status: e.event_status,
    event_date: e.event_date,
    end_date: e.end_date,
    location: e.location,
    base_code: e.base_code || '',
    year: e.year ?? '',
    verdict: e.verdict || '',
    event_type: e.event_type,
    nearest_related: e.nearest_related_event || '—',
    website_live_date: e.website_live_date,
    sales_check: e.sales_check,
    website: e.website || '',
    web_bookings_enabled: e.accepting_web_bookings || e.web_bookings ? 'Yes' : 'No',
    vr1_status: e.vr1_sent_status || 'Not Sent',
    sales_team: e.sales_team,
    sales_lead: e.team_leader,
    // The event's sales executive, resolved the same way the backend resolves it
    // (book_event/models.py auto_assign_sales): the FK first, then the free-text
    // `sales_team` the Events tab keeps in step with it. Read by the Bookings tab,
    // where Sales Executive is owned by the event rather than by the booking.
    sales_exec: e.sales_executive_name || e.sales_team || '',
    tele_team: e.telemarketing_team,
    mr_senior: e.market_research_senior,
    mr_junior: e.market_research_junior,
    spex_lead: e.spex_team,
    owner_src: ownerSrc(e.owner_resolution),
    email_marketing_name: e.email_marketing_name || '',
    branding_name: e.branding_name || '',
    annualisation: e.annualisation || '',
    date_format: e.date_format || '',
    related_event_1: e.related_event_1 || '',
    related_event_2: e.related_event_2 || '',
    related_event_3: e.related_event_3 || '',
    upcoming_event_1: e.upcoming_event_1 || '',
    upcoming_event_2: e.upcoming_event_2 || '',
    upcoming_event_3: e.upcoming_event_3 || '',
  };
}

// Inverse of toFrontend — only fields EventWriteSerializer actually accepts
// are sent. A blank base_code or year is sent as null and Event.save() derives it.
function toBackend(f) {
  const out = {
    event_code: f.event_code,
    name: f.name,
    official_event_name: f.name,
    base_code: f.base_code,
    year: f.year === '' || f.year == null ? null : Number(f.year),
    event_date: f.event_date,
    end_date: f.end_date,
    location: f.location,
    event_type: f.event_type,
    nearest_related_event: f.nearest_related,
    website_live_date: f.website_live_date,
    sales_check: f.sales_check,
    website: f.website,
    web_bookings: f.web_bookings_enabled === 'Yes',
    vr1_sent_status: f.vr1_status,
    sales_team: f.sales_team,
    team_leader: f.sales_lead,
    telemarketing_team: f.tele_team,
    market_research_senior: f.mr_senior,
    market_research_junior: f.mr_junior,
    spex_team: f.spex_lead,
    email_marketing_name: f.email_marketing_name,
    branding_name: f.branding_name,
    annualisation: f.annualisation,
    date_format: f.date_format,
    related_event_1: f.related_event_1,
    related_event_2: f.related_event_2,
    related_event_3: f.related_event_3,
    upcoming_event_1: f.upcoming_event_1,
    upcoming_event_2: f.upcoming_event_2,
    upcoming_event_3: f.upcoming_event_3,
  };
  Object.keys(out).forEach((k) => { if (out[k] === undefined) delete out[k]; });
  return out;
}

export const list = () => fetchAllPages('events/').then((rows) => rows.map(toFrontend));

export const get = (id) => http.get(`events/${id}/`).then((r) => toFrontend(r.data));

export function update(id, patch) {
  return http.patch(`events/${id}/`, toBackend(patch)).then((r) => toFrontend(r.data));
}
export function remove(id) {
  return http.delete(`events/${id}/`).then(() => true);
}
export function create(payload) {
  return http.post('events/', toBackend(payload)).then((r) => toFrontend(r.data));
}

// DELETE /api/events/clear_all/ — HP only (accounts/permissions.py IsHPAccount).
// Clears the CATALOGUE only. Bookings hold their event as a text code rather than a
// foreign key, so they survive with codes that no longer resolve — which is why the
// confirmation says so and why clearing bookings is its own action.
export const clearAll = () => http.delete('events/clear_all/').then((r) => r.data);
