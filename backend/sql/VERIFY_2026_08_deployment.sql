-- ============================================================================
-- VERIFY_2026_08_deployment.sql
-- Post-deployment health check for the 2026-08 performance workstream.
--
-- READ-ONLY. Creates nothing, changes nothing, deletes nothing. Safe to run on
-- production at any time, as often as you like.
--
-- Run it in pgAdmin's Query Tool (it is a single SELECT, so the transaction
-- issue that affects the CREATE INDEX files does not apply here), or with:
--     psql "$DATABASE_URL" -f backend/sql/VERIFY_2026_08_deployment.sql
--
-- EVERY ROW MUST READ "PASS". Anything else is explained in the `detail` column
-- and in DEPLOY_2026_08_PERFORMANCE.md.
-- ============================================================================

WITH checks AS (

-- ── File 1: 2026_08_perf_indexes.sql ────────────────────────────────────────
SELECT 1 AS ord, 'File 1' AS area, '21 performance indexes' AS check_name,
       count(*)::text || ' of 21' AS detail,
       CASE WHEN count(*) = 21 THEN 'PASS' ELSE 'FAIL' END AS result
FROM   pg_indexes WHERE indexname IN (
  'book_events_reqdate_id_idx','book_events_invdate_id_idx',
  'book_events_booked_on_expr_idx','book_events_sales_exec_idx',
  'book_events_team_leader_idx','book_events_edition_idx',
  'book_events_source_idx','wh_ev_api_key_idx','wh_ev_created_booking_idx',
  'users_team_idx','users_mapped_lead_idx','events_sales_exec_idx',
  'events_master_code_idx','events_status_idx','tickets_created_id_idx',
  'tickets_status_created_id_idx','action_logs_user_created_idx',
  'action_logs_created_idx','wh_ev_created_id_idx',
  'paper_reviews_subdate_id_idx','proposal_subs_subdate_id_idx')

-- ── File 2: 2026_08_booked_on.sql ───────────────────────────────────────────
UNION ALL
SELECT 2, 'File 2', 'booked_on column exists',
       coalesce(max(data_type), 'MISSING'),
       CASE WHEN count(*) = 1 THEN 'PASS' ELSE 'FAIL' END
FROM   information_schema.columns
WHERE  table_schema = 'public' AND table_name = 'book_delegates'
  AND  column_name = 'booked_on'

UNION ALL
SELECT 3, 'File 2', 'booked_on index built and valid',
       coalesce(max(CASE WHEN i.indisvalid THEN 'valid' ELSE 'INVALID' END), 'MISSING'),
       CASE WHEN count(*) = 1 AND bool_and(i.indisvalid) THEN 'PASS' ELSE 'FAIL' END
FROM   pg_index i JOIN pg_class c ON c.oid = i.indexrelid
WHERE  c.relname = 'book_delegates_booked_id_idx'

UNION ALL
SELECT 4, 'File 2', 'no unbackfilled rows',
       count(*)::text || ' rows with NULL booked_on but a usable invoice date',
       CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM   book_delegates d JOIN book_events e ON d.invoice_number = e.invoice_number
WHERE  d.booked_on IS NULL
  AND  COALESCE(e.request_date, e.invoice_date) IS NOT NULL

UNION ALL
SELECT 5, 'File 2', 'booked_on agrees with COALESCE',
       count(*)::text || ' mismatched rows',
       CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM   book_delegates d JOIN book_events e ON d.invoice_number = e.invoice_number
WHERE  d.booked_on IS DISTINCT FROM COALESCE(e.request_date, e.invoice_date)

-- ── File 3: 2026_08_ticket_trgm.sql ─────────────────────────────────────────
UNION ALL
SELECT 6, 'File 3', 'pg_trgm extension installed',
       coalesce(max(extversion), 'NOT INSTALLED'),
       CASE WHEN count(*) = 1 THEN 'PASS' ELSE 'FAIL' END
FROM   pg_extension WHERE extname = 'pg_trgm'

UNION ALL
SELECT 7, 'File 3', '7 trigram indexes, all valid',
       count(*)::text || ' of 7 valid',
       CASE WHEN count(*) = 7 THEN 'PASS' ELSE 'FAIL' END
FROM   pg_index i JOIN pg_class c ON c.oid = i.indexrelid
WHERE  c.relname LIKE 'tickets_%_trgm_idx' AND i.indisvalid

UNION ALL
-- The one that silently makes trigram search useless: an index on upper(col)
-- WITHOUT the ::text cast is never matched against the predicate Django emits.
SELECT 8, 'File 3', 'trigram indexes carry the ::text cast',
       count(*)::text || ' of 7 correctly cast',
       CASE WHEN count(*) = 7 THEN 'PASS' ELSE 'FAIL' END
FROM   pg_indexes
WHERE  indexname LIKE 'tickets_%_trgm_idx' AND indexdef LIKE '%::text%'

-- ── File 4: 2026_08_perf_indexes_2.sql ──────────────────────────────────────
UNION ALL
SELECT 9, 'File 4', '5 team_activity_logs FK indexes',
       count(*)::text || ' of 5',
       CASE WHEN count(*) = 5 THEN 'PASS' ELSE 'FAIL' END
FROM   pg_indexes WHERE indexname IN (
  'team_activity_logs_destination_team_id_7e17f262',
  'team_activity_logs_moved_by_id_c480b640',
  'team_activity_logs_source_team_id_0fc16a0a',
  'team_activity_logs_team_id_9d332a8d',
  'team_activity_logs_user_id_693dba4b')

-- ── Cross-cutting ───────────────────────────────────────────────────────────
UNION ALL
-- An INVALID index is present in pg_indexes but the planner will NEVER use it.
-- This is what an interrupted CONCURRENTLY build leaves behind, and it is the
-- single most likely way for this deployment to look done and not be.
SELECT 10, 'Overall', 'no INVALID indexes anywhere',
       CASE WHEN count(*) = 0 THEN 'none'
            ELSE string_agg(c.relname, ', ') END,
       CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM   pg_index i JOIN pg_class c ON c.oid = i.indexrelid
WHERE  NOT i.indisvalid

UNION ALL
-- DISTINCT, not count(*): django_migrations has NO unique constraint on
-- (app, name) — only a primary key on id — so the ON CONFLICT DO NOTHING in the
-- deployment files has nothing to conflict on and does nothing. Re-running a
-- file inserts a SECOND row for the same migration. What matters for
-- correctness is that each of the 8 is present at least once, which is what
-- Django itself reads; duplicates are reported separately below.
SELECT 11, 'Overall', '8 migrations recorded (distinct)',
       count(DISTINCT (app, name))::text || ' of 8',
       CASE WHEN count(DISTINCT (app, name)) = 8 THEN 'PASS' ELSE 'FAIL' END
FROM   django_migrations
WHERE  (app = 'accounts'            AND name = '0025_perf_indexes')
   OR  (app = 'book_delegate'       AND name = '0013_booked_on')
   OR  (app = 'book_event'          AND name = '0022_perf_indexes')
   OR  (app = 'paper_review'        AND name = '0006_perf_indexes')
   OR  (app = 'proposal_submission' AND name = '0005_perf_indexes')
   OR  (app = 'ticket_central'      AND name IN ('0006_perf_indexes','0007_ticket_trgm_indexes'))
   OR  (app = 'webhooks'            AND name = '0007_perf_indexes')

UNION ALL
-- Cosmetic, not correctness: Django keys applied_migrations() by (app, name),
-- so duplicates collapse and `migrate` still behaves correctly. Reported so the
-- table can be tidied, and so a confusing row count has an explanation.
SELECT 12, 'Overall', 'no duplicate migration rows',
       CASE WHEN count(*) = 0 THEN 'none'
            ELSE count(*)::text || ' migration(s) recorded more than once '
                 || '(harmless; see cleanup query in the runbook)' END,
       CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'TIDY' END
FROM   (SELECT app, name FROM django_migrations
        GROUP BY app, name HAVING count(*) > 1) dupes

UNION ALL
-- Not changed by this deployment; verified because they are live credentials.
SELECT 13, 'Overall', 'webhook API keys intact',
       count(*)::text || ' keys, checksum ' ||
       coalesce(left(md5(string_agg(id::text || ':' || api_key, '|' ORDER BY id)), 12), 'n/a'),
       CASE WHEN count(*) > 0 THEN 'PASS' ELSE 'CHECK' END
FROM   webhook_api_keys

UNION ALL
-- Leftover from the runbook's optional belt-and-braces step. Holds live keys in
-- plaintext, so it should not still exist.
SELECT 14, 'Overall', 'no plaintext key backup left behind',
       CASE WHEN count(*) = 0 THEN 'none' ELSE 'DROP IT: ' || string_agg(tablename, ', ') END,
       CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'ACTION' END
FROM   pg_tables
WHERE  schemaname = 'public' AND tablename LIKE 'webhook_api_keys_backup%'
)

SELECT area, check_name, detail, result FROM checks ORDER BY ord;
