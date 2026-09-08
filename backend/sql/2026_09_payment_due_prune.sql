-- 2026_09_payment_due_prune.sql
-- Drops book_events.payment_due_date. Nothing reads it any more; the Bookings
-- screens never offered it, the importers only back-filled it from the Zoho
-- export, and on 9 Sep 2026 no open invoice carried a value. Run against the
-- linq_crm database BEFORE deploying the code that no longer declares it.
--
-- Pairs with book_event/migrations/0027_remove_bookevent_payment_due_date.py,
-- which mirrors this DDL for databases built from migrations alone. The INSERT
-- below records the migration as applied; everything is guarded so either order
-- is harmless.
--
-- Downstream, two column layouts lose one column. The Bookings tab written by
-- sync/bookings_sync.py drops "Payment Due Date"; its header row is rewritten on
-- every sync, so it realigns itself. The /api/data/delegates/ report drops
-- payment_due_date, so a sheet reading that feed by position must move every
-- column from Invoice Number onwards one place to the left.

BEGIN;

ALTER TABLE book_events DROP COLUMN IF EXISTS payment_due_date;

INSERT INTO django_migrations (app, name, applied)
SELECT 'book_event', '0027_remove_bookevent_payment_due_date', NOW()
 WHERE NOT EXISTS (SELECT 1 FROM django_migrations
                   WHERE app = 'book_event' AND name = '0027_remove_bookevent_payment_due_date');

COMMIT;

-- ── Verify ───────────────────────────────────────────────────────────────────
-- SELECT column_name FROM information_schema.columns
--  WHERE table_name = 'book_events' AND column_name = 'payment_due_date';
-- Expected, no rows.

-- ── Rollback (if needed) ─────────────────────────────────────────────────────
-- BEGIN;
-- ALTER TABLE book_events ADD COLUMN IF NOT EXISTS payment_due_date DATE NULL;
-- DELETE FROM django_migrations
--  WHERE app = 'book_event' AND name = '0027_remove_bookevent_payment_due_date';
-- COMMIT;
