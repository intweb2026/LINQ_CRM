-- 0028_user_login_access.sql
-- Adds login_access boolean to the users table (accounts.User, db_table="users").
-- Run against the linq_crm database BEFORE deploying the code that reads it.
--
-- Pairs with accounts/migrations/0028_user_login_access.py, which mirrors this
-- DDL for databases built from migrations alone, such as the test database. The
-- INSERT below records the migration as applied, so Django never runs it here;
-- everything is written IF NOT EXISTS so either order is harmless regardless.

BEGIN;

-- ── DDL ──────────────────────────────────────────────────────────────────────
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS login_access BOOLEAN NOT NULL DEFAULT TRUE;

-- Plain CREATE INDEX, deliberately: CONCURRENTLY cannot run inside a
-- transaction, and the column is a fresh boolean on a small table, so the
-- brief lock is not worth splitting the script for.
CREATE INDEX IF NOT EXISTS
  users_login_access_idx ON users (login_access);

-- ── State-only migration record ──────────────────────────────────────────────
-- Tells Django this migration has already been applied so it never tries to
-- re-run it or create the column again.
INSERT INTO django_migrations (app, name, applied)
VALUES ('accounts', '0028_user_login_access', NOW());

COMMIT;

-- ── Verify ───────────────────────────────────────────────────────────────────
-- SELECT column_name, data_type, column_default, is_nullable
--   FROM information_schema.columns
--  WHERE table_name = 'users' AND column_name = 'login_access';
-- Expected: login_access | boolean | true | NO

-- ── Rollback (if needed) ─────────────────────────────────────────────────────
-- BEGIN;
-- DROP INDEX IF EXISTS users_login_access_idx;
-- ALTER TABLE users DROP COLUMN IF EXISTS login_access;
-- DELETE FROM django_migrations WHERE app = 'accounts' AND name = '0028_user_login_access';
-- COMMIT;
