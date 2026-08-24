-- ============================================================================
-- 2026_08_bookings_created_order.sql
-- Index the Bookings table's new default sort: newest ADDED first.
--
-- HAND-RUN ONLY. Run with:
--     psql "$DATABASE_URL" -f backend/sql/2026_08_bookings_created_order.sql
--
-- WHY THIS EXISTS
-- BookDelegateViewSet.ordering moved from ["-booked_on"] to
-- ["-created_at", "-id"]. booked_on is COALESCE(invoice.request_date,
-- invoice.invoice_date) — a BUSINESS date carried on the invoice — so a delegate
-- entered today against an invoice raised three weeks ago sorted three weeks
-- down the table and newly entered work never appeared at the top. created_at is
-- the row's real added time, so it is what "new entries on top" means.
--
-- DEPLOY ORDER IS NOT LOAD-BEARING HERE, unlike 2026_08_booked_on.sql. This file
-- adds an INDEX ONLY: no column, no backfill. created_at already exists and is
-- NOT NULL, so the new ordering is CORRECT with or without this index — without
-- it Postgres just sorts the whole table to return one page of 50. Run it first
-- regardless; there is no reason to serve the slow plan.
--
-- CONCURRENTLY cannot run inside a transaction block; psql runs each statement in
-- its own transaction by default, which is what is needed. Do NOT wrap this file
-- in BEGIN/COMMIT and do NOT run it with psql -1.
--
-- The DDL is Django's own output, taken verbatim from
--     python manage.py sqlmigrate book_delegate 0014
-- per the workstream's rule that hand-written DDL decouples the database from
-- model state. IF NOT EXISTS is the only addition, so a re-run is safe.
-- ============================================================================


-- == SECTION 1: DDL ==========================================================

-- DESC on BOTH columns, matching ORDER BY created_at DESC, id DESC exactly, so
-- the page is one index scan rather than a full sort. No NULLS clause: created_at
-- is DateTimeField(default=timezone.now) and NOT NULL, so the nulls_last spelling
-- that book_delegates_booked_id_idx needs has nothing to guard against here.
-- Django emits:
--   CREATE INDEX CONCURRENTLY "book_delegates_created_id_idx"
--     ON "book_delegates" ("created_at" DESC, "id" DESC);
CREATE INDEX CONCURRENTLY IF NOT EXISTS book_delegates_created_id_idx
  ON book_delegates (created_at DESC, id DESC);

-- Refresh planner statistics so the new index is costed correctly.
ANALYZE book_delegates;


-- == SECTION 2: RECORD THE STATE-ONLY MIGRATION AS APPLIED ===================
-- book_delegate/migrations/0014_created_at_order_index.py exists so
-- makemigrations stays clean and model state matches the DB; it must never be
-- executed by migrate, so it is recorded as applied here. The app label and
-- migration name match that file exactly.

INSERT INTO django_migrations (app, name, applied)
VALUES ('book_delegate', '0014_created_at_order_index', NOW())
ON CONFLICT DO NOTHING;


-- == SECTION 3: ROLLBACK (run only to undo) ==================================
-- Every statement commented out. Uncomment the whole block to revert.
-- Order does not matter against the Python here: dropping the index only makes
-- the sort slower, never wrong.
--
-- DROP INDEX CONCURRENTLY IF EXISTS book_delegates_created_id_idx;
-- DELETE FROM django_migrations
--   WHERE app = 'book_delegate' AND name = '0014_created_at_order_index';
--
-- book_delegates_booked_id_idx is deliberately NOT dropped by this file in
-- either direction: booked_on is still the period window's column
-- (period_date_fields) and still the Request Date column's neighbour, so that
-- index keeps earning its keep.


-- == SECTION 4: VERIFY =======================================================

-- 4.1  The index exists AND is valid. An INVALID index is present in pg_indexes
--      but the planner will never use it, so existence alone is not enough.
SELECT c.relname    AS index_name,
       i.indisvalid AS is_valid
FROM   pg_index i
JOIN   pg_class c ON c.oid = i.indexrelid
WHERE  c.relname = 'book_delegates_created_id_idx';
-- Expect exactly 1 row, is_valid = t.

-- 4.2  The planner actually USES it for the default page. Expect an Index Scan
--      on book_delegates_created_id_idx and NO "Sort" node.
EXPLAIN (COSTS OFF)
SELECT id FROM book_delegates ORDER BY created_at DESC, id DESC LIMIT 50;

-- 4.3  CONTEXT, NOT A GATE. How much real added-time signal created_at carries.
--      import_booking_excel does set it from Zoho's "Added Time"
--      (`if del_dt: bd.created_at = del_dt`), but on the development database
--      that produced nothing: 1,251 of 1,252 rows share 2026-08-14 15:07:xx, the
--      load timestamp, and one hand-entered row carries 2026-08-21. Where that
--      is the shape, the backlog sorts in source-FILE order rather than
--      chronologically — accepted, because the backlog still sits below every
--      later entry, which is the point. Do not treat a low distinct_days as a
--      reason to abort; treat it as a reason to backfill Added Time later.
SELECT count(*)                                AS rows,
       count(DISTINCT created_at::date)         AS distinct_days,
       min(created_at)                          AS earliest,
       max(created_at)                          AS latest
FROM   book_delegates;

-- 4.4  The head of the table, which is what the report was about. The newest
--      created_at must be at the top regardless of its invoice's request_date.
SELECT d.id, d.created_at, d.booked_on, d.invoice_number
FROM   book_delegates d
ORDER  BY d.created_at DESC, d.id DESC
LIMIT  10;

-- 4.5  The migration row Section 2 inserts. Expect 1.
SELECT app, name FROM django_migrations
WHERE  app = 'book_delegate' AND name = '0014_created_at_order_index';
