-- ============================================================================
-- 2026_08_perf_indexes.sql
-- Performance indexes for list-endpoint sorts, RBAC filters, and FK joins.
--
-- HAND-RUN ONLY. Run with:
--     psql "$DATABASE_URL" -f backend/sql/2026_08_perf_indexes.sql
--
-- CONCURRENTLY cannot run inside a transaction block; psql runs each statement
-- in its own transaction by default, which is exactly what is needed. Do NOT
-- wrap this file in BEGIN/COMMIT, and do NOT run it through a client that opens
-- an implicit transaction (psql -1, or a Django RunSQL operation).
--
-- IF NOT EXISTS makes every statement idempotent, so a re-run after a partial
-- failure is safe and cheap.
--
-- ORDERING SPELLING IS LOAD-BEARING. PostgreSQL's default for DESC is
-- NULLS FIRST. Django's models.Index(fields=["-created_at", "-id"]) therefore
-- emits plain DESC, while the two book_events indexes are declared as explicit
-- expressions with .desc(nulls_last=True) and emit DESC NULLS LAST. Those are
-- DIFFERENT indexes. Each statement below is byte-identical to what Django's
-- own schema editor generates for the matching Meta.indexes entry, verified by
-- calling Index.create_sql() against the real models; changing a NULLS clause
-- here silently decouples the database from model state.
-- ============================================================================


-- == SECTION 1: DDL ==========================================================

-- ---------------------------------------------------------------------------
-- 1.1  book_events, declared in BookEvent.Meta.indexes
-- ---------------------------------------------------------------------------
-- Bookings default sort. request_date is the column book_delegate/views.py
-- orders by, and it carried no index of any kind. The pk tiebreak is what
-- StableOrderingFilter appends, so the index answers the whole ORDER BY rather
-- than leading it and re-sorting the ties.
CREATE INDEX CONCURRENTLY IF NOT EXISTS book_events_reqdate_id_idx
  ON book_events (request_date DESC NULLS LAST, id DESC);

-- Invoice-date sort, same shape.
CREATE INDEX CONCURRENTLY IF NOT EXISTS book_events_invdate_id_idx
  ON book_events (invoice_date DESC NULLS LAST, id DESC);

-- ---------------------------------------------------------------------------
-- 1.2  book_events, NOT expressible in Meta, SQL-file only
-- ---------------------------------------------------------------------------
-- The period window filters on "booked on", which is request_date when present
-- and invoice_date otherwise. An expression index is the only thing that can
-- serve COALESCE as an indexed lookup; Django's Meta.indexes cannot express a
-- COALESCE over two columns, so this one lives here and nowhere else. It is
-- deliberately absent from book_event/migrations/0022_perf_indexes.py.
CREATE INDEX CONCURRENTLY IF NOT EXISTS book_events_booked_on_expr_idx
  ON book_events ((COALESCE(request_date, invoice_date)) DESC NULLS LAST);

-- ---------------------------------------------------------------------------
-- 1.3  Columns the MODELS believe are indexed and the DATABASE is missing
-- ---------------------------------------------------------------------------
-- Everything in this block is declared db_index=True or is a ForeignKey, so
-- Django's migration state records an auto-named index for each one, and none
-- of them exist in the database. Verified 2026-08-19 against linq_crm by
-- reading pg_index directly; this is the same drift class that sync_indexes was
-- written for, and the reason Task 3d now reads the database instead of the
-- migration graph.
--
-- They are created under explicit names rather than Django's hash-suffixed auto
-- names because an auto name is not reproducible by hand. Coverage is what the
-- planner cares about; the name only has to be stable and unique. No
-- Meta.indexes entry and no migration accompanies these, because model state
-- ALREADY claims the column is indexed; adding one would create a second,
-- duplicate index on a future migrate against an empty database.

