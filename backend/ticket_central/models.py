"""
ticket_central/models.py
─────────────────────────
Two-phase ticket: Market Research → Data Mining.
Field list extracted verbatim from Zoho Creator form (2026-06-02).
"""
from django.conf import settings
from django.contrib.postgres.indexes import GinIndex, OpClass
from django.db import models
from django.db.models.functions import Upper


class Ticket(models.Model):

    # ── Enums ─────────────────────────────────────────────────────────────
    class Status(models.TextChoices):
        # DRAFT is no longer reachable through the API: creation goes straight
        # to MR_SUBMITTED (serializers.py:121-124). It is kept in the enum
        # because migrated/historical rows still hold it, and because
        # submit_mr accepts DRAFT alongside RETURNED — in practice that branch
        # now only serves the RETURNED loop.
        DRAFT           = "draft",           "Draft"
        MR_SUBMITTED    = "mr_submitted",    "MR Submitted"
        COMPLETED       = "completed",       "Completed"
        RETURNED        = "returned",        "Returned"

    class Priority(models.TextChoices):
        AS    = "AS",    "AS"
        AD    = "AD",    "AD"
        SPEX  = "SPEX",  "SPEX"
        DD    = "DD",    "DD"
        ASSOC = "ASSOC", "ASSOC"
        MEDIA = "MEDIA", "MEDIA"
        AB    = "AB",    "AB"

    class TypeOfTicket(models.TextChoices):
        WHITE    = "WH",  "White"
        BLUE     = "BX",  "Blue"
        GREEN    = "GR",  "Green"
        YELLOW   = "YL",  "Yellow"
        LINKEDIN = "LX",  "LinkedIn"
        COMP     = "CX",  "Comp."
        PLATINUM = "PX",  "Platinum"
        GOLD     = "GX",  "Gold"
        ZID      = "ZID", "ZID"

    class Relationship(models.TextChoices):
        DIRECT   = "direct",   "Direct"
        INDIRECT = "indirect", "Indirect"

    # ── Identifiers (shared; not caller-writable) ────────────────────────
    # D1: NOT unique — historical data has duplicate ticket_numbers by design.
    # Assigned AT CREATE by TicketCreateSerializer.create() (serializers.py:129-132)
    # whenever a purpose is present, which the serializer requires — so in
    # practice every API-created ticket is numbered immediately. The overnight
    # `backfill_ticket_numbers` cron is retained for migrated/historical rows
    # that arrived without one. This supersedes D9, which said the number was
    # filled overnight rather than at form submit.
    ticket_number = models.CharField(max_length=50, blank=True, default="", db_index=True)
    # D2: Zoho record ID — the true unique key for migrated records.
    external_id   = models.CharField(
        max_length=50, unique=True, blank=True, null=True, db_index=True,
        help_text="Source system ID (Zoho) — for migrated records only",
    )
    event_code    = models.CharField(max_length=50, blank=True, default="", db_index=True)
    event_name    = models.CharField(max_length=255, blank=True, default="")

    status = models.CharField(
        max_length=20, choices=Status.choices,
        default=Status.DRAFT, db_index=True,
    )

    # ── SECTION A: Market Research fields ────────────────────────────────
    purpose                 = models.CharField(max_length=255, blank=True, default="")
    # D3: was URLField(500) — ~33% of Zoho data overflows, so plain TextField.
    link_url                = models.TextField(blank=True, default="")
    linkedin_keywords       = models.CharField(max_length=500, blank=True, default="")
    # `duplicate_tickets` used to live here, a free-text column where Zoho wrote
    # "⚠️ <earlier ticket number>" by hand. Repeats are detected live now, off
    # link_key below, so the column was dropped in migration 0008 rather than
    # left to disagree with the check.
    competitor_event_name   = models.CharField(max_length=255, blank=True, default="")
    organizer               = models.CharField(max_length=255, blank=True, default="")
    event_month_year        = models.DateField(null=True, blank=True)
    event_location          = models.CharField(max_length=255, blank=True, default="")
    # D4: plain CharField (Zoho values don't match a fixed choice set).
    relationship            = models.CharField(max_length=30, blank=True, default="")
    type_of_ticket          = models.CharField(max_length=50, blank=True, default="")
    # D4: plain CharField (Zoho priority values vary).
    priority                = models.CharField(max_length=20, blank=True, default="")
    estimate                = models.PositiveIntegerField(null=True, blank=True)
    mr_comments             = models.TextField(blank=True, default="")
    # D4: FK → CharField for migration safety (stores Zoho name text as-is).
    assigned_mr             = models.CharField(max_length=255, blank=True, default="")

    # ── SECTION B: Data Mining fields ────────────────────────────────────
    # D4: FK → CharField for migration safety.
    assign_name             = models.CharField(max_length=255, blank=True, default="")
    assign_date             = models.DateField(null=True, blank=True)
    actual_number           = models.PositiveIntegerField(null=True, blank=True)
    new_contacts_created    = models.PositiveIntegerField(null=True, blank=True)
    source_spreadsheet_id   = models.CharField(max_length=200, blank=True, default="")
    source_tab              = models.CharField(max_length=100, blank=True, default="")
    source_row_number       = models.PositiveIntegerField(null=True, blank=True)
    idempotency_key         = models.CharField(max_length=100, blank=True, default="")
    # ⚠ Ticket Type (DMD) choices unknown — distinct from type_of_ticket
    ticket_type             = models.CharField(max_length=50, blank=True, default="")
    complete_date           = models.DateField(null=True, blank=True)
    hubspot_entry_date      = models.DateField(null=True, blank=True)
    mined_count             = models.PositiveIntegerField(null=True, blank=True)
    dm_comments             = models.TextField(blank=True, default="")

    # ── DMD Level 2 (LX-2) fields ────────────────────────────────────────
    # D4: FK → CharField for migration safety.
    assign_name_lx2         = models.CharField(max_length=255, blank=True, default="")
    actual_count_lx2        = models.PositiveIntegerField(null=True, blank=True)
    complete_date_lx2       = models.DateField(null=True, blank=True)
    dm_comments_lx2         = models.TextField(blank=True, default="")

    # D16: preserve Zoho "Added User" as free text (no FK resolution).
    # Also the second half of who-may-see-this: get_queryset() scopes a
    # non-exempt role to created_by OR this, so an import that names a real
    # person hands them their own rows. Indexed on Upper() below, because
    # __iexact compiles to UPPER(col) = UPPER(%s) and a bare-column index is
    # not matched against that expression.
    added_user_text         = models.CharField(max_length=150, blank=True, default="")

    # ── Repeated-link detection ──────────────────────────────────────────
    # A stable digest of link_url, normalised (see utils.normalize_link), kept
    # in step by save(). A real column rather than a functional index on
    # link_url for two reasons: normalisation drops the scheme, "www." and any
    # trailing slash, which no index expression could reproduce; and link_url is
    # an unbounded TextField, where a btree index risks the 2704-byte row limit
    # on a long URL and would fail at INSERT time rather than at migrate time.
    link_key                = models.CharField(
        max_length=40, blank=True, default="", db_index=True,
    )

    # ── Audit / lifecycle ────────────────────────────────────────────────
    created_by       = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="tickets_created",
    )
    mr_submitted_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="tickets_mr_submitted",
    )
    mr_submitted_at  = models.DateTimeField(null=True, blank=True)
    dmd_submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="tickets_dmd_submitted",
    )
    dmd_submitted_at = models.DateTimeField(null=True, blank=True)
    returned_by      = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="tickets_returned",
    )
    returned_at      = models.DateTimeField(null=True, blank=True)
    return_reason    = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tickets"
        # ASCENDING, oldest first: Added Time is the row's own insert stamp, so
        # ascending puts the newest entry at the END of the table, which is how
        # the inline entry grid builds a batch and how people read it back.
        #
        # The descending indexes below are still the right ones and did NOT need
        # rebuilding: PostgreSQL scans a btree backwards, and (-created_at, -id)
        # reversed is exactly (created_at, id). Measured with EXPLAIN after the
        # flip, on 42,912 rows:
        #   status tab   → Index Only Scan Backward, no sort at all
        #   plain list   → Index Scan Backward on the single-column created_at
        #                  index, plus an Incremental Sort for the id tiebreak,
        #                  which only ever sorts within one tied timestamp
        # so the first page stays a cheap partial scan either way.
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["event_code"]),
            models.Index(fields=["-created_at"]),
            models.Index(fields=["updated_at"]),
            # Ticket Central's default sort is -created_at with the pk appended by
            # StableOrderingFilter, so the single-column -created_at index above
            # gets the leading column right and then re-sorts every tied group by
            # hand. created_at is a timestamp so ties are rare, but the status tabs
            # are not: filtering to one status and sorting by date is two separate
            # indexes today, and PostgreSQL can only use one of them.
            models.Index(fields=["-created_at", "-id"], name="tickets_created_id_idx"),
            models.Index(
                fields=["status", "-created_at", "-id"],
                name="tickets_status_created_id_idx",
            ),
            # ── Trigram search ────────────────────────────────────────────────
            # One search was fourteen unanchored substring predicates per row over
            # ~43,000 rows. Measured before this change, ?search=summit planned as
            # a Parallel Seq Scan removing 14,290 rows per worker, 3,793 buffers,
            # 111 ms execution — and the same scan ran again for the COUNT before
            # Prompt 1's CachedCountPaginator removed the repeat.
            #
            # THE EXPRESSION MUST MATCH THE EMITTED SQL, NOT THE COLUMN.
            # Django's PostgreSQL backend compiles __icontains to
            #     UPPER("tickets"."event_code"::text) LIKE UPPER(%s)
            # A gin_trgm_ops index on the BARE column is not matched against an
            # UPPER() expression: it would build, the plan would not change, and
            # the work would look done while nothing improved. Indexing
            # Upper("col") is what the planner matches.
            #
            # No explicit Cast is needed even though the predicate carries ::text.
            # upper() takes text, so PostgreSQL normalises Upper("event_code") on a
            # varchar column to upper((event_code)::text) when it stores the index
            # expression — verified by reading pg_indexes.indexdef back after
            # creating it, and confirmed by the resulting Bitmap Index Scan.
            #
            # SEPARATE INDEXES, NOT ONE MULTICOLUMN. SearchFilter ORs one predicate
            # per field, and separate indexes let the planner bitmap-OR across
            # exactly the ones a given term can use.
            GinIndex(OpClass(Upper("ticket_number"), name="gin_trgm_ops"),
                     name="tickets_ticketnum_trgm_idx"),
            GinIndex(OpClass(Upper("event_code"), name="gin_trgm_ops"),
                     name="tickets_event_code_trgm_idx"),
            GinIndex(OpClass(Upper("purpose"), name="gin_trgm_ops"),
                     name="tickets_purpose_trgm_idx"),
            GinIndex(OpClass(Upper("organizer"), name="gin_trgm_ops"),
                     name="tickets_organizer_trgm_idx"),
            GinIndex(OpClass(Upper("competitor_event_name"), name="gin_trgm_ops"),
                     name="tickets_competitor_trgm_idx"),
            GinIndex(OpClass(Upper("assigned_mr"), name="gin_trgm_ops"),
                     name="tickets_assigned_mr_trgm_idx"),
            GinIndex(OpClass(Upper("assign_name"), name="gin_trgm_ops"),
                     name="tickets_assign_name_trgm_idx"),
            # Equality, not substring, so a plain btree on the same expression
            # __iexact emits. Feeds the per-user scope in get_queryset().
            models.Index(Upper("added_user_text"),
                         name="tickets_added_user_idx"),
        ]

    def save(self, *args, **kwargs):
        """
        Two derived values, kept in step on every save.

        `purpose` is stored upper-case, never lower. It is free text that keys
        the ticket-number counter, and webhook senders push lower-case codes, so
        "ccu" arriving from a webhook would open a second sequence beside "CCU"
        and restart it at 10001.

        `link_key` is the digest the repeated-link check reads, derived from
        link_url.

        Both are enforced here because every write path except the Smart Import
        update branch goes through save(): the API, the webhook (which reuses
        TicketCreateSerializer), MR edits, bulk update (accounts/bulk_update.py
        writes with obj.save() per row) and the admin. That one queryset.update()
        path normalises the purpose in utils._coerce_row and sets link_key in the
        importer itself.

        When a caller passes update_fields, link_key joins it only if link_url is
        in there — otherwise a targeted save (the workflow transitions all use
        one) would widen into a column it did not touch.
        """
        # Local imports: utils imports this module.
        from .utils import link_digest, normalize_purpose

        self.purpose = normalize_purpose(self.purpose)
        self.link_key = link_digest(self.link_url)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
            if "link_url" in update_fields:
                update_fields.add("link_key")
                kwargs["update_fields"] = list(update_fields)
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.ticket_number or '(pending)'} — {self.purpose[:40]}"


class TicketSequence(models.Model):
    """
    Tracks the next ticket_number to assign per purpose_key.
    Mirrors Zoho's Ticket_Sequences table.
    Updated once per batch run by the backfill management command (D6).
    """
    purpose_key     = models.CharField(max_length=50, unique=True, db_index=True)
    last_number     = models.PositiveIntegerField(default=10000)
    added_user_text = models.CharField(max_length=150, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ticket_sequences"
        ordering = ["purpose_key"]

    def __str__(self):
        return f"{self.purpose_key} → {self.last_number}"
