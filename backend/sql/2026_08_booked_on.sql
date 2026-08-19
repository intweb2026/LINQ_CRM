-- ============================================================================
-- 2026_08_booked_on.sql
-- Denormalise the Bookings sort key onto book_delegates.booked_on.
--
-- HAND-RUN ONLY. Run with:
--     psql "$DATABASE_URL" -f backend/sql/2026_08_booked_on.sql
--
-- ############################################################################
-- ## DEPLOY ORDER IS LOAD-BEARING. RUN THIS FILE BEFORE DEPLOYING THE PYTHON.##
-- ############################################################################
-- The Python from this workstream changes BookDelegateViewSet.ordering to
-- ["-booked_on"] and period_date_fields to ("booked_on",). If the code ships
-- first, the column either does not exist (every Bookings request 500s) or
-- exists unbackfilled, in which case booked_on is NULL on every row, the sort
-- degrades to StableOrderingFilter's pk tiebreak alone, and the Bookings table
-- silently reorders itself for every user. Run this file, confirm SECTION 4
-- reports zero mismatches, then deploy.
--
-- CONCURRENTLY cannot run inside a transaction block; psql runs each statement
-- in its own transaction by default, which is exactly what is needed. Do NOT
-- wrap this file in BEGIN/COMMIT and do NOT run it with psql -1. The DO block
-- below is its own transaction per iteration, which is the point of chunking.
--
-- Every DDL statement in SECTION 1 is Django's own output, taken verbatim from
--     python manage.py sqlmigrate book_delegate 0013
-- per the workstream's rule that hand-written DDL decouples the database from
-- model state. IF NOT EXISTS is the only addition, so a re-run is safe.
-- ============================================================================


-- == SECTION 1: DDL AND DML ==================================================

-- Nullable column with no default: instant in PostgreSQL 11+, no table rewrite.
-- Django emits: ALTER TABLE "book_delegates" ADD COLUMN "booked_on" date NULL;
ALTER TABLE book_delegates ADD COLUMN IF NOT EXISTS booked_on date;

-- Backfill in chunks. ~1,251 rows on the development snapshot is small, but
-- chunking keeps lock duration and WAL generation bounded and lets autovacuum
-- keep pace, and the same file has to be safe to run when the table is larger.
--
-- JOINED ON invoice_number, NOT on id: BookDelegate.invoice is a ForeignKey
-- declared to_field="invoice_number" db_column="invoice_number", so
-- book_delegates has no invoice_id column at all — its FK column is the varchar
-- invoice_number, and that is what joins to book_events.invoice_number.
--
-- The chunk predicate is "booked_on IS NULL", which is also the loop's progress
-- marker. A delegate whose invoice has BOTH dates NULL would legitimately
-- compute NULL and so would be re-selected forever; the AND EXISTS guard below
-- restricts the loop to rows that will actually change, so the loop terminates.
DO $$
DECLARE touched integer;
BEGIN
  LOOP
    UPDATE book_delegates d
    SET    booked_on = COALESCE(e.request_date, e.invoice_date)
    FROM   book_events e
    WHERE  d.invoice_number = e.invoice_number
      AND  d.id IN (
             SELECT d2.id FROM book_delegates d2
             JOIN   book_events e2 ON d2.invoice_number = e2.invoice_number
             WHERE  d2.booked_on IS NULL
               AND  d2.invoice_number IS NOT NULL
               AND  COALESCE(e2.request_date, e2.invoice_date) IS NOT NULL
             ORDER BY d2.id
             LIMIT 5000
           );
    GET DIAGNOSTICS touched = ROW_COUNT;
    EXIT WHEN touched = 0;
    RAISE NOTICE 'backfilled % rows', touched;
  END LOOP;
END $$;

-- Then the index, concurrently, after the data is in place. Building it first
-- would index 1,251 NULLs and then churn every one of them during the backfill.
-- Statement below is Django's own, from sqlmigrate, with IF NOT EXISTS added:
--   CREATE INDEX CONCURRENTLY "book_delegates_booked_id_idx"
--     ON "book_delegates" ("booked_on" DESC NULLS LAST, "id" DESC);
CREATE INDEX CONCURRENTLY IF NOT EXISTS book_delegates_booked_id_idx
  ON book_delegates (booked_on DESC NULLS LAST, id DESC);

-- Refresh planner statistics so the new column and index are costed correctly.
ANALYZE book_delegates;


-- == SECTION 2: RECORD THE STATE-ONLY MIGRATION AS APPLIED ===================
-- book_delegate/migrations/0013_booked_on.py exists so makemigrations stays
-- clean and model state matches the DB; it must never be executed by migrate,
-- so it is recorded as applied here. The app label and migration name match
-- that file exactly.

INSERT INTO django_migrations (app, name, applied)
VALUES ('book_delegate', '0013_booked_on', NOW())
ON CONFLICT DO NOTHING;


-- == SECTION 3: ROLLBACK (run only to undo) ==================================
-- Every statement commented out. Uncomment the whole block to revert.
-- Roll back the PYTHON FIRST, then this: with the column dropped and the old
-- code still deployed, ordering=["-booked_on"] would raise FieldError on every
-- Bookings request.
--
-- DROP INDEX CONCURRENTLY IF EXISTS book_delegates_booked_id_idx;
-- ALTER TABLE book_delegates DROP COLUMN IF EXISTS booked_on;
-- DELETE FROM django_migrations
--   WHERE app = 'book_delegate' AND name = '0013_booked_on';


-- == SECTION 4: VERIFY =======================================================
-- All three checks below must pass before the Python is deployed.

-- 4.1  The index exists AND is valid. An INVALID index is present in pg_indexes
--      but the planner will never use it, so existence alone is not enough.
SELECT c.relname   AS index_name,
       i.indisvalid AS is_valid
FROM   pg_index i
JOIN   pg_class c ON c.oid = i.indexrelid
WHERE  c.relname = 'book_delegates_booked_id_idx';
-- Expect exactly 1 row, is_valid = t.

-- 4.2  Zero rows where booked_on is NULL but the invoice HAS a usable date.
--      A non-zero result means the backfill did not finish.
SELECT count(*) AS unbackfilled_expect_0
FROM   book_delegates d
JOIN   book_events e ON d.invoice_number = e.invoice_number
WHERE  d.booked_on IS NULL
  AND  COALESCE(e.request_date, e.invoice_date) IS NOT NULL;

-- 4.3  booked_on agrees with COALESCE(request_date, invoice_date) on every row.
--      IS DISTINCT FROM, not <>, so a NULL on either side compares correctly
--      rather than yielding NULL and silently dropping out of the count.
SELECT count(*) AS mismatched_expect_0
FROM   book_delegates d
JOIN   book_events e ON d.invoice_number = e.invoice_number
WHERE  d.booked_on IS DISTINCT FROM COALESCE(e.request_date, e.invoice_date);

-- 4.4  Context, not a gate: delegates with no matching invoice row at all.
--      db_constraint=False on the FK means these are possible; they keep a NULL
--      booked_on and sort last under DESC NULLS LAST.
SELECT count(*) AS orphan_delegates
FROM   book_delegates d
LEFT   JOIN book_events e ON d.invoice_number = e.invoice_number
WHERE  e.invoice_number IS NULL;

-- 4.5  The migration row Section 2 inserts. Expect 1.
SELECT app, name FROM django_migrations
WHERE  app = 'book_delegate' AND name = '0013_booked_on';
