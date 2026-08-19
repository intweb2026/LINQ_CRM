"""
accounts/tests_prune_logs.py
─────────────────────────────
Log retention.

WHAT THIS PINS
1.  The DEFAULT IS A DRY RUN. It reports a nonzero would-delete count and
    deletes nothing. This mirrors the bulk_update preview convention: the
    destructive path is the one you have to ask for, and a command that deleted
    by default would be one typo away from destroying evidence.
2.  --commit deletes ONLY rows outside the window. A window applied with the
    wrong sign, or against the wrong column, would delete exactly the rows you
    wanted to keep, so the in-window survivors are asserted explicitly.
3.  ActionLog IS NEVER TOUCHED, by either mode. It is the audit trail; it is
    absent from LOG_RETENTION_DAYS on purpose and no flag combination may
    delete from it.
"""
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import ActionLog
from webhooks.models import WebhookLog

User = get_user_model()

# 90-day window on webhook logs only, so the fixture below is unambiguous and
# the test does not depend on whatever the deployed defaults happen to be.
RETENTION = {"webhooks.WebhookLog": 90}


def run(*args):
    out = StringIO()
    call_command("prune_logs", *args, stdout=out, stderr=out)
    return out.getvalue()


@override_settings(LOG_RETENTION_DAYS=RETENTION)
class PruneLogsTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="prune_u", password="x", role="admin",
            email="prune@iq-hub.com",
        )
        now = timezone.now()

        # 7 rows well outside a 90-day window, 4 comfortably inside it.
        cls.old_ids, cls.new_ids = [], []
        for i in range(7):
            log = WebhookLog.objects.create(status=WebhookLog.Status.RECEIVED)
            # created_at is auto_now_add on this model, so the value has to be
            # forced after the fact with a queryset update; assigning before
            # save() would simply be overwritten.
            WebhookLog.objects.filter(pk=log.pk).update(
                created_at=now - timedelta(days=200 + i))
            cls.old_ids.append(log.pk)
        for i in range(4):
            log = WebhookLog.objects.create(status=WebhookLog.Status.RECEIVED)
            WebhookLog.objects.filter(pk=log.pk).update(
                created_at=now - timedelta(days=i))
            cls.new_ids.append(log.pk)

        for i in range(5):
            ActionLog.objects.create(user=cls.user, action=f"act-{i}")
        ActionLog.objects.update(created_at=now - timedelta(days=3000))

    # ── 1. Dry run ────────────────────────────────────────────────────────────

    def test_dry_run_deletes_nothing(self):
        before = WebhookLog.objects.count()
        run()
        self.assertEqual(WebhookLog.objects.count(), before)

    def test_dry_run_reports_a_nonzero_would_delete_count(self):
        output = run()
        self.assertIn("webhooks.WebhookLog", output)
        self.assertIn("would delete  : 7", output)
        self.assertIn("Nothing was deleted", output)

    def test_dry_run_reports_the_oldest_and_newest_in_the_doomed_set(self):
        output = run()
        self.assertIn("oldest/newest :", output)

    def test_dry_run_names_the_deletion_path_it_would_take(self):
        """
        The raw-versus-ORM choice is printed rather than assumed, so a
        ForeignKey added to a log model later shows up in the output instead of
        silently changing what the command bypasses.
        """
        output = run()
        self.assertIn("path          : raw chunked SQL", output)
        self.assertIn("inbound FKs: False", output)
        self.assertIn("delete signals: False", output)

    # ── 2. Commit ─────────────────────────────────────────────────────────────

    def test_commit_deletes_only_rows_outside_the_window(self):
        run("--commit")

        surviving = set(WebhookLog.objects.values_list("pk", flat=True))
        self.assertEqual(
            surviving, set(self.new_ids),
            "the wrong side of the window was deleted",
        )
        self.assertEqual(WebhookLog.objects.count(), 4)

    def test_commit_reports_what_it_deleted(self):
        output = run("--commit")
        self.assertIn("DELETED       : 7", output)
        self.assertIn("Deleted 7 row(s) in total", output)

    def test_commit_is_idempotent(self):
        run("--commit")
        after_first = WebhookLog.objects.count()
        run("--commit")
        self.assertEqual(WebhookLog.objects.count(), after_first)

    # ── 3. ActionLog is untouchable ───────────────────────────────────────────

    def test_action_log_is_unchanged_by_a_dry_run(self):
        before = ActionLog.objects.count()
        run()
        self.assertEqual(ActionLog.objects.count(), before)

    def test_action_log_is_unchanged_by_commit(self):
        """
        Every ActionLog row here is 3,000 days old, so any window at all would
        take all of them. They survive because the model is not in
        LOG_RETENTION_DAYS, which is the guarantee under test.
        """
        before = ActionLog.objects.count()
        self.assertEqual(before, 5)
        run("--commit")
        self.assertEqual(ActionLog.objects.count(), before)

    def test_the_report_says_action_log_was_excluded(self):
        self.assertIn("accounts.ActionLog is EXCLUDED by design", run())
        self.assertIn("accounts.ActionLog is EXCLUDED by design", run("--commit"))

    # ── 4. The what-if flag ───────────────────────────────────────────────────

    def test_report_action_logs_deletes_nothing_and_reports_counts(self):
        before = ActionLog.objects.count()
        output = run("--report-action-logs")
        self.assertEqual(ActionLog.objects.count(), before)
        for days in (180, 365, 730):
            self.assertIn(f"{days:>4} days ->", output)
        self.assertIn("This flag never deletes", output)

    def test_report_action_logs_counts_the_rows_a_window_would_take(self):
        """All 5 rows are 3,000 days old, so every window should claim all 5."""
        output = run("--report-action-logs")
        self.assertIn("would delete        5 of 5", output)

    def test_report_action_logs_does_not_prune_other_models(self):
        before = WebhookLog.objects.count()
        run("--report-action-logs")
        self.assertEqual(WebhookLog.objects.count(), before)


@override_settings(LOG_RETENTION_DAYS={})
class PruneLogsEmptyConfigTests(TestCase):
    def test_empty_retention_config_is_a_no_op_not_a_crash(self):
        output = run()
        self.assertIn("LOG_RETENTION_DAYS is empty", output)
