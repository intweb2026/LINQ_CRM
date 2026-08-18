import { useCallback, useState } from 'react';
import { ExtLink, PageHead } from '../components/UI';
import DataTable from '../components/DataTable';
import { Icon } from '../lib/icons';
import { Dot, Who } from '../components/Badge';
import { fdate, nf } from '../lib/helpers';
import { htmlToText } from '../lib/richText';
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
import DateRangeFilter from '../components/DateRangeFilter';

/**
 * Module scope, evaluated once — the stability DataTable's memoised Row needs to
 * skip re-rendering rows that have not changed. Rebuilt per render, as this was,
 * the array is a changed prop on every loaded row each time a page arrives, so the
 * whole table re-renders and the memo buys nothing. Nothing here reads component
 * state; anything added that does has to move back inside, under useMemo. Same
 * reasoning as REVIEW_COLS in PaperReviewPage.
 */
/**
 * One line of an HTML-stored prose column.
 *
 * agenda_addition arrives from Zoho as markup, so the raw value in a cell this
 * narrow read `<p><b>FROM INVISIBLE LOSSES TO SMART…` and was cut off inside the
 * first tag. The words are what belongs on one line; the formatting itself is in
 * the edit modal, on RichTextField. A value that is nothing but markup reduces to
 * no words at all, and reads as empty rather than as a stray fragment.
 */
const proseCell = (v) => {
  const text = htmlToText(v);
  return text
    ? <span className="dim" style={{ maxWidth: 260, display: 'inline-block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', verticalAlign: 'bottom' }}>{text}</span>
    : <span className="dim">—</span>;
};

const PROPOSAL_COLS = [
  /* event_code and company_name carry NO `opts`. Those dropdowns were
     built by scanning the loaded rows, which under server paging means
     the fifty on screen. Both are registered filter_spec fields, so a
     text condition on either is still evaluated by the database over
     every row. The status columns keep theirs; those lists are constants
     rather than a scan of the data. */
  { key: 'event_code', serverOrdering: 'event_code', label: 'Event Code', group: 'id', cell: (v) => <span className="mono lnk">{v}</span> },
  { key: 'submission_date', serverOrdering: 'submission_date', label: 'Submission Date', group: 'id', cell: (v) => (v ? fdate(v) : <span className="dim">—</span>) },
  { key: 'participation_type', serverOrdering: 'participation_type', label: 'Participation Type', group: 'id', cell: (v) => v || <span className="dim">—</span>, opts: () => PARTICIPATION_TYPES },
  // Name only — Company Name has its own column; see PaperReviewPage.
  { key: 'speaker_name', serverOrdering: 'speaker_name', label: 'Speaker Name', group: 'sp', cls: 'st', cell: (v) => <Who name={v} avatar={false} /> },
  { key: 'email', serverOrdering: 'email', label: 'Email Address', group: 'sp', cell: (v) => <span style={{ fontSize: 11.5 }}>{v}</span> },
  { key: 'company_name', serverOrdering: 'company_name', label: 'Company Name', group: 'sp' },
  /* ExtLink rather than a hand-rolled <a>: it resolves the href (an anchor-wrapped
     or scheme-less cell is otherwise a link to nowhere), refuses to linkify text
     that is not an address, opens in its own tab with no handle back to this one,
     and stops the click reaching the row's own open-the-record handler. */
  { key: 'linkedin_speaker', serverOrdering: 'linkedin_speaker', label: 'LinkedIn (Speaker)', group: 'sp', cell: (v) => <ExtLink value={v} className="mono lnk" style={{ fontSize: 11 }} /> },
  { key: 'linkedin_company', serverOrdering: 'linkedin_company', label: 'LinkedIn (Company)', group: 'sp', cell: (v) => <ExtLink value={v} className="mono lnk" style={{ fontSize: 11 }} /> },
  { key: 'linkedin_followers', serverOrdering: 'linkedin_followers', label: 'LinkedIn Followers', group: 'sp', num: true, cell: (v) => (v == null ? <span className="dim">—</span> : nf(v)) },
  { key: 'qc_grade', serverOrdering: 'qc_grade', label: 'QC Grade', group: 'qc', cell: (v) => (v ? <Dot tone={QC_GRADE_TONE[v] || 'neutral'}>{v}</Dot> : <span className="dim">—</span>), opts: () => QC_GRADES },
  { key: 'qc_score', serverOrdering: 'qc_score', label: 'QC Score', group: 'qc', num: true, cell: (v) => (v == null ? <span className="dim">—</span> : nf(v)) },
  { key: 'presentation_theme', serverOrdering: 'presentation_theme', label: 'Presentation Theme', group: 'qc' },
  { key: 'sales_pitch_factor', serverOrdering: 'sales_pitch_factor', label: 'Sales Pitch Factor', group: 'qc' },
  { key: 'agenda_slot', serverOrdering: 'agenda_slot', label: 'Agenda Slot', group: 'qc' },
  { key: 'agenda_addition', serverOrdering: 'agenda_addition', label: 'Agenda Addition', group: 'qc', cell: proseCell },
  { key: 'speaker_slot_status', serverOrdering: 'speaker_slot_status', label: 'Speaker Slot Status', group: 'st', cell: (v) => (v ? <Dot tone={SPEAKER_SLOT_TONE[v] || 'neutral'}>{v}</Dot> : <span className="dim">—</span>), opts: () => SPEAKER_SLOT_STATUSES },
  { key: 'sponsorship_status', serverOrdering: 'sponsorship_status', label: 'Sponsorship Status', group: 'st', cell: (v) => (v ? <Dot tone={SPONSORSHIP_TONE[v] || 'neutral'}>{v}</Dot> : <span className="dim">—</span>), opts: () => SPONSORSHIP_STATUSES },
  { key: 'revenue_possibility', serverOrdering: 'revenue_possibility', label: 'Revenue Possibility', group: 'st', cell: (v) => (v ? <Dot tone={REVENUE_TONE[v] || 'neutral'}>{v}</Dot> : <span className="dim">—</span>), opts: () => REVENUE_POSSIBILITY },
  { key: 'spex_remarks', serverOrdering: 'spex_remarks', label: 'SpEx Remarks', group: 'st' },
  { key: 'internal_footnotes_mr', label: 'Internal Footnotes (MR)', group: 'mr' },
  { key: 'slot_recommendation_mr', label: 'Slot Recommendation by MR', group: 'mr' },
];

