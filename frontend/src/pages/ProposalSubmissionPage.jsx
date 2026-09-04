import { useCallback, useState } from 'react';
import { ExtLink } from '../components/UI';
import DataTable from '../components/DataTable';
import { Icon } from '../lib/icons';
import { Dot, Who } from '../components/Badge';
import { fdate, nf } from '../lib/helpers';
import { htmlToText } from '../lib/richText';
import {
  PARTICIPATION_TYPES, QC_GRADES, QC_GRADE_TONE, SPEAKER_SLOT_STATUSES, SPEAKER_SLOT_TONE,
  SPONSORSHIP_STATUSES, SPONSORSHIP_TONE, REVENUE_POSSIBILITY, REVENUE_TONE,
  // Booking Status by SE has a KNOWN vocabulary, unlike the panel and risk columns
  // below, because it is read from the bookings pipeline rather than typed here.
  // Reused rather than restated; the pair already backs the Bookings grid.
  PAYMENT_STATUSES, STATUS_TONE,
  PANEL_APPROACHED, PANEL_APPROACHED_TONE, SLOT_REOFFER_STATUSES, SLOT_REOFFER_TONE,
  RISK_LEVELS, RISK_TONE,
  // The SAME list the paper review form offers. agenda_slot is where the bridge
  // writes PaperReview.session_location_on_agenda, and all ten distinct values
  // stored in this column are exactly that vocabulary, so a second list here
  // would be a second thing to keep in step with the data.
  PAPER_SESSION_OPTIONS,
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
  /* EVERY column declares `serverField`. It is what routes a filter condition to
     Django; without it DataTable re-applies the condition in the BROWSER, over
     the rows already fetched. Not one column here carried it, so the comment
     below — that a text condition on these is "evaluated by the database over
     every row" — described an intention rather than the behaviour: every filter
     on this table narrowed the loaded page and the footer counted that page. The
     names match backend/proposal_submission/views.py filter_spec_fields exactly, which is the
     registry the schema endpoint advertises and the only thing a criterion is
     allowed to name. A new column needs its entry there before it can have one
     here; deny-by-default means an unregistered name is dropped back to the
     browser rather than 400ing the list. */
  /* event_code and company_name carry NO `opts`. Those dropdowns were
     built by scanning the loaded rows, which under server paging means
     the fifty on screen. Both are registered filter_spec fields, so a
     text condition on either is still evaluated by the database over
     every row. The status columns keep theirs; those lists are constants
     rather than a scan of the data. */
  { key: 'event_code', serverField: 'event_code', serverOrdering: 'event_code', label: 'Event Code', group: 'id', cell: (v) => <span className="mono lnk">{v}</span> },
  /* READ ONLY, and not stored on the proposal at all; it is annotated from the
     event catalogue by ProposalSubmissionViewSet._annotate_tracker_context. It
     carries serverField and serverOrdering like every other column because that
     annotation is real SQL; the catalogue is the source of truth for an event
     date, so editing it here would be editing a copy. */
  { key: 'event_date', serverField: 'event_date', serverOrdering: 'event_date', label: 'Event Date', type: 'date', group: 'id', cell: (v) => (v ? fdate(v) : <span className="dim">—</span>) },
  /* Also read from the catalogue, the SPEX Manager column is the SPEX team on the
     event. NO opts, since it is a free-text team name maintained there; a pinned
     list here would go stale the first time somebody joins. It is a registered
     filter_spec field, so a text condition on it is evaluated by the database over
     every row. */
  { key: 'spex_manager', serverField: 'spex_manager', serverOrdering: 'spex_manager', label: 'SPEX Manager', group: 'id', cell: (v) => (v ? <Who name={v} avatar={false} /> : <span className="dim">—</span>) },
  { key: 'submission_date', serverField: 'submission_date', serverOrdering: 'submission_date', label: 'Submission Date', type: 'date', group: 'id', cell: (v) => (v ? fdate(v) : <span className="dim">—</span>) },
  { key: 'participation_type', serverField: 'participation_type', serverOrdering: 'participation_type', label: 'Participation Type', group: 'id', cell: (v) => v || <span className="dim">—</span>, opts: () => PARTICIPATION_TYPES },
  // Name only — Company Name has its own column; see PaperReviewPage.
  { key: 'speaker_name', serverField: 'speaker_name', serverOrdering: 'speaker_name', label: 'Speaker Name', group: 'sp', cls: 'st', cell: (v) => <Who name={v} avatar={false} /> },
  { key: 'email', serverField: 'email', serverOrdering: 'email', label: 'Email Address', group: 'sp', cell: (v) => <span style={{ fontSize: 11.5 }}>{v}</span> },
  { key: 'company_name', serverField: 'company_name', serverOrdering: 'company_name', label: 'Company Name', group: 'sp' },
  /* ExtLink rather than a hand-rolled <a>: it resolves the href (an anchor-wrapped
     or scheme-less cell is otherwise a link to nowhere), refuses to linkify text
     that is not an address, opens in its own tab with no handle back to this one,
     and stops the click reaching the row's own open-the-record handler. */
  { key: 'linkedin_speaker', serverField: 'linkedin_speaker', serverOrdering: 'linkedin_speaker', label: 'LinkedIn (Speaker)', group: 'sp', cell: (v) => <ExtLink value={v} className="mono lnk" style={{ fontSize: 11 }} /> },
  { key: 'linkedin_company', serverField: 'linkedin_company', serverOrdering: 'linkedin_company', label: 'LinkedIn (Company)', group: 'sp', cell: (v) => <ExtLink value={v} className="mono lnk" style={{ fontSize: 11 }} /> },
  { key: 'linkedin_followers', serverField: 'linkedin_followers', serverOrdering: 'linkedin_followers', label: 'LinkedIn Followers', group: 'sp', num: true, cell: (v) => (v == null ? <span className="dim">—</span> : nf(v)) },
  { key: 'qc_grade', serverField: 'qc_grade', serverOrdering: 'qc_grade', label: 'QC Grade', group: 'qc', cell: (v) => (v ? <Dot tone={QC_GRADE_TONE[v] || 'neutral'}>{v}</Dot> : <span className="dim">—</span>), opts: () => QC_GRADES },
  { key: 'qc_score', serverField: 'qc_score', serverOrdering: 'qc_score', label: 'QC Score', group: 'qc', num: true, cell: (v) => (v == null ? <span className="dim">—</span> : nf(v)) },
  { key: 'presentation_theme', serverField: 'presentation_theme', serverOrdering: 'presentation_theme', label: 'Presentation Theme', group: 'qc' },
  { key: 'sales_pitch_factor', serverField: 'sales_pitch_factor', serverOrdering: 'sales_pitch_factor', label: 'Sales Pitch Factor', group: 'qc' },
  /* TWO slot columns, and the pair is the point: agenda_slot is what the MRE
     recommended on the paper review, speaking_slot_assignment is what the agenda
     team actually did. They agree on most rows, and the ones where they differ are
     what this grid is for. Same ten options behind both. */
  { key: 'agenda_slot', serverField: 'agenda_slot', serverOrdering: 'agenda_slot', label: 'Slot Recommendation by MRE', group: 'qc', cell: (v) => v || <span className="dim">—</span>, opts: () => PAPER_SESSION_OPTIONS },
  { key: 'speaking_slot_assignment', serverField: 'speaking_slot_assignment', serverOrdering: 'speaking_slot_assignment', label: 'Speaking Slot Assignment', group: 'qc', cell: (v) => v || <span className="dim">—</span>, opts: () => PAPER_SESSION_OPTIONS },
  /* A CHECKBOX, and a different question from Agenda Addition beside it: that one
     is the session outline, this is whether the speaker reached the published
     agenda. Filter options are the raw booleans the backend parses, labelled for
     the reader — same treatment as NOS? on the Paper Review grid. */
  { key: 'added_to_agenda', serverField: 'added_to_agenda', serverOrdering: 'added_to_agenda', label: 'Added to Agenda', group: 'qc',
    cell: (v) => (v ? <span className="tg bg-teal">Yes</span> : <span className="dim">No</span>),
    opts: () => ['true', 'false'], optLabel: (v) => (v === 'true' ? 'Yes' : 'No') },
  { key: 'agenda_addition', serverField: 'agenda_addition', serverOrdering: 'agenda_addition', label: 'Agenda Addition', group: 'qc', cell: proseCell },
  { key: 'speaker_slot_status', serverField: 'speaker_slot_status', serverOrdering: 'speaker_slot_status', label: 'Speaker Slot Status', group: 'st', cell: (v) => (v ? <Dot tone={SPEAKER_SLOT_TONE[v] || 'neutral'}>{v}</Dot> : <span className="dim">—</span>), opts: () => SPEAKER_SLOT_STATUSES },
  /* The tracker's panel track. Panel Approached? and Speaker Slot Re-Offered carry
     `opts` because their vocabularies are CONFIRMED; Panel Topic and Panel Status are
     free text and carry none, which is why neither gets a Dot either. Every one is a
     registered filter_spec field, so a condition on the two text columns is still
     evaluated by the database over every row rather than over the loaded page. */
  { key: 'panel_approached', serverField: 'panel_approached', serverOrdering: 'panel_approached', label: 'Panel Approached?', group: 'st', cell: (v) => (v ? <Dot tone={PANEL_APPROACHED_TONE[v] || 'neutral'}>{v}</Dot> : <span className="dim">—</span>), opts: () => PANEL_APPROACHED },
  { key: 'panel_topic', serverField: 'panel_topic', serverOrdering: 'panel_topic', label: 'Panel Topic', group: 'st' },
  { key: 'panel_status', serverField: 'panel_status', serverOrdering: 'panel_status', label: 'Panel Status', group: 'st' },
  { key: 'speaker_slot_reoffered', serverField: 'speaker_slot_reoffered', serverOrdering: 'speaker_slot_reoffered', label: 'Speaker Slot Re-Offered', group: 'st', cell: (v) => (v ? <Dot tone={SLOT_REOFFER_TONE[v] || 'neutral'}>{v}</Dot> : <span className="dim">—</span>), opts: () => SLOT_REOFFER_STATUSES },
  { key: 'sponsorship_status', serverField: 'sponsorship_status', serverOrdering: 'sponsorship_status', label: 'Sponsorship Status', group: 'st', cell: (v) => (v ? <Dot tone={SPONSORSHIP_TONE[v] || 'neutral'}>{v}</Dot> : <span className="dim">—</span>), opts: () => SPONSORSHIP_STATUSES },
  { key: 'revenue_possibility', serverField: 'revenue_possibility', serverOrdering: 'revenue_possibility', label: 'Revenue Possibility', group: 'st', cell: (v) => (v ? <Dot tone={REVENUE_TONE[v] || 'neutral'}>{v}</Dot> : <span className="dim">—</span>), opts: () => REVENUE_POSSIBILITY },
  { key: 'spex_remarks', serverField: 'spex_remarks', serverOrdering: 'spex_remarks', label: 'SpEx Remarks', group: 'st' },
  { key: 'risk_assessment_live', serverField: 'risk_assessment_live', serverOrdering: 'risk_assessment_live', label: 'Risk Assessment (Live)', group: 'st', cell: (v) => (v ? <Dot tone={RISK_TONE[v] || 'neutral'}>{v}</Dot> : <span className="dim">—</span>), opts: () => RISK_LEVELS },
  { key: 'internal_footnotes_mr', serverField: 'internal_footnotes_mr', label: 'Internal Footnotes (MR)', group: 'mr' },
  { key: 'slot_recommendation_mr', serverField: 'slot_recommendation_mr', label: 'Slot Recommendation by MR', group: 'mr' },
  /* READ ONLY, matched to a book_delegates row on (event_code, lower(email)) —
     the PERSON, not the invoice contact, who is often somebody in accounts who
     never spoke. Blank until the speaker actually books, which is the useful
     signal here: an empty Booking Date on a Confirmed slot is a speaker who has
     not paid. Not editable, because Bookings owns these three values. */
  { key: 'booking_date', serverField: 'booking_date', serverOrdering: 'booking_date', label: 'Booking Date', type: 'date', group: 'bk', cell: (v) => (v ? fdate(v) : <span className="dim">—</span>) },
  { key: 'payment_date', serverField: 'payment_date', serverOrdering: 'payment_date', label: 'Payment Date', type: 'date', group: 'bk', cell: (v) => (v ? fdate(v) : <span className="dim">—</span>) },
  { key: 'booking_status_se', serverField: 'booking_status_se', serverOrdering: 'booking_status_se', label: 'Booking Status by SE', group: 'bk', cell: (v) => (v ? <Dot tone={STATUS_TONE[v] || 'neutral'}>{v}</Dot> : <span className="dim">—</span>), opts: () => PAYMENT_STATUSES },
];

