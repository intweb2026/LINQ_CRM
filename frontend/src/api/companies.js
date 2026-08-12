// Real backend: /api/companies/ (see backend/companies/serializers.py + views.py).
import { http } from './client';

function toFrontend(c) {
  return {
    id: c.id,
    name: c.name,
    city: c.city,
    country: c.country,
    website: c.website || '',
    delegate_count: c.delegate_count || 0,
    created_at: c.created_at,
  };
}

// list() removed: it walked all 7,672 companies. CompaniesPage pages
// server-side via DataTable's `server` prop (CompanyViewSet carries
// FilterSpecMixin); `fromApi` below is its row mapper.

export const delegates = (id) => http.get(`companies/${id}/delegates/`).then((r) => r.data);

// Row mapper for DataTable server mode, which receives raw API rows.
export const fromApi = toFrontend;
