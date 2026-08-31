"""
dataapi/models.py
──────────────────
Read-only API key for external data consumers.

WHY THIS IS NOT ONE OF THE TWO EXISTING KEY MODELS
`webhooks.WebhookApiKey` (header X-CRM-API-KEY) authenticates inbound webhook
payloads and carries no user identity. `book_event`'s ApiKeyAuthentication
(header X-API-KEY) authenticates website-to-CRM booking creation. Neither is
RBAC-scoped, and neither was designed for outbound export; repurposing either
would let a Sheets credential reach a write endpoint. This model is separate,
its authenticator is wired per-view only, and every resource it can reach is
read-only.
"""
import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


# The resources a key can be scoped to. This is the ONE list; the management
# viewset validates against it, and each data-export viewset's `resource_name`
# must appear here or a key can never be scoped to it. An empty `scopes` list on
# a row still means unrestricted, which is why the model itself does not
# constrain the field.
DATA_API_SCOPES = ("bookings", "delegates", "events", "tickets")


class DataApiKey(models.Model):
    """
    The raw key is shown ONCE on creation. Only its SHA-256 hash is stored, so
    a leaked database row cannot be replayed against the API.
    """

    name = models.CharField(max_length=150)
    key_hash = models.CharField(
        max_length=64, unique=True, db_index=True,
        help_text="SHA-256 hex digest of the raw API key",
    )
    key_preview = models.CharField(
        max_length=20, blank=True, default="",
        help_text="First 8 + last 4 chars for admin display",
    )
    scopes = models.JSONField(
        default=list, blank=True,
        help_text='Allowed resources, e.g. ["bookings","delegates"]. Empty = all.',
    )
    is_active = models.BooleanField(default=True, db_index=True)
    rate_limit_per_minute = models.PositiveIntegerField(default=60)
    expires_at = models.DateTimeField(
        null=True, blank=True, help_text="Null = never expires",
    )
    notes = models.TextField(blank=True, default="")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="created_data_api_keys",
    )
    created_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(null=True, blank=True)
    usage_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "data_api_keys"
        ordering = ["-created_at"]
        verbose_name = "Data API Key"
        verbose_name_plural = "Data API Keys"

    def __str__(self):
        return f"{self.name} ({self.key_preview})"

    # ── Key lifecycle ─────────────────────────────────────────────────────

    PREFIX = "dapi_"

    @classmethod
    def generate_raw_key(cls) -> str:
        return cls.PREFIX + secrets.token_urlsafe(40)

    @staticmethod
    def hash_key(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    @classmethod
    def create_key(cls, *, name, created_by=None, scopes=None, notes="",
                   rate_limit_per_minute=60, expires_at=None):
        """
        Create a new DataApiKey. Returns (instance, raw_key).
        The raw_key is the ONLY time the plaintext is available.
        """
        raw_key = cls.generate_raw_key()
        # ASCII "..." rather than a single ellipsis character: this string is
        # echoed to a Windows console by create_data_api_key, and the default
        # console codepage there cannot encode U+2026.
        preview = raw_key[:8] + "..." + raw_key[-4:]
        instance = cls.objects.create(
            name=name,
            key_hash=cls.hash_key(raw_key),
            key_preview=preview,
            scopes=scopes or [],
            created_by=created_by,
            notes=notes,
            rate_limit_per_minute=rate_limit_per_minute,
            expires_at=expires_at,
        )
        return instance, raw_key

    def is_valid(self) -> bool:
        if not self.is_active:
            return False
        if self.expires_at and self.expires_at < timezone.now():
            return False
        return True

    def has_scope(self, resource: str) -> bool:
        """Empty scopes list = unrestricted."""
        if not self.scopes:
            return True
        return resource in self.scopes

    def record_usage(self):
        self.last_used_at = timezone.now()
        self.usage_count += 1
        self.save(update_fields=["last_used_at", "usage_count"])


# ── Deletion tombstones ───────────────────────────────────────────────────────
#
# Every model this API exports is HARD deleted; there is no is_deleted column
# and no archive table anywhere in this schema (see
# book_delegate/tests_hard_delete.py). That is fine for a full pull and wrong
# for a delta pull: ?updated_since= can only ever return rows that still exist,
# so a consumer polling it never learns that a record went, keeps the copy it
# already wrote, and its row count drifts permanently above the CRM's own. One
# tombstone per delete is the missing half of that feed.
#
# Keyed by the same `id` the export serialisers emit as Record ID, because that
# is the column the consumer upserts on and therefore the only one it can
# delete by.


class DeletedRecord(models.Model):
    resource = models.CharField(max_length=20)
    record_id = models.BigIntegerField()
    deleted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "data_api_deleted_records"
        # pk order, matching every export viewset: DataApiCursorPagination is
        # keyset pagination over pk and needs a unique monotonic sort.
        ordering = ["pk"]
        indexes = [
            models.Index(fields=["resource", "deleted_at"],
                         name="dapi_deleted_res_at_idx"),
        ]

    def __str__(self):
        return f"{self.resource}#{self.record_id} deleted {self.deleted_at}"


# Model label -> the resource_name its rows are exported under. The values are
# DATA_API_SCOPES entries, and the deletions endpoint checks the key's scope
# against them, so a key restricted to ["delegates"] reads delegate tombstones
# and no others.
DELETION_SOURCES = {
    "book_event.BookEvent": "bookings",
    "book_delegate.BookDelegate": "delegates",
    "events.Event": "events",
    "ticket_central.Ticket": "tickets",
}


def record_deletion(sender, instance, **kwargs):
    """
    post_delete receiver, wired in dataapi/apps.py.

    post_delete rather than an explicit call in each delete view, because the
    delete paths are many and two of them are not views at all: the Bookings
    modal deletes an invoice and Django cascades to its delegates, and
    delegates/bulk_delete/ deletes a queryset. A signal catches every one,
    cascades included, and cannot be forgotten by the next path somebody adds.

    ponytail: one INSERT per deleted row, and a receiver on these models also
    costs Django its fast-delete path. Both are fine at this table's real
    delete volume (tens of rows at a time); batch in pre_delete if a mass
    purge ever makes it hurt.
    """
    resource = DELETION_SOURCES.get(sender._meta.label)
    if resource and instance.pk is not None:
        DeletedRecord.objects.create(resource=resource, record_id=instance.pk)
