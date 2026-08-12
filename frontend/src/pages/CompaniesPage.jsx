import { PageHead } from '../components/UI';
import DataTable from '../components/DataTable';
import { fdate, nf } from '../lib/helpers';
import * as companiesApi from '../api/companies';

export default function CompaniesPage() {
  // No companiesApi.list(): that walked all 7,672 rows (~16 sequential requests)
  // before the first row could render. CompanyViewSet now carries FilterSpecMixin
  // so the table pages and filters server-side.
  return (
    <>
      <PageHead title="Companies" sub="Directory of every company that has come through a booking, with their delegate history." />
      <DataTable
        tableId="companies"
        server={{ resource: 'companies', mapRow: companiesApi.fromApi }}
        noun="companies" pageSize={50} defaultSort={{ key: 'name', dir: 'asc' }} searchPlaceholder="Search companies…"
        cols={[
          { key: 'name', label: 'Company', cls: 'st', serverField: 'name', serverOrdering: 'name' },
          { key: 'city', label: 'City', serverField: 'city', serverOrdering: 'city' },
          { key: 'country', label: 'Country', serverField: 'country', serverOrdering: 'country' },
          { key: 'website', label: 'Website', serverField: 'website', cell: (v) => (v ? <a href={v} target="_blank" rel="noreferrer" className="mono lnk" style={{ fontSize: 11 }}>{v.replace(/^https?:\/\//, '')}</a> : <span className="dim">—</span>) },
          { key: 'delegate_count', label: 'Delegates', num: true, cell: (v) => nf(v) },
          { key: 'created_at', label: 'Added', serverOrdering: 'created_at', cell: (v) => fdate(v) },
        ]}
      />
    </>
  );
}