-- RBAC ORs sales_executive/team_leader into every non-admin bookings query, so
-- both branches are read on effectively every request a non-admin makes.
CREATE INDEX CONCURRENTLY IF NOT EXISTS book_events_sales_exec_idx
  ON book_events (sales_executive_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS book_events_team_leader_idx
  ON book_events (team_leader_id);

-- Event metrics join on (event_code, edition); event_code is already indexed.
CREATE INDEX CONCURRENTLY IF NOT EXISTS book_events_edition_idx
  ON book_events (edition);

-- Channel grouping on the dashboard.
CREATE INDEX CONCURRENTLY IF NOT EXISTS book_events_source_idx
  ON book_events (source);

-- webhook_events select_related targets on the log listing. Both are
-- ForeignKeys, both absent from the database.
CREATE INDEX CONCURRENTLY IF NOT EXISTS wh_ev_api_key_idx
  ON webhook_events (api_key_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS wh_ev_created_booking_idx
  ON webhook_events (created_booking_id);

-- users had exactly two indexes in the database, its pk and the username
-- unique. team_id drives permission resolution and mapped_lead_id the team
-- grid; both are walked per request.
CREATE INDEX CONCURRENTLY IF NOT EXISTS users_team_idx
  ON users (team_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS users_mapped_lead_idx
  ON users (mapped_lead_id);

-- events: the _owner_by_event walk, the registry lookup, and the live/upcoming
-- filters.
CREATE INDEX CONCURRENTLY IF NOT EXISTS events_sales_exec_idx
  ON events (sales_executive_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS events_master_code_idx
  ON events (master_code);
CREATE INDEX CONCURRENTLY IF NOT EXISTS events_status_idx
  ON events (status);

-- ---------------------------------------------------------------------------
-- 1.4  tickets, declared in Ticket.Meta.indexes
-- ---------------------------------------------------------------------------
-- Plain DESC, not DESC NULLS LAST: these are fields=[...] declarations and this
-- is what Django emits for them. See the header note.
CREATE INDEX CONCURRENTLY IF NOT EXISTS tickets_created_id_idx
  ON tickets (created_at DESC, id DESC);
CREATE INDEX CONCURRENTLY IF NOT EXISTS tickets_status_created_id_idx
  ON tickets (status, created_at DESC, id DESC);

-- ---------------------------------------------------------------------------
-- 1.5  action_logs, declared in ActionLog.Meta.indexes
-- ---------------------------------------------------------------------------
-- This table had exactly one index in the database, its primary key. The
-- composite leads with user_id so it also serves the plain FK lookup, which is
-- why no separate single-column user_id index is created.
CREATE INDEX CONCURRENTLY IF NOT EXISTS action_logs_user_created_idx
  ON action_logs (user_id, created_at DESC);
CREATE INDEX CONCURRENTLY IF NOT EXISTS action_logs_created_idx
  ON action_logs (created_at DESC);

-- ---------------------------------------------------------------------------
-- 1.6  webhook_events, paper_reviews, proposal_submissions, Meta-declared
-- ---------------------------------------------------------------------------
-- created_at is WebhookLog.Meta.ordering, so it is what direct ORM iteration
-- and any caller that does not pass ?ordering= sorts by. wh_ev_created_idx
-- already leads correctly but leaves the pk tiebreak to a sort over 130,304
-- rows.
CREATE INDEX CONCURRENTLY IF NOT EXISTS wh_ev_created_id_idx
  ON webhook_events (created_at DESC, id DESC);

-- Exactly PaperReview.Meta.ordering. The existing single-column
-- paper_submission_date index cannot answer it as an ordered scan: the column
-- is nullable and heavily tied, so every page re-sorted the ties.
CREATE INDEX CONCURRENTLY IF NOT EXISTS paper_reviews_subdate_id_idx
  ON paper_reviews (paper_submission_date DESC, id DESC);

-- Exactly ProposalSubmission.Meta.ordering; same reasoning, same shape.
CREATE INDEX CONCURRENTLY IF NOT EXISTS proposal_subs_subdate_id_idx
  ON proposal_submissions (submission_date DESC, id DESC);

-- ---------------------------------------------------------------------------
-- 1.7  DELIBERATELY NOT CREATED
-- ---------------------------------------------------------------------------
-- book_delegates (invoice_id), THE COLUMN DOES NOT EXIST. BookDelegate.invoice
--   is declared with to_field="invoice_number" and db_column="invoice_number",
--   so the join column is book_delegates.invoice_number, a varchar, not a
--   bigint invoice_id. It is already the leading column of
--   book_delega_invoice_3737aa_idx, the Meta.indexes entry ["invoice","email"],
--   which is present in the database. Creating anything here would duplicate an
--   index that already exists, on a column name that does not.
--
-- webhook_events (status), wh_ev_status_idx is declared in
--   WebhookLog.Meta.indexes and is present in the database. The dashboard's
--   failed-count read is already served.


-- == SECTION 2: RECORD THE STATE-ONLY MIGRATIONS AS APPLIED ==================
-- The matching Django migration files exist so makemigrations stays clean and
-- model state matches the DB; they must never be executed by migrate, so they
-- are recorded as applied here. App labels and migration names match the files
-- in Task 3c exactly.
--
-- book_delegate and events are ABSENT from this list on purpose: neither
-- received a Meta.indexes entry in this workstream, so neither has a
-- 00XX_perf_indexes migration to record. Inserting a row naming a migration
-- file that does not exist would break the next real migrate on those apps.

INSERT INTO django_migrations (app, name, applied)
VALUES
  ('book_event',          '0022_perf_indexes', NOW()),
  ('ticket_central',      '0006_perf_indexes', NOW()),
  ('webhooks',            '0007_perf_indexes', NOW()),
  ('accounts',            '0025_perf_indexes', NOW()),
  ('paper_review',        '0006_perf_indexes', NOW()),
  ('proposal_submission', '0005_perf_indexes', NOW())
ON CONFLICT DO NOTHING;


-- == SECTION 3: ROLLBACK (run only to undo) ==================================
-- Every statement here is commented out. Uncomment the whole block to revert.
-- DROP INDEX CONCURRENTLY is also non-transactional, same rule as SECTION 1.
--
-- DROP INDEX CONCURRENTLY IF EXISTS book_events_reqdate_id_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS book_events_invdate_id_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS book_events_booked_on_expr_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS book_events_sales_exec_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS book_events_team_leader_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS book_events_edition_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS book_events_source_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS wh_ev_api_key_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS wh_ev_created_booking_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS users_team_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS users_mapped_lead_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS events_sales_exec_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS events_master_code_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS events_status_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS tickets_created_id_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS tickets_status_created_id_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS action_logs_user_created_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS action_logs_created_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS wh_ev_created_id_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS paper_reviews_subdate_id_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS proposal_subs_subdate_id_idx;
--
-- DELETE FROM django_migrations WHERE name IN (
--     '0022_perf_indexes','0006_perf_indexes','0007_perf_indexes',
--     '0025_perf_indexes','0005_perf_indexes')
--   AND app IN ('book_event','ticket_central','webhooks','accounts',
--               'paper_review','proposal_submission');


-- == SECTION 4: VERIFY =======================================================
-- Every one of the 21 names below must come back. A missing name means its
-- CREATE failed; scroll up for the error and re-run the file, which is safe.
SELECT indexname, tablename FROM pg_indexes
WHERE indexname IN (
  'book_events_reqdate_id_idx',      'book_events_invdate_id_idx',
  'book_events_booked_on_expr_idx',  'book_events_sales_exec_idx',
  'book_events_team_leader_idx',     'book_events_edition_idx',
  'book_events_source_idx',          'wh_ev_api_key_idx',
  'wh_ev_created_booking_idx',       'users_team_idx',
  'users_mapped_lead_idx',           'events_sales_exec_idx',
  'events_master_code_idx',          'events_status_idx',
  'tickets_created_id_idx',          'tickets_status_created_id_idx',
  'action_logs_user_created_idx',    'action_logs_created_idx',
  'wh_ev_created_id_idx',            'paper_reviews_subdate_id_idx',
  'proposal_subs_subdate_id_idx'
)
ORDER BY tablename, indexname;

-- Expect exactly 21.
SELECT count(*) AS indexes_present_expect_21 FROM pg_indexes
WHERE indexname IN (
  'book_events_reqdate_id_idx',      'book_events_invdate_id_idx',
  'book_events_booked_on_expr_idx',  'book_events_sales_exec_idx',
  'book_events_team_leader_idx',     'book_events_edition_idx',
  'book_events_source_idx',          'wh_ev_api_key_idx',
  'wh_ev_created_booking_idx',       'users_team_idx',
  'users_mapped_lead_idx',           'events_sales_exec_idx',
  'events_master_code_idx',          'events_status_idx',
  'tickets_created_id_idx',          'tickets_status_created_id_idx',
  'action_logs_user_created_idx',    'action_logs_created_idx',
  'wh_ev_created_id_idx',            'paper_reviews_subdate_id_idx',
  'proposal_subs_subdate_id_idx'
);

-- An INVALID index means a CONCURRENTLY build was interrupted; it must be
-- dropped and re-created, it will never be used by the planner.
SELECT c.relname AS invalid_index
FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
WHERE NOT i.indisvalid;

-- The six migration rows Section 2 inserts. Expect 6.
SELECT app, name FROM django_migrations
WHERE name IN ('0022_perf_indexes','0006_perf_indexes','0007_perf_indexes',
               '0025_perf_indexes','0005_perf_indexes')
  AND app IN ('book_event','ticket_central','webhooks','accounts',
              'paper_review','proposal_submission')
ORDER BY app;

-- Refresh planner statistics so the new indexes are considered immediately.
ANALYZE;
