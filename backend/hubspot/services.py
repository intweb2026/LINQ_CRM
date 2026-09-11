"""
hubspot/services.py
────────────────────
The sync jobs. Everything here writes the cache tables in models.py and is
called from a management command or cron, never from a request.

Each function returns a small dict the command prints and the SyncLog row
stores, so "did this work, and what did it do" is answerable without reading
the logs.

DEGRADING IS THE POINT. A missing token, a missing scope or a HubSpot outage
must leave the CRM working with whatever it already cached. Every entry point
catches HubSpotError, records it, and returns; none of them raise into a cron
job that has four other datasets to sync.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from book_event.models import SyncLog

from . import client
from .models import HubSpotCall, HubSpotContact, HubSpotPurposeCount

logger = logging.getLogger(__name__)

# How long a cached contact is trusted before it is refetched. Phone numbers
# barely move, so a week is generous and keeps a nightly run to the handful of
# leads that are actually new.
CONTACT_TTL = timedelta(days=7)

# Call counts move every shift, so they get hours rather than days. The cap is
# what keeps a run bounded: the oldest counts are refreshed first and the rest
# wait for the next run, which spreads the cost instead of trying to be
# complete in one pass.
CALL_COUNT_TTL = timedelta(hours=6)
CALL_COUNT_MAX_PER_RUN = 150

# How far back the first calls sync reaches. After that the watermark takes
# over and each run fetches only what is new.
CALLS_BACKFILL_DAYS = 14

# Mailable counts move as marketing works the lists, so they get hours rather
# than the week a phone number gets. Two requests per code makes a wide refresh
# cheap enough that this can be short.
MAILABLE_TTL = timedelta(hours=8)


def _log(dataset: str, *, rows: int = 0, error: str = "") -> None:
    """
    Record the outcome on the SyncLog row for this dataset.

    Reusing book_event.SyncLog rather than adding a table: it already holds
    exactly dataset, last run, status, row count and error message, which is
    the whole question being asked, and the Google Sync page already reads it.
    """
    SyncLog.objects.update_or_create(
        dataset=dataset,
        defaults={
            "last_synced_at": timezone.now(),
            "last_status": SyncLog.Status.FAILED if error else SyncLog.Status.SUCCESS,
            "records_synced": rows,
            "error_message": error[:2000],
        },
    )


def sync_contacts(emails: list[str], *, force: bool = False) -> dict:
    """
    Make sure every address in `emails` has a fresh-enough contact row.

    Only the missing and the stale are fetched. Passing the whole active lead
    set every night is therefore cheap: on a steady day it resolves to the few
    addresses that are new.
    """
    result = {"asked": 0, "fetched": 0, "written": 0, "skipped_fresh": 0,
              "via_secondary": 0, "error": ""}
    wanted = sorted({e.strip().lower() for e in emails if e and e.strip()})
    result["asked"] = len(wanted)
    if not wanted:
        return result
    if not client.enabled():
        result["error"] = "HUBSPOT_TOKEN is not set"
        logger.info("hubspot: contact sync skipped, no token")
        return result

    cutoff = timezone.now() - CONTACT_TTL
    fresh = set()
    if not force:
        fresh = set(
            HubSpotContact.objects
            .filter(email__in=wanted, fetched_at__gte=cutoff)
            .values_list("email", flat=True)
        )
    todo = [e for e in wanted if e not in fresh]
    result["skipped_fresh"] = len(fresh)
    if not todo:
        return result

    try:
        found = client.contacts_by_email(todo)
    except client.HubSpotError as exc:
        result["error"] = str(exc)
        _log("hubspot_contacts", rows=0, error=str(exc))
        logger.warning("hubspot: contact sync failed: %s", exc)
        return result

    # SECOND PASS over whatever the primary-email read could not resolve.
    # HubSpot matches a batch read on the primary address only, so a booking
    # made with somebody's secondary address looked like a contact that does not
    # exist, and the invoice-type classifier read that absence as evidence of a
    # blind invoice. A wrong verdict caused by a lookup miss is the worst kind,
    # so the misses are retried across every email field.
    missing = [e for e in todo if e not in found]
    if missing:
        try:
            extra = client.contacts_by_any_email(missing)
        except client.HubSpotError as exc:
            extra = {}
            logger.info("hubspot: secondary email pass failed: %s", exc)
        if extra:
            logger.info(
                "credit_control: %s of %s unmatched addresses resolved on a "
                "secondary email field", len(extra), len(missing),
            )
        found.update(extra)
        result["via_secondary"] = len(extra)

    result["fetched"] = len(found)
    now = timezone.now()
    for email, props in found.items():
        HubSpotContact.objects.update_or_create(
            email=email,
            defaults={
                "contact_id": str(props.get("id") or ""),
                "phone": (props.get("phone") or "")[:50],
                "mobile_phone": (props.get("mobilephone") or "")[:50],
                "direct_phone": (props.get("direct_phone_number") or "")[:50],
                "fetched_at": now,
            },
        )
        result["written"] += 1

    # An address HubSpot does not know still gets a row, stamped now, so the
    # next run does not ask about it again for a week. Without this, every
    # unknown address is refetched on every single run forever.
    for email in todo:
        if email not in found:
            HubSpotContact.objects.update_or_create(
                email=email, defaults={"fetched_at": now},
            )

    _log("hubspot_contacts", rows=result["written"])
    return result


def sync_call_counts(emails: list[str], *, limit: int = CALL_COUNT_MAX_PER_RUN) -> dict:
    """
    Refresh per-contact call totals for the stalest contacts first.

    Bounded on purpose. This is one request per contact, so a thousand active
    leads would be a thousand requests; refreshing the oldest `limit` and
    letting the rest wait keeps any single run short and still converges,
    because the ones skipped today are the stalest tomorrow.
    """
    result = {"considered": 0, "updated": 0, "failed": 0, "error": ""}
    if not client.enabled():
        result["error"] = "HUBSPOT_TOKEN is not set"
        return result

    wanted = {e.strip().lower() for e in emails if e and e.strip()}
    if not wanted:
        return result

    cutoff = timezone.now() - CALL_COUNT_TTL
    rows = list(
        HubSpotContact.objects
        .filter(email__in=wanted)
        .exclude(contact_id="")
        .filter(Q(times_called_at__isnull=True) | Q(times_called_at__lt=cutoff))
        .order_by("times_called_at")[:limit]
    )
    result["considered"] = len(rows)

    for row in rows:
        try:
            summary = client.call_summary_for_contact(row.contact_id)
        except client.HubSpotError as exc:
            # A 403 here means the private app is missing crm.objects.calls.read.
            # Stop the whole pass rather than log the same 403 a hundred times.
            result["error"] = str(exc)
            result["failed"] += 1
            logger.warning("hubspot: call counts unavailable: %s", exc)
            break
        if summary is None:
            result["failed"] += 1
            continue
        HubSpotContact.objects.filter(pk=row.pk).update(
            times_called=summary["total"],
            times_called_at=timezone.now(),
            last_call_at=summary["last_at"],
            last_call_seconds=summary["last_seconds"],
        )
        result["updated"] += 1

    _log("hubspot_call_counts", rows=result["updated"], error=result["error"])
    return result


def sync_calls() -> dict:
    """
    Pull call engagements logged since the watermark into the cache.

    Idempotent: rows are upserted on HubSpot's own call id, so an overlapping
    window costs nothing. The watermark is stored on the SyncLog row's
    last_synced_at, which is one fewer place to keep state than a Script
    Property, and it is deliberately rewound slightly on each run so a call
    logged during the previous run cannot fall in the gap.
    """
    result = {"fetched": 0, "written": 0, "since": None, "error": ""}
    if not client.enabled():
        result["error"] = "HUBSPOT_TOKEN is not set"
        return result

    row = SyncLog.objects.filter(dataset="hubspot_calls").first()
    if row and row.last_synced_at:
        since = row.last_synced_at - timedelta(hours=1)
    else:
        since = timezone.now() - timedelta(days=CALLS_BACKFILL_DAYS)
    result["since"] = since.isoformat()

    try:
        batch = list(client.calls_since(since))
    except client.HubSpotError as exc:
        result["error"] = str(exc)
        _log("hubspot_calls", rows=0, error=str(exc))
        logger.warning("hubspot: calls sync failed: %s", exc)
        return result

    result["fetched"] = len(batch)

    # One owners read per sync, inverted to id -> email. Without it every call
    # lands with a bare owner id that nothing in the CRM can match a person to.
    try:
        owner_email = {oid: email for email, oid in client.owners_by_email().items()}
    except client.HubSpotError as exc:
        owner_email = {}
        logger.info("hubspot: owners unavailable, calls stored unattributed: %s", exc)

    for call in batch:
        props = call.get("properties") or {}
        when = client.parse_ts(props.get("hs_timestamp"))
        if not when:
            continue
        HubSpotCall.objects.update_or_create(
            call_id=str(call.get("id")),
            defaults={
                "owner_id": str(props.get("hubspot_owner_id") or ""),
                "owner_email": owner_email.get(
                    str(props.get("hubspot_owner_id") or ""), "",
                ),
                "occurred_at": when,
                # The UTC date IS the shift day. See HubSpotCall's docstring.
                "shift_date": when.date(),
                "disposition": (props.get("hs_call_disposition") or "")[:100],
            },
        )
        result["written"] += 1

    # Nothing reads a call older than the dashboard window, so old rows are
    # pruned rather than kept forever. This is a cache.
    HubSpotCall.objects.filter(
        occurred_at__lt=timezone.now() - timedelta(days=CALLS_BACKFILL_DAYS * 3)
    ).delete()

    _log("hubspot_calls", rows=result["written"])
    return result


def sync_mailable_counts(purposes: list[str], *, force: bool = False) -> dict:
    """
    Refresh the contact and mailable counts for a set of `contact_purpose` codes.

    Two requests per code and no paging, however many contacts sit under it.
    Only the missing and the stale are fetched, so a nightly run over a few
    hundred event codes settles down to the handful that changed.

    Marketing works these lists continuously, so the window is hours rather than
    the week a phone number gets.
    """
    result = {"asked": 0, "fetched": 0, "skipped_fresh": 0, "error": ""}
    wanted = sorted({(p or "").strip() for p in purposes if p and p.strip()})
    result["asked"] = len(wanted)
    if not wanted:
        return result
    if not client.enabled():
        result["error"] = "HUBSPOT_TOKEN is not set"
        return result

    cutoff = timezone.now() - MAILABLE_TTL
    fresh = set()
    if not force:
        fresh = set(
            HubSpotPurposeCount.objects
            .filter(purpose__in=wanted, fetched_at__gte=cutoff)
            .values_list("purpose", flat=True)
        )
    result["skipped_fresh"] = len(fresh)

    for purpose in wanted:
        if purpose in fresh:
            continue
        try:
            counts = client.count_mailable(purpose)
        except client.HubSpotError as exc:
            # One bad code must not lose the rest of the run, but a 403 means
            # the scope is missing and every remaining code would fail the same
            # way, so that one stops.
            result["error"] = str(exc)
            logger.warning("hubspot: mailable count failed for %s: %s", purpose, exc)
            if exc.status == 403:
                break
            continue
        HubSpotPurposeCount.objects.update_or_create(
            purpose=purpose,
            defaults={
                "mailable_count": counts["mailable"],
                "total_count": counts["total"],
                "fetched_at": timezone.now(),
            },
        )
        result["fetched"] += 1

    _log("hubspot_mailable", rows=result["fetched"], error=result["error"])
    return result