const PROPOSAL_GROUPS = [
  { key: 'id', label: 'Identification' }, { key: 'sp', label: 'Speaker & company' }, { key: 'qc', label: 'Quality & content' },
  { key: 'st', label: 'Status & revenue' }, { key: 'mr', label: 'Internal notes' },
  { key: 'bk', label: 'Booking' },
];
const PROPOSAL_HIDDEN = ['internal_footnotes_mr', 'slot_recommendation_mr'];

export default function ProposalSubmissionPage() {
  const { canView, can } = useSession();
  // Date range, applied by the SERVER over submission_date falling back to
  // created_at — see accounts/period_filter.py. submission_date is nullable, and
  // the fallback is what stops a window hiding every row that never got one.
  //
  // Fixed at 'all' now — the Date Range control was removed from this page
  // (kept on Bookings only), so there is no UI left to change it.
  const period = 'all';
  /**
   * The row count, as its own small aggregate; the table no longer holds the set.
   *
   * This page used to load every proposal into the browser with a fetchAllPages
   * walk and read `.length` off it — 3,752 rows over 8 sequential requests on the
   * current database, none of which rendered until the last one landed. See
   * ProposalSubmissionViewSet.stats.
   */
  const fetchStats = useCallback(() => proposalApi.stats(period), [period]);
  const { data: stats, refetchQuiet: reloadStats } =
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
        // No status tabs or date-range row on this page to fold these into
        // (see BookingsPage / TicketCentralPage), so they ride on the
        // table's own toolbar row instead of a row of their own.
        extraToolbar={<>
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
        </>}
        // 100 rather than 50, for the reason given on the same prop in
        // PaperReviewPage: half as many scroll stops, each one a round trip plus a
        // re-layout of everything already rendered.
        noun="proposals" pageSize={1000} infinite defaultSort={{ key: 'submission_date', dir: 'desc' }} searchPlaceholder="Search speaker, company, event…"
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
