import { useState } from 'react';
import { EmptyState, PageHead } from '../components/UI';
import DataTable from '../components/DataTable';
import { Icon } from '../lib/icons';
import { Dot, Who } from '../components/Badge';
import { fdate, nf, uniq } from '../lib/helpers';
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

export default function PaperReviewPage() {
  const { canView, can } = useSession();
  const { data: reviews, refetch, refetchQuiet, loading, error } = useFetch(paperReviewApi.list, [], { initialData: [] });
  const REVIEWS = reviews || [];
  // Quiet, so a background refresh cannot replace a table full of rows with the
  // error panel below on one dropped request. `refetch` stays wired to the Try
  // again button, where an error IS what the user is asking about.
  const { refreshNow: refresh } = useLiveData(refetchQuiet, { resources: ['paper-reviews'] });
  // 'paper-reviews' is the router path (config/urls.py), not the module key used
  // for permissions — the two differ here and a wrong one 404s the schema fetch.
  const bulk = useBulkUpdate('paper-reviews', refresh);
  const [editReview, setEditReview] = useState(null);
  const [newOpen, setNewOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  // C1 — a client-side toggle over the loaded rows, matching how every other
  // filter on this page works (the table is in client mode; see the outstanding
  // server-mode work noted in the implementation report). duplicate_count is
  // computed server-side per row, so this filters on a real number rather than
  // recomputing anything in the browser.
  const [dupesOnly, setDupesOnly] = useState(false);
  const VISIBLE = dupesOnly ? REVIEWS.filter((r) => (r.duplicate_count || 0) > 0) : REVIEWS;
  const dupeCount = REVIEWS.filter((r) => (r.duplicate_count || 0) > 0).length;

  if (!canView('paper_review')) return <NoAccessPage module="Paper Review" />;

  return (
    <>
      <PageHead title="Paper Review"
        actions={<>
          {can('create', 'paper_review') ? <button className="btn btn-s" onClick={() => setImportOpen(true)}><Icon name="download" size={15} />Import</button> : null}
          {can('create', 'paper_review') ? <button className="btn btn-p" onClick={() => setNewOpen(true)}><Icon name="plus" size={15} />New review</button> : null}
          <ClearAllButton noun="paper review" count={REVIEWS.length}
            onClear={paperReviewApi.clearAll} onCleared={refresh}
            extra="Proposal submissions generated from these reviews are NOT deleted — they are unlinked from the review and stay in Proposal Submission, which has its own clear-all." />
        </>} />

      {error && !loading ? (
        <EmptyState icon="warn" title="Unable to load paper reviews" body="Something went wrong while loading this data. Please try again in a moment."
          action={<button className="btn btn-s btn-sm" onClick={() => refetch().catch(() => {})}><Icon name="refresh" size={13} />Try again</button>} />
      ) : (
      <DataTable
        rows={VISIBLE} noun="reviews" pageSize={50} defaultSort={{ key: 'paper_submission_date', dir: 'desc' }} searchPlaceholder="Search speaker, company, event…"
        select={can('update', 'paper_review')}
        extraToolbar={dupeCount > 0 || dupesOnly ? (
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: 'var(--text-3)', whiteSpace: 'nowrap' }}
            title="A duplicate is another review with the same speaker email on the same event. The count only covers events you are assigned to, so a duplicate on someone else's event reads as none.">
            <input type="checkbox" className="ck" checked={dupesOnly}
              onChange={(e) => setDupesOnly(e.target.checked)} />
            Duplicates only ({dupeCount})
          </label>
        ) : null}
        groups={[
          { key: 'id', label: 'Identification' }, { key: 'sp', label: 'Speaker & company' }, { key: 'sc', label: 'Review scoring' },
          { key: 'ag', label: 'Agenda & feedback' }, { key: 'ct', label: 'Proposal content' },
        ]}
        hiddenDefault={['speaker_email_ref', 'research_email_ref', 'internal_footnotes', 'feedback_to_speaker']}
        cols={[
          { key: 'event_code', label: 'Event Code', group: 'id', cell: (v) => <span className="mono lnk">{v}</span>, opts: () => uniq(REVIEWS.map((r) => r.event_code)) },
          { key: 'paper_submission_date', label: 'Paper Submission Date', group: 'id', cell: (v) => (v ? fdate(v) : <span className="dim">—</span>) },
          { key: 'speaker_email_ref', label: 'Speaker Email Ref', group: 'id', cell: (v) => v || <span className="dim">—</span> },
          { key: 'research_email_ref', label: 'Research Email Ref', group: 'id', cell: (v) => v || <span className="dim">—</span> },
          // C1 — the row marker. Advisory only: a resubmission is legitimate, so
          // this never blocks anything. The tooltip carries the scope caveat,
          // because the count is computed over the caller's own events only.
          { key: 'duplicate_count', label: 'Duplicate?', group: 'id', num: true,
            cell: (v) => ((v || 0) > 0
              ? <span className="tg bg-amber" title={`${v} other review${v === 1 ? '' : 's'} with this speaker's email on this event, within your assigned events`}>{v}</span>
              : <span className="dim">—</span>) },
          { key: 'speaker_name', label: 'Speaker Name', group: 'sp', cls: 'st', cell: (v, r) => <Who name={v} sub={r.company_name} /> },
          { key: 'company_name', label: 'Company Name', group: 'sp', opts: () => uniq(REVIEWS.map((r) => r.company_name)) },
          { key: 'email', label: 'Email Address of the Speaker', group: 'sp', cell: (v) => <span style={{ fontSize: 11.5 }}>{v}</span> },
          { key: 'linkedin_speaker', label: 'LinkedIn Profile of Speaker', group: 'sp', cell: (v) => (v ? <a href={v} target="_blank" rel="noreferrer" className="mono lnk" style={{ fontSize: 11 }}>{v}</a> : <span className="dim">—</span>) },
          { key: 'linkedin_followers', label: 'LinkedIn Followers Count', group: 'sp', num: true, cell: (v) => (v == null ? <span className="dim">—</span> : nf(v)) },
          { key: 'linkedin_company', label: 'LinkedIn Company Profile', group: 'sp', cell: (v) => (v ? <a href={v} target="_blank" rel="noreferrer" className="mono lnk" style={{ fontSize: 11 }}>{v}</a> : <span className="dim">—</span>) },
          { key: 'nos', label: 'NOS?', group: 'sp', cell: (v) => (v ? <span className="tg bg-teal">Yes</span> : <span className="dim">No</span>) },
          ...PAPER_REVIEW_CRITERIA.map((c) => ({
            key: c.key, label: `${c.label} (${c.max})`, group: 'sc', num: true,
            cell: (v) => (v == null || v === '' ? <span className="dim">—</span> : nf(v)),
          })),
          { key: 'proposal_score', label: 'Proposal Score', group: 'sc', num: true, cell: (v) => (v == null ? <span className="dim">—</span> : <b style={{ color: 'var(--text)' }}>{nf(v)}</b>) },
          { key: 'grade', label: 'Grade', group: 'sc', cell: (v) => (v ? <Dot tone={PAPER_GRADE_TONE[v] || 'neutral'}>{v}</Dot> : <span className="dim">—</span>), opts: () => PAPER_GRADES },
          { key: 'session_location_on_agenda', label: 'Session or Location on Agenda', group: 'ag', opts: () => PAPER_SESSION_OPTIONS },
          { key: 'internal_footnotes', label: 'Internal Footnotes', group: 'ag', cell: (v) => v || <span className="dim">—</span> },
          { key: 'feedback_to_speaker', label: 'Feedback to Speaker or Request Information', group: 'ag', cell: (v) => v || <span className="dim">—</span> },
          { key: 'theme', label: 'Theme', group: 'ct' },
          { key: 'proposal_received', label: 'Proposal Received', group: 'ct', cell: (v) => (v ? <span className="dim" style={{ maxWidth: 260, display: 'inline-block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', verticalAlign: 'bottom' }}>{v}</span> : <span className="dim">—</span>) },
          { key: 'agenda_addition', label: 'Agenda Addition', group: 'ct', cell: (v) => (v ? <span className="dim" style={{ maxWidth: 260, display: 'inline-block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', verticalAlign: 'bottom' }}>{v}</span> : <span className="dim">—</span>) },
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
        <BulkUpdateModal {...bulk.props} rowLabel="review" totalMatching={REVIEWS.length} />
      ) : null}

      {editReview ? <PaperReviewFormModal review={editReview} onClose={() => setEditReview(null)} onSaved={refresh} /> : null}
      {newOpen ? <PaperReviewFormModal onClose={() => setNewOpen(false)} onSaved={refresh} /> : null}
      {importOpen ? <PaperReviewImportModal onClose={() => setImportOpen(false)} onImported={refresh} /> : null}
    </>
  );
}
