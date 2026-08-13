import { useState } from 'react';
import { EmptyState, PageHead } from '../components/UI';
import DataTable from '../components/DataTable';
import { Icon } from '../lib/icons';
import { Dot, Who } from '../components/Badge';
import { fdate, nf, uniq } from '../lib/helpers';
import {
  PARTICIPATION_TYPES, QC_GRADES, QC_GRADE_TONE, SPEAKER_SLOT_STATUSES, SPEAKER_SLOT_TONE,
  SPONSORSHIP_STATUSES, SPONSORSHIP_TONE, REVENUE_POSSIBILITY, REVENUE_TONE,
} from '../lib/constants';
import * as proposalApi from '../api/proposalSubmission';
import { useFetch } from '../hooks/useFetch';
import { useBulkUpdate } from '../hooks/useBulkUpdate';
import { useLiveData } from '../hooks/useLiveData';
import { useSession } from '../context/SessionContext';
import NoAccessPage from './NoAccessPage';
import ProposalFormModal from './proposalSubmission/ProposalFormModal';
import ProposalImportModal from './proposalSubmission/ProposalImportModal';
import BulkUpdateModal from '../components/BulkUpdateModal';
import ClearAllButton from '../components/ClearAllButton';

export default function ProposalSubmissionPage() {
  const { canView, can } = useSession();
  const { data: proposals, refetch, refetchQuiet, loading, error } = useFetch(proposalApi.list, [], { initialData: [] });
  const PROPOSALS = proposals || [];
  /**
   * Also fires when PAPER REVIEWS are written: importing a review generates the
   * proposals derived from it, so a page showing proposals goes stale on a write
   * to a resource it never reads. That is exactly the kind of link a per-page
   * refresh call misses and a subscription does not.
   */
  const { refreshNow: refresh } = useLiveData(refetchQuiet, {
    resources: ['proposal-submissions', 'paper-reviews'],
  });
  // Router path, not the permission module key — see config/urls.py.
  const bulk = useBulkUpdate('proposal-submissions', refresh);
  const [editProposal, setEditProposal] = useState(null);
  const [newOpen, setNewOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);

  if (!canView('proposal_submission')) return <NoAccessPage module="Proposal Submission" />;

  return (
    <>
      <PageHead title="Proposal Submission"
        actions={<>
          {can('create', 'proposal_submission') ? (
            <>
              <button className="btn btn-s" onClick={() => setImportOpen(true)}><Icon name="download" size={15} />Import</button>
              <button className="btn btn-p" onClick={() => setNewOpen(true)}><Icon name="plus" size={15} />New proposal</button>
            </>
          ) : null}
          {/* Outside the create gate: its audience is the HP account, and nesting it
              would make that the intersection of two unrelated checks. */}
          <ClearAllButton noun="proposal submission" count={PROPOSALS.length}
            onClear={proposalApi.clearAll} onCleared={refresh}
            extra="Paper reviews are not touched. Proposals that were generated from a review will be recreated if that review is imported again." />
        </>} />

      {error && !loading ? (
        <EmptyState icon="warn" title="Unable to load proposal submissions" body="Something went wrong while loading this data. Please try again in a moment."
          action={<button className="btn btn-s btn-sm" onClick={() => refetch().catch(() => {})}><Icon name="refresh" size={13} />Try again</button>} />
      ) : (
      <DataTable
        rows={PROPOSALS} noun="proposals" pageSize={50} defaultSort={{ key: 'submission_date', dir: 'desc' }} searchPlaceholder="Search speaker, company, event…"
        select={can('update', 'proposal_submission')}
        groups={[
          { key: 'id', label: 'Identification' }, { key: 'sp', label: 'Speaker & company' }, { key: 'qc', label: 'Quality & content' },
          { key: 'st', label: 'Status & revenue' }, { key: 'mr', label: 'Internal notes' },
        ]}
        hiddenDefault={['internal_footnotes_mr', 'slot_recommendation_mr']}
        cols={[
          { key: 'event_code', label: 'Event Code', group: 'id', cell: (v) => <span className="mono lnk">{v}</span>, opts: () => uniq(PROPOSALS.map((p) => p.event_code)) },
          { key: 'submission_date', label: 'Submission Date', group: 'id', cell: (v) => (v ? fdate(v) : <span className="dim">—</span>) },
          { key: 'participation_type', label: 'Participation Type', group: 'id', cell: (v) => v || <span className="dim">—</span>, opts: () => PARTICIPATION_TYPES },
          { key: 'speaker_name', label: 'Speaker Name', group: 'sp', cls: 'st', cell: (v, r) => <Who name={v} sub={r.company_name} /> },
          { key: 'email', label: 'Email Address', group: 'sp', cell: (v) => <span style={{ fontSize: 11.5 }}>{v}</span> },
          { key: 'company_name', label: 'Company Name', group: 'sp', opts: () => uniq(PROPOSALS.map((p) => p.company_name)) },
          { key: 'linkedin_speaker', label: 'LinkedIn (Speaker)', group: 'sp', cell: (v) => (v ? <a href={v} target="_blank" rel="noreferrer" className="mono lnk" style={{ fontSize: 11 }}>{v}</a> : <span className="dim">—</span>) },
          { key: 'linkedin_company', label: 'LinkedIn (Company)', group: 'sp', cell: (v) => (v ? <a href={v} target="_blank" rel="noreferrer" className="mono lnk" style={{ fontSize: 11 }}>{v}</a> : <span className="dim">—</span>) },
          { key: 'linkedin_followers', label: 'LinkedIn Followers', group: 'sp', num: true, cell: (v) => (v == null ? <span className="dim">—</span> : nf(v)) },
          { key: 'qc_grade', label: 'QC Grade', group: 'qc', cell: (v) => (v ? <Dot tone={QC_GRADE_TONE[v] || 'neutral'}>{v}</Dot> : <span className="dim">—</span>), opts: () => QC_GRADES },
          { key: 'qc_score', label: 'QC Score', group: 'qc', num: true, cell: (v) => (v == null ? <span className="dim">—</span> : nf(v)) },
          { key: 'presentation_theme', label: 'Presentation Theme', group: 'qc' },
          { key: 'sales_pitch_factor', label: 'Sales Pitch Factor', group: 'qc' },
          { key: 'agenda_slot', label: 'Agenda Slot', group: 'qc' },
          { key: 'agenda_addition', label: 'Agenda Addition', group: 'qc', cell: (v) => (v ? <span className="dim" style={{ maxWidth: 260, display: 'inline-block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', verticalAlign: 'bottom' }}>{v}</span> : <span className="dim">—</span>) },
          { key: 'speaker_slot_status', label: 'Speaker Slot Status', group: 'st', cell: (v) => (v ? <Dot tone={SPEAKER_SLOT_TONE[v] || 'neutral'}>{v}</Dot> : <span className="dim">—</span>), opts: () => SPEAKER_SLOT_STATUSES },
          { key: 'sponsorship_status', label: 'Sponsorship Status', group: 'st', cell: (v) => (v ? <Dot tone={SPONSORSHIP_TONE[v] || 'neutral'}>{v}</Dot> : <span className="dim">—</span>), opts: () => SPONSORSHIP_STATUSES },
          { key: 'revenue_possibility', label: 'Revenue Possibility', group: 'st', cell: (v) => (v ? <Dot tone={REVENUE_TONE[v] || 'neutral'}>{v}</Dot> : <span className="dim">—</span>), opts: () => REVENUE_POSSIBILITY },
          { key: 'spex_remarks', label: 'SpEx Remarks', group: 'st' },
          { key: 'internal_footnotes_mr', label: 'Internal Footnotes (MR)', group: 'mr' },
          { key: 'slot_recommendation_mr', label: 'Slot Recommendation by MR', group: 'mr' },
        ]}
        card={(r) => (
          <div className="rc">
            <div className="rc-t"><Who name={r.speaker_name} /><span style={{ flex: 1 }} /><span className="mono" style={{ color: 'var(--t-600)' }}>{r.event_code}</span></div>
            <div className="rc-m">
              <div><div className="l">Company</div><div className="v">{r.company_name}</div></div>
              <div><div className="l">QC Grade</div><div className="v">{r.qc_grade || '—'}</div></div>
              <div><div className="l">Speaker Slot</div><div className="v">{r.speaker_slot_status || '—'}</div></div>
              <div><div className="l">Sponsorship</div><div className="v">{r.sponsorship_status || '—'}</div></div>
            </div>
          </div>
        )}
        onRow={can('update', 'proposal_submission') ? (r) => setEditProposal(r) : undefined}
        bulkActions={(ids, { clear, total }) => (
          <div className="bulk">
            {/* The rows on this page, not every match — the count says which. */}
            <span className="n">{nf(ids.length)}</span> selected
            {total > ids.length ? <span className="dim" style={{ fontSize: 11 }}>&nbsp;of {nf(total)} matching</span> : null}
            <div className="sep" />
            <button className="btn btn-sm btn-p" onClick={() => bulk.open(ids, clear)}>
              <Icon name="edit" size={13} />Update field…
            </button>
            <button className="x" aria-label="Clear" onClick={clear}><Icon name="x" size={13} /></button>
          </div>
        )}
      />
      )}

      {bulk.ready ? (
        <BulkUpdateModal {...bulk.props} rowLabel="proposal" totalMatching={PROPOSALS.length} />
      ) : null}

      {editProposal ? <ProposalFormModal proposal={editProposal} onClose={() => setEditProposal(null)} onSaved={refresh} /> : null}
      {newOpen ? <ProposalFormModal onClose={() => setNewOpen(false)} onSaved={refresh} /> : null}
      {importOpen ? <ProposalImportModal onClose={() => setImportOpen(false)} onImported={refresh} /> : null}
    </>
  );
}
