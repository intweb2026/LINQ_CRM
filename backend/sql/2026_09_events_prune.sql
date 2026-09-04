-- 2026_09_events_prune.sql
-- Drops two events columns nothing reads any more: capacity and expected_revenue.
-- Run against the linq_crm database BEFORE deploying the code that no longer
-- declares them. Run AFTER 2026_09_performance_matrix.sql.
--
-- Pairs with events/migrations/0019_remove_capacity_expected_revenue.py, which
-- mirrors this DDL for databases built from migrations alone. The INSERT below
-- records the migration as applied; everything is guarded so either order is
-- harmless.
--
-- NOT dropped, deliberately:
--   status                 still read by the Mining Matrix and the events filter;
--                          retired from every screen and serializer only.
--   event_management_team  hidden on the Events screen, but Proposal Submission
--                          reads it as the tracker's Production Executive.

BEGIN;

ALTER TABLE events DROP COLUMN IF EXISTS capacity;
ALTER TABLE events DROP COLUMN IF EXISTS expected_revenue;

INSERT INTO django_migrations (app, name, applied)
SELECT 'events', '0019_remove_capacity_expected_revenue', NOW()
 WHERE NOT EXISTS (SELECT 1 FROM django_migrations
                   WHERE app = 'events' AND name = '0019_remove_capacity_expected_revenue');

COMMIT;

-- ── Verify ───────────────────────────────────────────────────────────────────
-- SELECT column_name FROM information_schema.columns
--  WHERE table_name = 'events' AND column_name IN ('capacity','expected_revenue');
-- Expected: no rows.

-- ── Rollback (if needed) ─────────────────────────────────────────────────────
-- BEGIN;
-- ALTER TABLE events ADD COLUMN IF NOT EXISTS capacity INTEGER NOT NULL DEFAULT 500;
-- ALTER TABLE events ADD COLUMN IF NOT EXISTS expected_revenue NUMERIC(14,2) NOT NULL DEFAULT 0;
-- DELETE FROM django_migrations WHERE app = 'events' AND name = '0019_remove_capacity_expected_revenue';
-- COMMIT;
