// Real backend: /api/events/ (see backend/events/serializers.py for the
// exact field set — EventListSerializer / EventDetailSerializer / EventWriteSerializer).
//
// Known gaps — fields this UI expects that the backend does not currently
// expose/accept, defaulted below rather than fabricated:
//   - `edition`      — no backend field; stored nowhere, always ''.
//   - `capacity`      — Event model has the column but no serializer exposes it.
//   - `web_bookings`  — UI wants a numeric web-sourced booking count; the
//                       backend field of the same name is actually a boolean
//                       "web bookings enabled" flag (mapped to
//                       `web_bookings_enabled` instead). Defaulted to 0.
//   - `email_marketing` (campaign slug) — no backend field; derived from the
//                       event code the same way NewEventModal already did
//                       against the old mock data.
import { http, fetchAllPages } from './client';

function toFrontend(e) {
  return {
    id: e.id,
    event_code: e.event_code,
    name: e.official_event_name || e.name || e.event_code,
    status: e.status,
    event_date: e.event_date,
    end_date: e.end_date,
    location: e.location,
    edition: '',
    event_type: e.event_type,
    capacity: 0,
    web_bookings: 0,
    nearest_related: e.nearest_related_event || '—',
    website_live_date: e.website_live_date,
    sales_check: e.sales_check,
    website: e.website || '',
    web_bookings_enabled: e.accepting_web_bookings || e.web_bookings ? 'Yes' : 'No',
    vr1_status: e.vr1_sent_status || 'Not Sent',
    sales_team: e.sales_team,
    sales_lead: e.team_leader,
    speaker_team: e.speaker_sales_team,
    tele_team: e.telemarketing_team,
    mr_senior: e.market_research_senior,
    mr_junior: e.market_research_junior,
    spex_lead: e.spex_team,
    event_mgmt: e.event_management_team,
    email_marketing: e.event_code ? e.event_code.split(' ')[0].toLowerCase() + '-campaign' : '',
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
// are sent; `edition`/`capacity` are silently dropped (backend has nowhere
// to store them).
function toBackend(f) {
  const out = {
    event_code: f.event_code,
    name: f.name,
    official_event_name: f.name,
    status: f.status,
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
    speaker_sales_team: f.speaker_team,
    telemarketing_team: f.tele_team,
    market_research_senior: f.mr_senior,
    market_research_junior: f.mr_junior,
    spex_team: f.spex_lead,
    event_management_team: f.event_mgmt,
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
