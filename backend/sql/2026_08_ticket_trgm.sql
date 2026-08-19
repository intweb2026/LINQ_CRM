-- ============================================================================
-- 2026_08_ticket_trgm.sql
-- Trigram GIN indexes for Ticket Central search.
--
-- HAND-RUN ONLY. Run with:
--     psql "$DATABASE_URL" -f backend/sql/2026_08_ticket_trgm.sql
--
-- CREATE EXTENSION needs database-owner rights, which the application role
-- deliberately does not have. That is the second reason this is hand-run and
-- not left to migrate; the first is the project's standing schema convention.
--
-- CONCURRENTLY cannot run inside a transaction block; psql runs each statement
-- in its own transaction by default. Do NOT wrap in BEGIN/COMMIT or use psql -1.
--
-- DEPLOY ORDER. Unlike 2026_08_booked_on.sql this file has NO ordering
-- constraint against the Python. The trimmed search_fields list is correct with
-- or without these indexes, and the indexes are inert until a search runs. Run
-- it before or after; running it first simply means the first search is fast.
--
-- Every CREATE INDEX below is Django's own output, taken verbatim from
--     python manage.py sqlmigrate ticket_central 0007
-- with IF NOT EXISTS added so a re-run is safe.
--
-- WHY THE EXPRESSION AND NOT THE BARE COLUMN
-- Django's PostgreSQL backend compiles __icontains to
--     UPPER("tickets"."event_code"::text) LIKE UPPER(%s)
-- A gin_trgm_ops index on the bare column is NOT matched by the planner against
-- an UPPER() expression: it builds fine, the plan never changes, and the work
-- looks done while nothing improved. These index UPPER(col), which is what the
-- planner matches. No explicit ::text cast is written because upper() takes
-- text, so PostgreSQL normalises UPPER("event_code") on a varchar column to
-- upper((event_code)::text) when storing the expression — read it back from
-- pg_indexes.indexdef in SECTION 4 to confirm.
-- ============================================================================


-- == SECTION 1: DDL ==========================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX CONCURRENTLY IF NOT EXISTS tickets_ticketnum_trgm_idx
  ON tickets USING gin ((UPPER(ticket_number)) gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS tickets_event_code_trgm_idx
  ON tickets USING gin ((UPPER(event_code)) gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS tickets_purpose_trgm_idx
  ON tickets USING gin ((UPPER(purpose)) gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS tickets_organizer_trgm_idx
  ON tickets USING gin ((UPPER(organizer)) gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS tickets_competitor_trgm_idx
  ON tickets USING gin ((UPPER(competitor_event_name)) gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS tickets_assigned_mr_trgm_idx
  ON tickets USING gin ((UPPER(assigned_mr)) gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS tickets_assign_name_trgm_idx
  ON tickets USING gin ((UPPER(assign_name)) gin_trgm_ops);

ANALYZE tickets;


-- == SECTION 2: RECORD THE STATE-ONLY MIGRATION AS APPLIED ===================
-- ticket_central/migrations/0007_ticket_trgm_indexes.py exists so model state
-- matches the DB and makemigrations stays clean; it must never be executed by
-- migrate, so it is recorded as applied here.

INSERT INTO django_migrations (app, name, applied)
VALUES ('ticket_central', '0007_ticket_trgm_indexes', NOW())
ON CONFLICT DO NOTHING;


-- == SECTION 3: ROLLBACK (run only to undo) ==================================
-- Every statement commented out. Safe to run with the Python still deployed:
-- the trimmed search_fields list works with or without these indexes, it simply
-- reverts to a sequential scan.
--
-- DROP INDEX CONCURRENTLY IF EXISTS tickets_ticketnum_trgm_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS tickets_event_code_trgm_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS tickets_purpose_trgm_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS tickets_organizer_trgm_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS tickets_competitor_trgm_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS tickets_assigned_mr_trgm_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS tickets_assign_name_trgm_idx;
-- DELETE FROM django_migrations
--   WHERE app = 'ticket_central' AND name = '0007_ticket_trgm_indexes';
--
-- pg_trgm is deliberately NOT dropped here. Dropping an extension cascades to
-- every dependent object across the whole database, and nothing is gained by
-- removing it. To remove it anyway, after the DROP INDEXes above:
--   DROP EXTENSION pg_trgm;


-- == SECTION 4: VERIFY =======================================================

-- 4.1  The extension is installed.
SELECT extname, extversion FROM pg_extension WHERE extname = 'pg_trgm';
-- Expect exactly 1 row.

-- 4.2  All 7 indexes exist AND are valid. An INVALID index is present in
--      pg_indexes but the planner will never use it.
SELECT c.relname AS index_name, i.indisvalid AS is_valid
FROM   pg_index i
JOIN   pg_class c ON c.oid = i.indexrelid
WHERE  c.relname IN (
  'tickets_ticketnum_trgm_idx',  'tickets_event_code_trgm_idx',
  'tickets_purpose_trgm_idx',    'tickets_organizer_trgm_idx',
  'tickets_competitor_trgm_idx', 'tickets_assigned_mr_trgm_idx',
  'tickets_assign_name_trgm_idx')
ORDER BY c.relname;
-- Expect 7 rows, every is_valid = t.

-- 4.3  The STORED expression, to confirm PostgreSQL normalised UPPER(col) to
--      upper((col)::text) and so matches the predicate Django emits. If any row
--      here reads upper(col) WITHOUT ::text on a varchar column, the planner
--      will not match it and the index is inert.
SELECT indexname, indexdef FROM pg_indexes
WHERE  indexname LIKE 'tickets_%_trgm_idx'
ORDER  BY indexname;

-- 4.4  Total size of the new indexes, so the write-side cost is a number.
SELECT pg_size_pretty(SUM(pg_total_relation_size(c.oid))) AS trgm_index_total
FROM   pg_class c
WHERE  c.relname LIKE 'tickets_%_trgm_idx';

-- 4.5  THE GATE. This must show a Bitmap Index Scan naming one of the indexes
--      above, and must NOT show "Seq Scan on tickets". The predicate is the one
--      Django actually emits, taken from connection.queries.
EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM tickets
WHERE  UPPER(ticket_number::text)         LIKE UPPER('%summit%')
   OR  UPPER(event_code::text)            LIKE UPPER('%summit%')
   OR  UPPER(purpose::text)               LIKE UPPER('%summit%')
   OR  UPPER(organizer::text)             LIKE UPPER('%summit%')
   OR  UPPER(competitor_event_name::text) LIKE UPPER('%summit%')
   OR  UPPER(assigned_mr::text)           LIKE UPPER('%summit%')
   OR  UPPER(assign_name::text)           LIKE UPPER('%summit%');

-- 4.6  The migration row Section 2 inserts. Expect 1.
SELECT app, name FROM django_migrations
WHERE  app = 'ticket_central' AND name = '0007_ticket_trgm_indexes';
