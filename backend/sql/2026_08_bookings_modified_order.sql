-- ============================================================================
-- 2026_08_bookings_modified_order.sql
-- Index the Bookings table's new default sort: newest MODIFIED first.
--
-- HAND-RUN ONLY. Run with:
--     psql "$DATABASE_URL" -f backend/sql/2026_08_bookings_modified_order.sql
--
-- WHY THIS EXISTS
-- BookDelegateViewSet.ordering moved from ["-created_at", "-id"] to
-- ["-updated_at", "-id"], by request: the table must lead with the row someone
-- touched LAST, not the row entered last. Those are the same thing only until the
-- first edit. Under -created_at a correction made this morning to a row entered
-- in July stayed buried in July, so the person who made it could not see their
-- own work. updated_at is auto_now=True, so every full save() stamps it and the
-- rows reorder themselves.
--
-- The frontend had to move with it. The Modified Time column's header was DEAD,
-- because BookingsPage.jsx declared no serverOrdering for it and updated_at was
-- absent from ordering_fields, and DRF silently drops an unlisted ordering term.
-- Both are fixed in the same change, along with the cell rendering in IST rather
-- than in whatever timezone the viewer's machine happens to be set to.
--
-- DEPLOY ORDER IS NOT LOAD-BEARING. This file adds an INDEX ONLY: no column, no
-- backfill. updated_at already exists and is NOT NULL, so the new ordering is
-- CORRECT with or without this index — without it Postgres sorts the whole table
-- to return one page of 50. Run it first regardless.
--
-- CONCURRENTLY cannot run inside a transaction block; psql runs each statement in
-- its own transaction by default, which is what is needed. Do NOT wrap this file
-- in BEGIN/COMMIT and do NOT run it with psql -1.
--
-- The DDL is Django's own output, taken verbatim from
--     python manage.py sqlmigrate book_delegate 0015
-- per the workstream's rule that hand-written DDL decouples the database from
-- model state. IF NOT EXISTS is the only addition, so a re-run is safe.
-- ============================================================================


-- == SECTION 1: DDL ==========================================================

-- DESC on BOTH columns, matching ORDER BY updated_at DESC, id DESC exactly, so
-- the page is one index scan rather than a full sort. No NULLS clause: updated_at
-- is DateTimeField(auto_now=True) and therefore NOT NULL, so the nulls_last
-- spelling that book_delegates_booked_id_idx needs has nothing to guard here.
-- Django emits:
--   CREATE INDEX CONCURRENTLY "book_delegates_updated_id_idx"
--     ON "book_delegates" ("updated_at" DESC, "id" DESC);
CREATE INDEX CONCURRENTLY IF NOT EXISTS book_delegates_updated_id_idx
  ON book_delegates (updated_at DESC, id DESC);

-- Refresh planner statistics so the new index is costed correctly.
ANALYZE book_delegates;


-- == SECTION 2: RECORD THE STATE-ONLY MIGRATION AS APPLIED ===================
-- book_delegate/migrations/0015_updated_at_order_index.py exists so
-- makemigrations stays clean and model state matches the DB; it must never be
-- executed by migrate, so it is recorded as applied here. The app label and
-- migration name match that file exactly.

INSERT INTO django_migrations (app, name, applied)
VALUES ('book_delegate', '0015_updated_at_order_index', NOW())
ON CONFLICT DO NOTHING;


-- == SECTION 3: ROLLBACK (run only to undo) ==================================
-- Every statement commented out. Uncomment the whole block to revert.
-- Order does not matter against the Python here: dropping the index only makes
-- the sort slower, never wrong.
--
-- DROP INDEX CONCURRENTLY IF EXISTS book_delegates_updated_id_idx;
-- DELETE FROM django_migrations
--   WHERE app = 'book_delegate' AND name = '0015_updated_at_order_index';
--
-- book_delegates_created_id_idx is deliberately NOT dropped by this file in
-- either direction. created_at is still the Added Time column's serverOrdering
-- and still a sort the user can pick, so that index keeps earning its keep; the
-- same is true of book_delegates_booked_id_idx and the period window.


-- == SECTION 4: VERIFY =======================================================

-- 4.1  The index exists AND is valid. An INVALID index is present in pg_indexes
--      but the planner will never use it, so existence alone is not enough.
SELECT c.relname    AS index_name,
       i.indisvalid AS is_valid
FROM   pg_index i
JOIN   pg_class c ON c.oid = i.indexrelid
WHERE  c.relname = 'book_delegates_updated_id_idx';
-- Expect exactly 1 row, is_valid = t.

-- 4.2  The planner actually USES it for the default page. Expect an Index Scan
--      on book_delegates_updated_id_idx and NO "Sort" node.
EXPLAIN (COSTS OFF)
SELECT id FROM book_delegates ORDER BY updated_at DESC, id DESC LIMIT 50;

-- 4.3  CONTEXT, NOT A GATE. How much real modified-time signal updated_at
--      carries. The import stamps it at load time for every row it writes, so on
--      a freshly loaded database expect one dominant timestamp and a handful of
--      distinct days from rows people have actually edited. That is the correct
--      shape, not a problem: the edited rows are precisely the ones that must
--      float to the top, and the untouched backlog below them is in load order.
SELECT count(*)                              AS rows,
       count(DISTINCT updated_at::date)      AS distinct_days,
       count(*) FILTER (WHERE updated_at > created_at + interval '1 second')
                                             AS edited_since_creation,
       min(updated_at)                       AS earliest,
       max(updated_at)                       AS latest
FROM   book_delegates;

-- 4.4  The head of the table, which is what the request was about. The most
--      recently modified rows must lead, regardless of when they were created.
--      created_at is shown alongside so an edited old row is recognisable.
SELECT d.id, d.updated_at, d.created_at, d.invoice_number
FROM   book_delegates d
ORDER  BY d.updated_at DESC, d.id DESC
LIMIT  10;

-- 4.5  The one write path that used to bypass auto_now. services.py
--      clear_delegate_overrides() is a queryset .update(), which does NOT fire
--      auto_now, and it now sets updated_at explicitly. A row whose overrides are
--      all NULL and whose updated_at still equals created_at to the microsecond
--      has never been stamped; after clearing overrides on one, this must change.
--      Informational, not a gate.
SELECT count(*) AS never_stamped
FROM   book_delegates
WHERE  updated_at = created_at;

-- 4.6  The migration row Section 2 inserts. Expect 1.
SELECT app, name FROM django_migrations
WHERE  app = 'book_delegate' AND name = '0015_updated_at_order_index';