const PROPOSAL_GROUPS = [
  { key: 'id', label: 'Identification' }, { key: 'sp', label: 'Speaker & company' }, { key: 'qc', label: 'Quality & content' },
  { key: 'st', label: 'Status & revenue' }, { key: 'mr', label: 'Internal notes' },
];
const PROPOSAL_HIDDEN = ['internal_footnotes_mr', 'slot_recommendation_mr'];

export default function ProposalSubmissionPage() {
  const { canView, can } = useSession();
  // Date range, applied by the SERVER over submission_date falling back to
  // created_at — see accounts/period_filter.py. submission_date is nullable, and
  // the fallback is what stops a window hiding every row that never got one.
  const [period, setPeriod] = useState('all');
  /**
   * The row count, as its own small aggregate; the table no longer holds the set.
   *
   * This page used to load every proposal into the browser with a fetchAllPages
   * walk and read `.length` off it — 3,752 rows over 8 sequential requests on the
   * current database, none of which rendered until the last one landed. See
   * ProposalSubmissionViewSet.stats.
   */
  const fetchStats = useCallback(() => proposalApi.stats(period), [period]);
  const { data: stats, loading: statsLoading, refetchQuiet: reloadStats } =
    useFetch(fetchStats, [period], { initialData: {} });
  const total = stats && stats.total != null ? stats.total : null;

  /**
   * The table keeps its own rows current in server mode; this pair is for the
   * count beside it, which is a separate query.
   *
   * The subscription also fires when PAPER REVIEWS are written: importing a
   * review generates the proposals derived from it, so a page showing proposals
   * goes stale on a write to a resource it never reads. That is exactly the kind
   * of link a per-page refresh call misses and a subscription does not. The table
   * gets the same reach through server.live.
   */
  const [tableRefetch, setTableRefetch] = useState(null);
  const keepRefetch = useCallback((fn) => setTableRefetch(() => fn), []);
  const { refreshNow: refreshStats } = useLiveData(reloadStats, {
    resources: ['proposal-submissions', 'paper-reviews'],
  });
  const refresh = useCallback(() => {
    if (tableRefetch) tableRefetch();
    refreshStats();
  }, [tableRefetch, refreshStats]);
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
          <ClearAllButton noun="proposal submission" count={total}
            onClear={proposalApi.clearAll} onCleared={refresh}
            extra="Paper reviews are not touched. Proposals that were generated from a review will be recreated if that review is imported again." />
        </>} />

      <DateRangeFilter value={period} onChange={setPeriod} loading={statsLoading}
        count={total} noun="proposals" note="by submission date" />

      <DataTable
        /**
         * SERVER MODE, for the reasons set out on the same prop in
         * PaperReviewPage: the first paint costs one 50-row request instead of a
         * walk of the whole table, and the wait now shows a "Loading proposals…"
         * spinner rather than the "No Proposals Found" state, which the table was
         * previously obliged to render while it still had nothing.
         *
         * `live` names paper-reviews as well, because the bridge creates
         * proposals from a review write — the same link the stats subscription
         * above covers, applied to the rows.
         */
        server={{ resource: 'proposal-submissions', live: ['paper-reviews'] }}
        serverParams={{ period }}
        onServerReady={keepRefetch}
        // 100 rather than 50, for the reason given on the same prop in
        // PaperReviewPage: half as many scroll stops, each one a round trip plus a
        // re-layout of everything already rendered.
        noun="proposals" pageSize={100} infinite defaultSort={{ key: 'submission_date', dir: 'desc' }} searchPlaceholder="Search speaker, company, event…"
        select={can('update', 'proposal_submission')}
        groups={PROPOSAL_GROUPS}
        hiddenDefault={PROPOSAL_HIDDEN}
        cols={PROPOSAL_COLS}
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
        bulkActions={(ids, { clear, total: matching }) => (
          <div className="bulk">
            {/* Whatever the header checkbox resolved — every match, or the rows
                ticked by hand. The count says which. */}
            <span className="n">{nf(ids.length)}</span> selected
            {matching > ids.length ? <span className="dim" style={{ fontSize: 11 }}>&nbsp;of {nf(matching)} matching</span> : null}
            <div className="sep" />
            <button className="btn btn-sm btn-p" onClick={() => bulk.open(ids, clear)}>
              <Icon name="edit" size={13} />Update field…
            </button>
            <button className="x" aria-label="Clear" onClick={clear}><Icon name="x" size={13} /></button>
          </div>
        )}
      />

      {bulk.ready ? (
        <BulkUpdateModal {...bulk.props} rowLabel="proposal" totalMatching={total} />
      ) : null}

      {editProposal ? <ProposalFormModal proposal={editProposal} onClose={() => setEditProposal(null)} onSaved={refresh} /> : null}
      {newOpen ? <ProposalFormModal onClose={() => setNewOpen(false)} onSaved={refresh} /> : null}
      {importOpen ? <ProposalImportModal onClose={() => setImportOpen(false)} onImported={refresh} /> : null}
    </>
  );
}
