-- ============================================================================
-- 2026_08_perf_indexes_2.sql
-- Second pass at the database-level index drift sync_indexes reports.
--
-- HAND-RUN ONLY. Run with:
--     psql "$DATABASE_URL" -f backend/sql/2026_08_perf_indexes_2.sql
--
-- CONCURRENTLY cannot run inside a transaction block; psql runs each statement
-- in its own transaction by default. Do NOT wrap in BEGIN/COMMIT or psql -1.
--
-- DEPLOY ORDER: none. Nothing in the Python depends on these; they are pure
-- planner input. Run before or after the deploy.
--
-- SELECTION RULE, AND WHY MOST OF THE DRIFT IS NOT HERE
-- The gate for this pass was: any drifted column whose table has more than
-- 10,000 live rows, PLUS every foreign key on team_activity_logs regardless of
-- size, because the activity drawer select_relateds four of them at once.
--
-- Measured 2026-08-19 against linq_crm, NO drifted table exceeds 10,000 rows.
-- The largest is book_events at 981. So the row-count gate selected NOTHING,
-- and this file is exactly the team_activity_logs foreign keys. The other 30
-- drifted columns are deferred and listed by name and row count in the
-- workstream report rather than silently dropped. They remain visible in
-- `manage.py sync_indexes`, which is where that decision should be revisited
-- once any of those tables actually grows.
--
-- NAMES ARE DJANGO'S OWN, NOT HAND-PICKED
-- Every statement below is generated output, from
--     schema_editor._create_index_sql(TeamActivityLog, fields=[<fk field>])
-- so each index carries the exact hash-suffixed name Django's migration state
-- already believes exists. That is deliberate and is the difference from
-- 2026_08_perf_indexes.sql, which had to invent names: matching Django's names
-- means a future `migrate` against an empty database produces these same
-- indexes rather than a second, duplicate set under different names.
--
-- Consequently there is NO migration file and NO django_migrations insert for
-- this pass. These columns are ForeignKeys, so model state ALREADY declares
-- them indexed; there is no model change to record. Inserting a row naming a
-- migration file that does not exist would break that app's next real migrate,
-- which is the trap Prompt 1 caught.
-- ============================================================================


-- == SECTION 1: DDL ==========================================================

-- team_activity_logs, 188 rows. Below the row-count gate, included anyway
-- because the team activity drawer select_relateds team, user, source_team and
-- destination_team together, so a single drawer open is four unindexed lookups.
-- At 188 rows these are cheap either way today; they are here so the drawer does
-- not degrade as the table grows, and the whole set costs a few kilobytes.
CREATE INDEX CONCURRENTLY IF NOT EXISTS team_activity_logs_destination_team_id_7e17f262
  ON team_activity_logs (destination_team_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS team_activity_logs_moved_by_id_c480b640
  ON team_activity_logs (moved_by_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS team_activity_logs_source_team_id_0fc16a0a
  ON team_activity_logs (source_team_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS team_activity_logs_team_id_9d332a8d
  ON team_activity_logs (team_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS team_activity_logs_user_id_693dba4b
  ON team_activity_logs (user_id);

ANALYZE team_activity_logs;


-- == SECTION 2: MIGRATION ROWS ===============================================
-- INTENTIONALLY EMPTY. See the header: these are ForeignKey auto-indexes, so
-- model state already declares them and no migration file was created for this
-- pass. There is therefore nothing to record as applied, and inserting a row
-- for a nonexistent migration file would break the next real migrate on that
-- app. This section is kept, empty and explained, rather than omitted, so the
-- four-section structure stays comparable across the workstream's SQL files.


-- == SECTION 3: ROLLBACK (run only to undo) ==================================
-- Every statement commented out. Nothing in the Python depends on these, so
-- rolling back needs no coordination with a deploy.
--
-- DROP INDEX CONCURRENTLY IF EXISTS team_activity_logs_destination_team_id_7e17f262;
-- DROP INDEX CONCURRENTLY IF EXISTS team_activity_logs_moved_by_id_c480b640;
-- DROP INDEX CONCURRENTLY IF EXISTS team_activity_logs_source_team_id_0fc16a0a;
-- DROP INDEX CONCURRENTLY IF EXISTS team_activity_logs_team_id_9d332a8d;
-- DROP INDEX CONCURRENTLY IF EXISTS team_activity_logs_user_id_693dba4b;
--
-- No django_migrations DELETE, because Section 2 inserts nothing.


-- == SECTION 4: VERIFY =======================================================

-- 4.1  All 5 indexes exist AND are valid.
SELECT c.relname AS index_name, i.indisvalid AS is_valid
FROM   pg_index i
JOIN   pg_class c ON c.oid = i.indexrelid
WHERE  c.relname IN (
  'team_activity_logs_destination_team_id_7e17f262',
  'team_activity_logs_moved_by_id_c480b640',
  'team_activity_logs_source_team_id_0fc16a0a',
  'team_activity_logs_team_id_9d332a8d',
  'team_activity_logs_user_id_693dba4b')
ORDER BY c.relname;
-- Expect 5 rows, every is_valid = t.

-- 4.2  Expect exactly 5.
SELECT count(*) AS present_expect_5 FROM pg_indexes
WHERE  indexname IN (
  'team_activity_logs_destination_team_id_7e17f262',
  'team_activity_logs_moved_by_id_c480b640',
  'team_activity_logs_source_team_id_0fc16a0a',
  'team_activity_logs_team_id_9d332a8d',
  'team_activity_logs_user_id_693dba4b');

-- 4.3  No INVALID index anywhere, which would mean an interrupted CONCURRENTLY
--      build that the planner will never use.
SELECT c.relname AS invalid_index
FROM   pg_index i JOIN pg_class c ON c.oid = i.indexrelid
WHERE  NOT i.indisvalid;
-- Expect 0 rows.

-- 4.4  Every team_activity_logs foreign key is now covered. Expect 0 rows back;
--      each row returned is an FK column still without a leading index.
SELECT a.attname AS uncovered_fk_column
FROM   pg_attribute a
WHERE  a.attrelid = 'team_activity_logs'::regclass
  AND  a.attname IN ('destination_team_id','moved_by_id','source_team_id',
                     'team_id','user_id')
  AND  NOT EXISTS (
        SELECT 1 FROM pg_index i
        WHERE i.indrelid = a.attrelid
          AND (i.indkey::int2[])[0] = a.attnum);
