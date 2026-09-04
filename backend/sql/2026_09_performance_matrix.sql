-- 2026_09_performance_matrix.sql
-- Edition identity on the events table (base_code, year, verdict) and the removal
-- of the three Event Performance tables the Performance Matrix replaces.
-- Run against the linq_crm database BEFORE deploying the code that reads it.
--
-- Pairs with events/migrations/0018_base_code_year_verdict.py and
-- performance_matrix/migrations/0001_drop_event_performance.py, which mirror this
-- for databases built from migrations alone (the test database). The INSERTs
-- below record both as applied, so Django never runs them here; everything is
-- guarded so either order is harmless regardless.

BEGIN;

-- ── DDL: events ──────────────────────────────────────────────────────────────
-- master_code was empty on every row (0 of 217 on 2026-09-04); renamed, not
-- dropped, so anything a later Events.csv load put there survives.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name = 'events' AND column_name = 'master_code')
     AND NOT EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name = 'events' AND column_name = 'base_code') THEN
    ALTER TABLE events RENAME COLUMN master_code TO base_code;
  END IF;
END $$;

ALTER TABLE events ADD COLUMN IF NOT EXISTS year INTEGER NULL;
ALTER TABLE events ADD COLUMN IF NOT EXISTS verdict VARCHAR(30) NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS events_year_idx      ON events (year);
CREATE INDEX IF NOT EXISTS events_base_year_idx ON events (base_code, year);

-- ── Backfill ─────────────────────────────────────────────────────────────────
-- year from the start date.
UPDATE events SET year = EXTRACT(YEAR FROM event_date)::int
 WHERE year IS NULL AND event_date IS NOT NULL;

-- base_code = first alphabetic token of the internal code that is not a month,
-- upper-cased. The SQL twin of events.codes.derive_base_code:
--   'AFS - JS' -> AFS, 'Feb2027_AFS-JS' -> AFS, 'BIU/GS - PM' -> BIU.
UPDATE events e SET base_code = upper(COALESCE(
  (SELECT m[1] FROM regexp_matches(e.event_code, '([A-Za-z]+)', 'g') WITH ORDINALITY AS r(m, n)
    WHERE upper(m[1]) NOT IN ('JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','SEPT','OCT','NOV','DEC',
                              'JANUARY','FEBRUARY','MARCH','APRIL','JUNE','JULY','AUGUST','SEPTEMBER',
                              'OCTOBER','NOVEMBER','DECEMBER')
    ORDER BY n LIMIT 1),
  (SELECT m[1] FROM regexp_matches(e.event_code, '([A-Za-z]+)', 'g') WITH ORDINALITY AS r(m, n)
    ORDER BY n LIMIT 1),
  ''))
 WHERE COALESCE(e.base_code, '') = '';

-- ── Event Performance removal ────────────────────────────────────────────────
-- All three were empty (0 rows each on 2026-09-04) and the module is deleted.
DROP TABLE IF EXISTS ep_follow_ups;
DROP TABLE IF EXISTS ep_mailshots;
DROP TABLE IF EXISTS ep_notes;
DELETE FROM django_migrations WHERE app = 'event_performance';

-- ── State-only migration records ─────────────────────────────────────────────
INSERT INTO django_migrations (app, name, applied)
SELECT 'events', '0018_base_code_year_verdict', NOW()
 WHERE NOT EXISTS (SELECT 1 FROM django_migrations
                   WHERE app = 'events' AND name = '0018_base_code_year_verdict');
INSERT INTO django_migrations (app, name, applied)
SELECT 'performance_matrix', '0001_drop_event_performance', NOW()
 WHERE NOT EXISTS (SELECT 1 FROM django_migrations
                   WHERE app = 'performance_matrix' AND name = '0001_drop_event_performance');

COMMIT;

-- ── Verify ───────────────────────────────────────────────────────────────────
-- SELECT column_name, data_type FROM information_schema.columns
--  WHERE table_name = 'events' AND column_name IN ('base_code','year','verdict');
-- Expected: three rows.
-- SELECT count(*) FROM events WHERE year IS NULL OR base_code = '';
-- Expected: 0.
-- SELECT base_code, year, count(*) FROM events GROUP BY 1,2 HAVING count(*) > 1;
-- Expected: no rows. Any row listed is two editions sharing one (base, year)
-- and needs a base code corrected on the Events form.
-- SELECT to_regclass('ep_follow_ups'), to_regclass('ep_mailshots'), to_regclass('ep_notes');
-- Expected: three NULLs.

-- ── Rollback (if needed) ─────────────────────────────────────────────────────
-- BEGIN;
-- DROP INDEX IF EXISTS events_base_year_idx;
-- DROP INDEX IF EXISTS events_year_idx;
-- ALTER TABLE events DROP COLUMN IF EXISTS verdict;
-- ALTER TABLE events DROP COLUMN IF EXISTS year;
-- ALTER TABLE events RENAME COLUMN base_code TO master_code;
-- DELETE FROM django_migrations WHERE app = 'events' AND name = '0018_base_code_year_verdict';
-- DELETE FROM django_migrations WHERE app = 'performance_matrix';
-- COMMIT;
-- The ep_* tables are not recreated: they held no rows.
