import { useCallback, useState } from 'react';
import { PageHead } from '../components/UI';
import DataTable from '../components/DataTable';
import { Icon } from '../lib/icons';
import { Dot, Who } from '../components/Badge';
import { fdate, nf } from '../lib/helpers';
import { PAPER_REVIEW_CRITERIA, PAPER_GRADES, PAPER_GRADE_TONE, PAPER_SESSION_OPTIONS } from '../lib/constants';
import * as paperReviewApi from '../api/paperReview';
import { useFetch } from '../hooks/useFetch';
import { useBulkUpdate } from '../hooks/useBulkUpdate';
import { useLiveData } from '../hooks/useLiveData';
import { useSession } from '../context/SessionContext';
import NoAccessPage from './NoAccessPage';
import PaperReviewFormModal from './paperReview/PaperReviewFormModal';
import PaperReviewImportModal from './paperReview/PaperReviewImportModal';
import BulkUpdateModal from '../components/BulkUpdateModal';
import ClearAllButton from '../components/ClearAllButton';
import DateRangeFilter from '../components/DateRangeFilter';

export default function PaperReviewPage() {
  const { canView, can } = useSession();
  // Date range, applied by the SERVER over paper_submission_date falling back to
  // created_at — see accounts/period_filter.py. The fallback matters here:
  // paper_submission_date is nullable, and a window over it alone would silently
  // hide every review whose submission date was never filled in.
  const [period, setPeriod] = useState('all');
  /**
   * The row count, as its own small aggregate; the table no longer holds the set.
   *
   * This page used to load EVERY review into the browser with a fetchAllPages
   * walk and read `.length` off it. That is 7,080 rows over 15 sequential
   * requests on the current database, roughly 36 MB of JSON, three quarters of it
   * the proposal_received and agenda_addition prose columns that the table shows
   * ellipsised anyway. Nothing rendered until the last page landed, which is
   * exactly what the long wait and the empty table were. See
   * PaperReviewViewSet.stats.
   */
  const fetchStats = useCallback(() => paperReviewApi.stats(period), [period]);
  const { data: stats, loading: statsLoading, refetchQuiet: reloadStats } =
    useFetch(fetchStats, [period], { initialData: {} });
  const total = stats && stats.total != null ? stats.total : null;

  /**
   * The table keeps its own rows current in server mode; this pair is for the
   * count beside it, which is a separate query and would otherwise stay on
   * whatever it read at mount. `keepRefetch` wraps the table's refetch in an
   * updater because React calls a bare function passed to a state setter instead
   * of storing it.
   */
  const [tableRefetch, setTableRefetch] = useState(null);
  const keepRefetch = useCallback((fn) => setTableRefetch(() => fn), []);
  const { refreshNow: refreshStats } = useLiveData(reloadStats, { resources: ['paper-reviews'] });
  const refresh = useCallback(() => {
    if (tableRefetch) tableRefetch();
    refreshStats();
  }, [tableRefetch, refreshStats]);
  // 'paper-reviews' is the router path (config/urls.py), not the module key used
  // for permissions — the two differ here and a wrong one 404s the schema fetch.
  const bulk = useBulkUpdate('paper-reviews', refresh);
  const [editReview, setEditReview] = useState(null);
  const [newOpen, setNewOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  /**
   * C1 — a SERVER filter now, not a narrowing of the loaded rows.
   *
   * It was handed to DataTable as `scope`, which was right while the browser held
   * every review. Under server paging that would filter the fifty rows on screen
   * and label the result "duplicates only", so a page that happened to carry none
   * would read as though there were none at all. ?has_duplicates=true is the same
   * predicate the annotation drives, PaperReviewFilter.filter_has_duplicates,
   * evaluated over the whole scoped set, and the footer reports how many matched.
   */
  const [dupesOnly, setDupesOnly] = useState(false);

  if (!canView('paper_review')) return <NoAccessPage module="Paper Review" />;

  return (
    <>
      <PageHead title="Paper Review"
        actions={<>
          {can('create', 'paper_review') ? <button className="btn btn-s" onClick={() => setImportOpen(true)}><Icon name="download" size={15} />Import</button> : null}
          {can('create', 'paper_review') ? <button className="btn btn-p" onClick={() => setNewOpen(true)}><Icon name="plus" size={15} />New review</button> : null}
          <ClearAllButton noun="paper review" count={total}
            onClear={paperReviewApi.clearAll} onCleared={refresh}
            extra="Proposal submissions generated from these reviews are NOT deleted — they are unlinked from the review and stay in Proposal Submission, which has its own clear-all." />
        </>} />

      <DateRangeFilter value={period} onChange={setPeriod} loading={statsLoading}
        count={total} noun="reviews" note="by paper submission date" />

      <DataTable
        /**
         * SERVER MODE. Django does the filtering, ordering and paging, so the
         * first paint costs one 50-row request instead of a fifteen-request walk
         * of the whole table. It is also what replaces the empty-table flash with
         * a "Loading reviews…" spinner: in-memory mode had nothing to render but
         * the "No Paper Reviews Found" state until every page had landed, so the
         * table truthfully reported having no rows while it was still fetching
         * them.
         *
         * No mapRow — api/paperReview.js has no toFrontend(), so the column keys
         * are already the serializer's field names.
         */
        server={{ resource: 'paper-reviews' }}
        // Real query params rather than filter_spec criteria, and neither could
        // be one: the window is a COALESCE over two columns, and has_duplicates
        // reads a Subquery annotation.
        serverParams={{ period, has_duplicates: dupesOnly ? 'true' : undefined }}
        onServerReady={keepRefetch}
        noun="reviews" pageSize={50} infinite defaultSort={{ key: 'paper_submission_date', dir: 'desc' }} searchPlaceholder="Search speaker, company, event…"
        select={can('update', 'paper_review')}
        /* The label carries no count any more. It was counted off the loaded
           rows, and asking the server for it costs a per-row Subquery evaluation
           over the whole table — measured at 389 ms here, against 13 ms for the
           plain total — to label a checkbox. Ticking it now shows the real figure
           in the footer, taken from the count the list response already carries. */
        extraToolbar={(
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: 'var(--text-3)', whiteSpace: 'nowrap' }}
            title="A duplicate is another review with the same speaker email on the same event. The count only covers events you are assigned to, so a duplicate on someone else's event reads as none.">
            <input type="checkbox" className="ck" checked={dupesOnly}
              onChange={(e) => setDupesOnly(e.target.checked)} />
            Duplicates only
          </label>
        )}
        groups={[
          { key: 'id', label: 'Identification' }, { key: 'sp', label: 'Speaker & company' }, { key: 'sc', label: 'Review scoring' },
          { key: 'ag', label: 'Agenda & feedback' }, { key: 'ct', label: 'Proposal content' },
        ]}
        hiddenDefault={['speaker_email_ref', 'research_email_ref', 'internal_footnotes', 'feedback_to_speaker']}
        cols={[
          /* event_code and company_name carry NO `opts`. Those dropdowns were
             built by scanning the loaded rows, which under server paging means
             the fifty on screen, so an event absent from page one would look like
             an event with no reviews. Both are registered filter_spec fields, so
             a text condition on either is still evaluated by the database over
             every row. grade and session_location_on_agenda keep theirs; those
             lists are constants, not a scan of the data. */
          { key: 'event_code', serverOrdering: 'event_code', label: 'Event Code', group: 'id', cell: (v) => <span className="mono lnk">{v}</span> },
          { key: 'paper_submission_date', serverOrdering: 'paper_submission_date', label: 'Paper Submission Date', group: 'id', cell: (v) => (v ? fdate(v) : <span className="dim">—</span>) },
          { key: 'speaker_email_ref', serverOrdering: 'speaker_email_ref', label: 'Speaker Email Ref', group: 'id', cell: (v) => v || <span className="dim">—</span> },
          { key: 'research_email_ref', serverOrdering: 'research_email_ref', label: 'Research Email Ref', group: 'id', cell: (v) => v || <span className="dim">—</span> },
          // C1 — the row marker. Advisory only: a resubmission is legitimate, so
          // this never blocks anything. The tooltip carries the scope caveat,
          // because the count is computed over the caller's own events only.
          { key: 'duplicate_count', serverOrdering: 'duplicate_count', label: 'Duplicate?', group: 'id', num: true,
            cell: (v) => ((v || 0) > 0
              ? <span className="tg bg-amber" title={`${v} other review${v === 1 ? '' : 's'} with this speaker's email on this event, within your assigned events`}>{v}</span>
              : <span className="dim">—</span>) },
          { key: 'speaker_name', serverOrdering: 'speaker_name', label: 'Speaker Name', group: 'sp', cls: 'st', cell: (v, r) => <Who name={v} sub={r.company_name} /> },
          { key: 'company_name', serverOrdering: 'company_name', label: 'Company Name', group: 'sp' },
          { key: 'email', serverOrdering: 'email', label: 'Email Address of the Speaker', group: 'sp', cell: (v) => <span style={{ fontSize: 11.5 }}>{v}</span> },
          { key: 'linkedin_speaker', serverOrdering: 'linkedin_speaker', label: 'LinkedIn Profile of Speaker', group: 'sp', cell: (v) => (v ? <a href={v} target="_blank" rel="noreferrer" className="mono lnk" style={{ fontSize: 11 }}>{v}</a> : <span className="dim">—</span>) },
          { key: 'linkedin_followers', serverOrdering: 'linkedin_followers', label: 'LinkedIn Followers Count', group: 'sp', num: true, cell: (v) => (v == null ? <span className="dim">—</span> : nf(v)) },
          { key: 'linkedin_company', serverOrdering: 'linkedin_company', label: 'LinkedIn Company Profile', group: 'sp', cell: (v) => (v ? <a href={v} target="_blank" rel="noreferrer" className="mono lnk" style={{ fontSize: 11 }}>{v}</a> : <span className="dim">—</span>) },
          { key: 'nos', serverOrdering: 'nos', label: 'NOS?', group: 'sp', cell: (v) => (v ? <span className="tg bg-teal">Yes</span> : <span className="dim">No</span>) },
          // The six rubric criteria are model columns under their own names, so
          // each orders server side under that name; PaperReviewViewSet spreads
          // CRITERIA_FIELDS into ordering_fields from the same single source.
          ...PAPER_REVIEW_CRITERIA.map((c) => ({
            key: c.key, serverOrdering: c.key, label: `${c.label} (${c.max})`, group: 'sc', num: true,
            cell: (v) => (v == null || v === '' ? <span className="dim">—</span> : nf(v)),
          })),
          { key: 'proposal_score', serverOrdering: 'proposal_score', label: 'Proposal Score', group: 'sc', num: true, cell: (v) => (v == null ? <span className="dim">—</span> : <b style={{ color: 'var(--text)' }}>{nf(v)}</b>) },
          { key: 'grade', serverOrdering: 'grade', label: 'Grade', group: 'sc', cell: (v) => (v ? <Dot tone={PAPER_GRADE_TONE[v] || 'neutral'}>{v}</Dot> : <span className="dim">—</span>), opts: () => PAPER_GRADES },
          { key: 'session_location_on_agenda', serverOrdering: 'session_location_on_agenda', label: 'Session or Location on Agenda', group: 'ag', opts: () => PAPER_SESSION_OPTIONS },
          { key: 'internal_footnotes', label: 'Internal Footnotes', group: 'ag', cell: (v) => v || <span className="dim">—</span> },
          { key: 'feedback_to_speaker', serverOrdering: 'feedback_to_speaker', label: 'Feedback to Speaker or Request Information', group: 'ag', cell: (v) => v || <span className="dim">—</span> },
          { key: 'theme', serverOrdering: 'theme', label: 'Theme', group: 'ct' },
          { key: 'proposal_received', serverOrdering: 'proposal_received', label: 'Proposal Received', group: 'ct', cell: (v) => (v ? <span className="dim" style={{ maxWidth: 260, display: 'inline-block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', verticalAlign: 'bottom' }}>{v}</span> : <span className="dim">—</span>) },
          { key: 'agenda_addition', serverOrdering: 'agenda_addition', label: 'Agenda Addition', group: 'ct', cell: (v) => (v ? <span className="dim" style={{ maxWidth: 260, display: 'inline-block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', verticalAlign: 'bottom' }}>{v}</span> : <span className="dim">—</span>) },
        ]}
        card={(r) => (
          <div className="rc">
            <div className="rc-t"><Who name={r.speaker_name} /><span style={{ flex: 1 }} /><span className="mono" style={{ color: 'var(--t-600)' }}>{r.event_code}</span></div>
            <div className="rc-m">
              <div><div className="l">Company</div><div className="v">{r.company_name}</div></div>
              <div><div className="l">Score</div><div className="v">{r.proposal_score ?? '—'}</div></div>
              <div><div className="l">Grade</div><div className="v">{r.grade || '—'}</div></div>
              <div><div className="l">Agenda</div><div className="v">{r.session_location_on_agenda || '—'}</div></div>
            </div>
          </div>
        )}
        onRow={can('update', 'paper_review') ? (r) => setEditReview(r) : undefined}
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
        <BulkUpdateModal {...bulk.props} rowLabel="review" totalMatching={total} />
      ) : null}

      {editReview ? <PaperReviewFormModal review={editReview} onClose={() => setEditReview(null)} onSaved={refresh} /> : null}
      {newOpen ? <PaperReviewFormModal onClose={() => setNewOpen(false)} onSaved={refresh} /> : null}
      {importOpen ? <PaperReviewImportModal onClose={() => setImportOpen(false)} onImported={refresh} /> : null}
    </>
  );
}
