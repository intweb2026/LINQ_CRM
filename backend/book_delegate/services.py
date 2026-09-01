"""
book_delegate/services.py

DelegatePaymentOverrideResolver
─────────────────────────────────
Detects shared vs mixed payment values across delegates on an invoice.
Used to decide whether the bottom payment section should show a single
value or a "Mixed" indicator, and to sync invoice-level changes to
delegates that haven't been individually overridden.
"""
from __future__ import annotations
from typing import Any

from django.utils import timezone


class DelegatePaymentOverrideResolver:
    """
    Given a BookEvent invoice, inspects its delegates and determines:

    - For each payment field: whether all delegates share the same effective
      value (shared) or have different values (mixed).
    - Which delegates have been individually overridden vs inheriting from
      the invoice.

    Usage:
        resolver = DelegatePaymentOverrideResolver(invoice)
        summary  = resolver.resolve()

    Returns a dict with structure:
        {
            "payment_status": {"shared": True,  "value": "Paid",  "mixed": False},
            "payment_type":   {"shared": False, "value": None,    "mixed": True},
            "payment_date":   {"shared": True,  "value": "2025-03-01", "mixed": False},
            "paid_or_free":   {"shared": True,  "value": "Paid",  "mixed": False},
            "ticket_tier":    {"shared": False, "value": None,    "mixed": True},
            "overrides":      [{"delegate_id": 3, "fields": ["payment_status"]}],
        }
    """

    FIELDS = [
        "payment_status",
        "payment_type",
        "payment_date",
        "paid_or_free",
        "ticket_tier",
    ]

    # Maps delegate override field → invoice field
    DELEGATE_FIELD_MAP = {
        "payment_status": ("delegate_payment_status",  "payment_status"),
        "payment_type":   ("delegate_payment_type",    "payment_type"),
        "payment_date":   ("delegate_payment_date",    "payment_date"),
        "paid_or_free":   ("delegate_paid_or_free",    "paid_or_free"),
        "ticket_tier":    ("delegate_ticket_tier",     "ticket_tier"),
    }

    def __init__(self, invoice):
        self.invoice = invoice

    def _effective(self, delegate, field: str) -> Any:
        """Return effective value for a field: delegate override or invoice default."""
        delegate_field, invoice_field = self.DELEGATE_FIELD_MAP[field]
        override = getattr(delegate, delegate_field, None)
        if override not in (None, ""):
            return override
        return getattr(self.invoice, invoice_field, None) or ""

    def resolve(self) -> dict:
        delegates = list(self.invoice.delegates.all())
        if not delegates:
            return {
                f: {"shared": True, "value": getattr(self.invoice, self.DELEGATE_FIELD_MAP[f][1], ""), "mixed": False}
                for f in self.FIELDS
            } | {"overrides": []}

        result: dict[str, Any] = {}
        overrides = []

        for field in self.FIELDS:
            delegate_attr, _ = self.DELEGATE_FIELD_MAP[field]
            values = [self._effective(d, field) for d in delegates]
            unique = set(v for v in values if v not in (None, ""))

            result[field] = {
                "shared": len(unique) <= 1,
                "value":  next(iter(unique)) if len(unique) == 1 else None,
                "mixed":  len(unique) > 1,
            }

        for delegate in delegates:
            overridden_fields = [
                field for field in self.FIELDS
                if getattr(delegate, self.DELEGATE_FIELD_MAP[field][0], None) not in (None, "")
            ]
            if overridden_fields:
                overrides.append({
                    "delegate_id":   delegate.id,
                    "delegate_name": delegate.full_name,
                    "fields":        overridden_fields,
                })

        result["overrides"] = overrides
        return result

    def sync_invoice_to_delegates(self, fields: list[str] | None = None) -> int:
        """
        Push invoice-level values to delegates that do NOT have individual overrides
        for the given fields. Returns count of delegates updated.

        Use this when the bottom payment section is saved and you want to
        propagate the invoice change to non-overridden delegates.
        """
        from book_delegate.models import BookDelegate

        target_fields = fields or self.FIELDS
        delegates = list(self.invoice.delegates.all())
        updated = 0

        for delegate in delegates:
            update_kwargs = {}
            for field in target_fields:
                delegate_attr, invoice_attr = self.DELEGATE_FIELD_MAP[field]
                # Only clear/sync if delegate has NO individual override
                if getattr(delegate, delegate_attr, None) in (None, ""):
                    pass  # Already inherits from invoice — no action needed
                # If delegate HAD an override and invoice changed, preserve delegate override
            if update_kwargs:
                # updated_at IS SET BY HAND, for the reason spelled out in
                # clear_delegate_overrides() below: a queryset .update() does not
                # fire auto_now, so the row would keep its old watermark and the
                # Data API's ?updated_since= delta feed would never offer it
                # again. Written here rather than only in the sibling method
                # because update_kwargs is currently always empty — the loop
                # above resolves to "no action needed" on every branch — and a
                # future body for it must not have to rediscover this.
                update_kwargs["updated_at"] = timezone.now()
                BookDelegate.objects.filter(id=delegate.id).update(**update_kwargs)
                updated += 1

        return updated

    def clear_delegate_overrides(self, delegate_ids: list[int], fields: list[str] | None = None) -> int:
        """
        Remove per-delegate overrides for specified fields, reverting them to
        invoice-level inheritance. Returns count of delegates updated.
        """
        from book_delegate.models import BookDelegate

        target_fields = fields or self.FIELDS
        update_kwargs = {
            self.DELEGATE_FIELD_MAP[f][0]: None
            for f in target_fields
        }
        # updated_at IS SET BY HAND because a queryset .update() does NOT fire
        # auto_now — the ORM never instantiates the rows, so no field's pre_save()
        # runs and the column keeps whatever it held. That was invisible until the
        # Bookings table's default sort became ["-updated_at", "-id"]
        # (BookDelegateViewSet.ordering): clearing a delegate's payment overrides
        # is a real edit, made deliberately, that visibly changes five cells, and
        # it left the row exactly where it was while every lesser edit floated to
        # the top. timezone.now() rather than a literal, so it is tz-aware under
        # USE_TZ and stored as UTC like every other timestamp; IST is applied when
        # the cell is rendered, not when it is written.
        #
        # STILL ONE STATEMENT. The alternative — load each row and save() it — is
        # what accounts/bulk_update.py does, and correctly so, because BookDelegate
        # .save() derives edition and event_code there. Nothing derived depends on
        # these five override columns going NULL, so the set-based write stays.
        update_kwargs["updated_at"] = timezone.now()
        return BookDelegate.objects.filter(id__in=delegate_ids).update(**update_kwargs)
