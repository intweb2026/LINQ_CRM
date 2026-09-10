"""
credit_control/engine.py
─────────────────────────
The routing pass. Reads book_events, book_delegates and events, and decides for
every invoice which bucket it belongs in and whose queue it sits on.

THE ENGINE MAKES NO NETWORK CALLS. Not to HubSpot, not to Anthropic. It is the
one piece of this module that must be able to run in a second on a laptop with
no credentials, because it is what decides who calls whom, and a routing pass
that can fail because a third party is slow is a routing pass that will
eventually leave a shift with no queue. The integrations run as their own
commands and write their own caches; this reads only the database.

IT IS ALSO IDEMPOTENT. Running it twice in a row changes nothing the second
time. Every decision is derived from the source and the lead's own stored
history, never from "what happened last run", which is what makes a re-run
after a crash safe and what makes the tests meaningful.

PRECEDENCE. The first rule that matches decides, and the order is not
negotiable because the rules overlap:

  1. the event's verdict excludes it        -> not chased at all
  2. the booking code is SpEx               -> its own tab, never Done
  3. no invoice date                        -> Not Invoiced Yet
  4. a payment date is present              -> Done, resolved
  5. otherwise                              -> active, and assigned

Assignment is one-way. Everything new goes to the team lead for its first four
days; on day 4 anything NOT in flight moves to an exec, carrying every remark
and disposition with it, and once with an exec it never moves again.

Rule 3 beating rule 4 is deliberate: an invoice with no date is not something
we know enough about to call paid, whatever else it carries.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone as dt_timezone

from django.db import transaction
from django.db.models import Count, Max
from django.utils import timezone

from accounts.models import User
from book_event.models import BookEvent
from events.models import Event
from teams.models import Team

from . import constants
from .models import CreditControlDisposition, CreditControlLead, CreditControlTouch

logger = logging.getLogger(__name__)

TEAM_NAME = "Credit Control"


# ── Booking code tests ──────────────────────────────────────────────────────

def _matches(code: str, needles) -> bool:
    text = (code or "").strip().lower()
    return any(n in text for n in needles)


def is_spex(code: str) -> bool:
    """SpEx and Speaker Table, which have their own tab and their own process."""
    return _matches(code, constants.SPEX_BOOKING_MATCH)


def is_speaker(code: str) -> bool:
    """
    A speaker booking, the only kind that can be a blind invoice.

    Tested AFTER is_spex by every caller, because "Speaker / GLD SpEx" answers
    true to both and SpEx wins.
    """
    return _matches(code, constants.SPEAKER_BOOKING_MATCH)


def is_addons(code: str) -> bool:
    """An add-ons-only booking, for the aged debtor breakdown."""
    return _matches(code, constants.ADDONS_BOOKING_MATCH)


# ── Dispositions ────────────────────────────────────────────────────────────

def normalize_disposition(value: str) -> str:
    """
    A stored disposition as its current label.

    A value retired months ago still sits on leads that were worked then, and
    it has to keep counting in the right group rather than falling into an
    "unknown" hole on the dashboard. Applied on every read; the value is
    rewritten in place the next time anything saves that lead.
    """
    text = (value or "").strip()
    return constants.RETIRED_DISPOSITIONS.get(text, text)


def category_map() -> dict[str, str]:
    """label -> category, including the retired labels, for the engine."""
    live = dict(CreditControlDisposition.objects.values_list("label", "category"))
    for old, new in constants.RETIRED_DISPOSITIONS.items():
        if new in live:
            live[old] = live[new]
    return live


def status_group_map() -> dict[str, str]:
    """label -> status group, including the retired labels, for the dashboard."""
    live = dict(CreditControlDisposition.objects.values_list("label", "status_group"))
    for old, new in constants.RETIRED_DISPOSITIONS.items():
        if new in live:
            live[old] = live[new]
    return live


def is_sticky(disposition: str, groups: dict[str, str]) -> bool:
    """
    Does this disposition pin its lead to whoever holds it, permanently?

    The test is the STATUS GROUP being Ongoing, not the category. An Ongoing
    case is one in flight with this caller: a callback booked, a date
    committed, sitting with their accounts team, a dispute being worked. Handing
    that to somebody else to even out a queue loses the thread, so it stays put
    for good, whoever holds it.

    A blank disposition is never sticky; nobody has spoken to anyone yet.
    """
    label = normalize_disposition(disposition)
    if not label:
        return False
    return groups.get(label) in constants.STICKY_STATUS_GROUPS


# ── Roster ──────────────────────────────────────────────────────────────────

class Roster:
    """
    Who takes first touch and who is in the handoff pool.

    Read from the Credit Control team on every pass rather than stored, so
    moving somebody in or out of the team is the whole of the change when the
    people change. `lead` is the team's `team_lead`; `pool` is every other
    member who can log in.

    Both may be empty, and that is not an exception. A brand-new install has no
    such team yet, and the honest behaviour is to bucket the leads correctly and
    leave them unassigned until somebody sets the team up, rather than to
    refuse to run.
    """

    def __init__(self, lead: User | None, pool: list[User], skipped=None):
        self.lead = lead
        self.pool = pool
        # Team members this roster deliberately left out, and why. Reported
        # rather than dropped: a caller silently missing from the roster is
        # indistinguishable from a caller with no work, and the first version of
        # this cost an afternoon working out why one person's queue and call
        # counts were empty.
        self.skipped = skipped or []

    @classmethod
    def load(cls) -> "Roster":
        team = (
            Team.objects
            .filter(name__iexact=TEAM_NAME, is_archived=False)
            .select_related("team_lead")
            .first()
        )
        if not team:
            logger.warning(
                "credit_control: no team named %r, leads will be bucketed but unassigned",
                TEAM_NAME,
            )
            return cls(None, [])

        everyone = list(User.objects.filter(team=team).order_by("id"))

        # A caller who cannot log in cannot work a queue, so assigning to them
        # would be a black hole: the leads would exist, be owned, and be
        # invisible to everybody but an all-access viewer. They are held out and
        # NAMED, which is the difference between a fixable message and a mystery.
        members, skipped = [], []
        for person in everyone:
            if not person.is_active:
                skipped.append((person, "the account is deactivated"))
            elif not person.login_access:
                skipped.append((person, "login access is off, so a queue would be invisible to them"))
            else:
                members.append(person)

        # TWO PLACES SPELL "team lead" IN THIS CODEBASE and they can disagree:
        # Team.team_lead is the FK the Teams screen writes, and User.is_team_lead
        # is a per-person flag. A team created with the flag set but the FK left
        # empty had no first-touch caller at all, and every new lead came out
        # unassigned. The FK wins where it is set; the flag is the fallback.
        lead = team.team_lead
        if lead is None:
            lead = next((m for m in members if m.is_team_lead), None)
            if lead is not None:
                logger.info(
                    "credit_control: team %r has no team_lead set; using %s, "
                    "who carries the is_team_lead flag",
                    TEAM_NAME, lead.username,
                )
        pool = [m for m in members if not lead or m.pk != lead.pk]
        if lead is None:
            logger.warning(
                "credit_control: team %r has no team lead, so new leads cannot "
                "be assigned a first touch", TEAM_NAME,
            )
        if not pool:
            logger.warning("credit_control: team %r has a lead but no pool", TEAM_NAME)
        return cls(lead, pool, skipped)

    def people(self) -> list[User]:
        """The lead first, then the pool. The order every report reads them in."""
        return ([self.lead] if self.lead else []) + self.pool

    def is_empty(self) -> bool:
        return self.lead is None and not self.pool


class PoolBalancer:
    """
    Hands a lead to whichever pool member is carrying the least.

    "Split equally between the rest" measured rather than counted out. A stored
    round-robin counter drifts the moment anything else moves a lead, and it
    cannot see that one caller already holds forty leads and another twelve;
    picking the smallest live queue is self-correcting and needs no state.

    Ties break on who has waited longest for a lead, so two empty queues fill
    alternately instead of one of them taking everything.
    """

    _EPOCH = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)

    def __init__(self, pool: list[User]):
        self.pool = pool
        counts = dict(
            CreditControlLead.objects
            .filter(bucket=CreditControlLead.Bucket.ACTIVE, assigned_to__in=pool)
            .values_list("assigned_to")
            .annotate(n=Count("invoice"))
            .values_list("assigned_to", "n")
        )
        self.counts = {u.pk: counts.get(u.pk, 0) for u in pool}
        last = dict(
            CreditControlLead.objects
            .filter(assigned_to__in=pool)
            .values_list("assigned_to")
            .annotate(latest=Max("handed_off_at"))
            .values_list("assigned_to", "latest")
        )
        self.last_given = {u.pk: (last.get(u.pk) or self._EPOCH) for u in pool}

    def next_owner(self) -> User | None:
        if not self.pool:
            return None
        pick = min(self.pool, key=lambda u: (self.counts[u.pk], self.last_given[u.pk]))
        self.counts[pick.pk] += 1
        self.last_given[pick.pk] = timezone.now()
        return pick


# ── The pass ────────────────────────────────────────────────────────────────

def excluded_event_codes() -> set[str]:
    """
    Event codes whose invoices are not chased, from the live verdict.

    Read on every pass, never copied onto the lead, so a verdict changed this
    morning takes effect on the next refresh in both directions.
    """
    return set(
        Event.objects
        .filter(verdict__in=constants.EXCLUDED_VERDICTS)
        .values_list("event_code", flat=True)
    )


def _mark_resolved(lead, invoice, *, groups, now):
    """
    Move a lead to Resolved and record who collected it.

    Stamped ONCE. `resolved_at` being already set means this lead has been
    through here, and re-stamping it on every later pass would keep resetting
    the collection date to today and move the credit to whoever happens to own
    the row now.

    CREDIT GOES TO THE OWNER AT THE MOMENT OF PAYMENT, and whether it counts as
    their work is a separate question from whether they held it: an invoice paid
    before anybody reached the contact is a common outcome, and counting that as
    somebody's win would flatter the whole team. `resolved_after_effort` is what
    keeps the two apart, and it is true when the lead carried a disposition in
    flight, an Ongoing one, or any remark at all.
    """
    fields = ["bucket", "resolved", "done_reason", "updated_at"]
    lead.bucket = CreditControlLead.Bucket.DONE
    lead.resolved = bool(invoice and invoice.payment_date)
    lead.done_reason = _done_reason(invoice)

    if lead.resolved_at is None:
        label = normalize_disposition(lead.disposition)
        lead.resolved_at = now
        lead.resolved_by = lead.assigned_to
        lead.resolved_disposition = label
        lead.resolved_after_effort = bool(
            (label and groups.get(label) in constants.STICKY_STATUS_GROUPS)
            or lead.remark.strip()
            or label
        )
        fields += [
            "resolved_at", "resolved_by", "resolved_disposition",
            "resolved_after_effort",
        ]
    lead.save(update_fields=fields)


def _done_reason(invoice: BookEvent | None) -> str:
    """
    Why a lead is in Done, in words, from the LIVE invoice.

    Derived on every pass rather than frozen at the moment it left the queue.
    The predecessor build stored the reason once, so a lead that was paid a
    month later still read "Pending" on the Done tab forever.
    """
    if invoice is None:
        return "Removed from source"
    if invoice.payment_date:
        return f"Paid {invoice.payment_date:%d %b %Y}"
    status = (invoice.payment_status or "").strip()
    if status and status != BookEvent.PaymentStatus.PENDING:
        return status
    return "Still pending, review"


@transaction.atomic
def refresh(*, now=None) -> dict:
    """
    One full routing pass. Returns a summary the command prints.

    ponytail: one transaction and a per-lead save. At a few thousand pending
    invoices this is a sub-second pass and the simplest thing that is
    obviously correct; if the pending set ever reaches six figures, split the
    writes into bulk_update batches by bucket rather than adding cleverness
    here.
    """
    now = now or timezone.now()
    today = now.date()
    summary = {
        "active": 0, "not_invoiced": 0, "spex": 0, "done": 0,
        "excluded": 0, "created": 0, "handed_off": 0, "reactivated": 0,
        "unassigned": 0,
    }

    roster = Roster.load()
    groups = status_group_map()
    excluded = excluded_event_codes()

    pending = list(
        BookEvent.objects
        .filter(payment_status=BookEvent.PaymentStatus.PENDING)
        .only(
            "invoice_number", "event_code", "booking_code", "invoice_date",
            "payment_date", "payment_status", "company_name",
        )
    )
    leads = {
        lead.invoice_id: lead
        for lead in CreditControlLead.objects.select_related("assigned_to")
    }
    balancer = PoolBalancer(roster.pool)

    seen: set[str] = set()
    # Keys that must NOT be swept into Done at the end, because this pass has
    # already placed them somewhere else.
    placed: set[str] = set()

    for invoice in pending:
        key = invoice.invoice_number
        seen.add(key)
        if place_invoice(
            invoice, leads.get(key), roster=roster, groups=groups,
            excluded=excluded, balancer=balancer, summary=summary,
            now=now, today=today,
        ):
            placed.add(key)

    return _finish(leads, seen, placed, roster, excluded, groups, summary, now=now)


def place_invoice(invoice, lead, *, roster, groups, excluded, balancer,
                  summary, now, today) -> bool:
    """
    Decide where ONE invoice belongs, and write it. True if it was placed.

    THE ONLY COPY OF THE FIVE RULES. The full pass loops over this and the
    post-save signal calls it for a single invoice, so a lead routed the instant
    its booking is created and a lead routed by tonight's pass go through exactly
    the same code. Two implementations of a precedence order is two orders.

    `lead` is the existing row or None. `summary` is mutated, which is what lets
    the caller report over a batch of one or of thousands.
    """
    key = invoice.invoice_number

    # 1. The event is not being chased.
    if invoice.event_code in excluded:
        summary["excluded"] += 1
        if lead:
            verdict = "excluded"
            lead.bucket = CreditControlLead.Bucket.DONE
            lead.resolved = False
            lead.done_reason = f"Event {verdict}, verdict withdrawn"
            lead.save(update_fields=["bucket", "resolved", "done_reason", "updated_at"])
            return True
        return False

    # 2. SpEx has its own tab. Never assigned, never aged, never Done.
    #
    # A lead row IS created, unlike the sheet build, which kept these out of
    # its ledger to stay small. Here the SpEx tab, the dashboard count and
    # every other bucket then read the same table through the same query,
    # and any remark somebody logged before a booking code changed survives.
    # It is never assigned an owner, so it appears on nobody's queue.
    if is_spex(invoice.booking_code):
        summary["spex"] += 1
        lead = lead or _create_lead(invoice, summary)
        lead.bucket = CreditControlLead.Bucket.SPEX
        lead.resolved = False
        lead.done_reason = ""
        lead.assigned_to = None
        lead.first_seen = None
        lead.handed_off_at = None
        lead.save(update_fields=[
            "bucket", "resolved", "done_reason", "assigned_to",
            "first_seen", "handed_off_at", "updated_at",
        ])
        return True

    # 3. Not invoiced yet.
    #
    # In the sheet this tested a blank invoice date OR a blank invoice
    # number. In the CRM invoice_number is the primary key of book_events
    # and BookEvent.save() refuses to write without one, so only the date
    # half can actually occur. The test is kept in the same shape as the
    # rule everyone agreed, and the second clause is simply unreachable.
    if not invoice.invoice_date or not invoice.invoice_number:
        summary["not_invoiced"] += 1
        lead = lead or _create_lead(invoice, summary)
        lead.bucket = CreditControlLead.Bucket.NOT_INVOICED
        lead.resolved = False
        lead.done_reason = ""
        # The clock restarts if it ever becomes invoiced. Clearing first_seen
        # is what makes that lead a first-touch lead again rather than one
        # that is instantly four days old.
        lead.first_seen = None
        lead.handed_off_at = None
        lead.save(update_fields=[
            "bucket", "resolved", "done_reason", "first_seen",
            "handed_off_at", "updated_at",
        ])
        return True

    # 4. The source says it is paid.
    if invoice.payment_date:
        summary["done"] += 1
        if lead:
            lead.bucket = CreditControlLead.Bucket.DONE
            lead.resolved = True
            lead.done_reason = _done_reason(invoice)
            lead.save(update_fields=["bucket", "resolved", "done_reason", "updated_at"])
            return True
        return False

    # 5. Active. Create if new, then decide the owner.
    summary["active"] += 1
    if lead is None:
        lead = _create_lead(invoice, summary)

    fields = ["bucket", "resolved", "done_reason", "updated_at"]
    lead.bucket = CreditControlLead.Bucket.ACTIVE
    lead.resolved = False
    lead.done_reason = ""

    # Coming back from Not Invoiced Yet, or brand new: first touch, and the
    # clock starts now so it cannot be handed off on this same pass.
    if lead.first_seen is None:
        lead.first_seen = now
        lead.assigned_to = roster.lead
        lead.handed_off_at = None
        fields += ["first_seen", "assigned_to", "handed_off_at"]
    elif lead.assigned_to is None and roster.lead:
        # An owner went missing, most likely a deactivated account. Back to
        # first touch rather than left in nobody's queue.
        lead.assigned_to = roster.lead
        fields += ["assigned_to"]
    else:
        moved = _maybe_hand_off(
            lead, invoice, roster, balancer, groups, now=now, today=today,
        )
        if moved:
            summary["handed_off"] += 1
            # The snapshot columns ride along, or the save drops what
            # _maybe_hand_off just froze and the receiving caller inherits a
            # lead with no explanation of why they have it.
            fields += [
                "assigned_to", "handed_off_at", "handed_off_from",
                "handoff_disposition", "handoff_remark",
            ]

    if lead.assigned_to is None:
        summary["unassigned"] += 1
    lead.save(update_fields=fields)
    return True

def _finish(leads, seen, placed, roster, excluded, groups, summary, *, now):
    """
    Close out a full pass: sweep whatever left the pending pull, then reactivate.

    Only meaningful over the WHOLE set, which is why it is not part of
    place_invoice: deciding that a lead has vanished from the source requires
    having looked at the whole source, and a single-invoice route has not.
    """
    # Everything with a lead that is no longer in the pending pull. Its status
    # moved off Pending, or the row is gone from the source entirely.
    stale_keys = [k for k in leads if k not in seen and k not in placed]
    if stale_keys:
        live = {
            inv.invoice_number: inv
            for inv in BookEvent.objects.filter(invoice_number__in=stale_keys)
        }
        for key in stale_keys:
            lead = leads[key]
            invoice = live.get(key)
            reason = _done_reason(invoice)
            if lead.bucket == CreditControlLead.Bucket.DONE and lead.done_reason == reason:
                continue
            _mark_resolved(lead, invoice, groups=groups, now=now)
            summary["done"] += 1

    summary["reactivated"] = _reactivate(roster, excluded, now=now)
    _record_run("credit_control_routing", summary["active"])
    return summary


def _record_run(dataset: str, rows: int) -> None:
    """
    Stamp when this job last finished, on the SyncLog row for `dataset`.

    Routing and the classifier recorded nothing, so the dashboard could say
    when they next run but never when they last did — and "next in 40 minutes"
    is not reassuring if the last one failed silently three days ago. The
    HubSpot jobs already wrote here; these two simply were not.

    Never raises. A bookkeeping row must not be able to fail a routing pass
    that has already committed its real work.
    """
    from book_event.models import SyncLog

    try:
        SyncLog.objects.update_or_create(
            dataset=dataset,
            defaults={
                "last_synced_at": timezone.now(),
                "last_status": SyncLog.Status.SUCCESS,
                "records_synced": rows,
                "error_message": "",
            },
        )
    except Exception:  # noqa: BLE001
        logger.warning("credit_control: could not stamp %s", dataset, exc_info=True)


def _create_lead(invoice: BookEvent, summary: dict) -> CreditControlLead:
    """
    Insert the lead row now, so the caller can go on saving fields onto it.

    force_insert is required, not decorative. The primary key is the invoice
    number rather than an autofield, so Django sees a populated pk on an unsaved
    instance and a plain save() emits an UPDATE, which matches no row and raises
    "Save with update_fields did not affect any rows."
    """
    lead = CreditControlLead(invoice_id=invoice.invoice_number)
    lead.save(force_insert=True)
    summary["created"] += 1
    return lead


def _maybe_hand_off(lead, invoice, roster, balancer, groups, *, now, today) -> bool:
    """
    Move a lead from the team lead to an exec if the day-4 rule says so.

    Returns True if it moved. NOTHING THE CALLER WROTE IS TOUCHED: the
    disposition, the remark, the next action and the callback date all travel
    with the invoice, because the exec picking it up needs to know what the lead
    already did and carry the case on from there. The only columns this writes
    are the owner and the handoff stamp.

    Four things stop a handoff, and each of them matters:

      * THE CURRENT OWNER IS NOT THE TEAM LEAD. Handoff runs one way only,
        lead to exec. A lead already with an exec stays there; the pool is not
        a carousel and a case does not tour the team.
      * the disposition is sticky, meaning its status group is Ongoing. A case
        in flight stays with whoever has the thread, permanently.
      * the lead has not had a full shift with first touch. first_seen is
        compared against today, so an invoice backdated three weeks still gets
        one shift with the team lead before it can move.
      * it has been handed off once already, which the first rule mostly covers
        and this makes explicit.
    """
    if roster.lead is None or lead.assigned_to_id != roster.lead.pk:
        return False
    if is_sticky(lead.disposition, groups):
        return False
    if lead.handed_off_at:
        return False
    if lead.first_seen is None or lead.first_seen.date() >= today:
        return False
    days = (today - invoice.invoice_date).days if invoice.invoice_date else 0
    if days < constants.HANDOFF_AFTER_DAYS:
        return False
    owner = balancer.next_owner()
    if owner is None or owner.pk == lead.assigned_to_id:
        return False

    # WHAT THE RECEIVING CALLER INHERITS, frozen here. The live disposition and
    # remark travel with the lead, which is right, but they immediately become
    # the NEW owner's editable fields: the first thing they do is type over the
    # previous caller's sentence, and the context of why they were given it goes
    # with it. Snapshotting the three at the moment of the move is what lets the
    # queue show "handed to you by Bruce, who logged VM: switchboard closes at
    # 4" after Derek has written his own note over the top.
    lead.handed_off_from = lead.assigned_to
    lead.handoff_disposition = normalize_disposition(lead.disposition)
    lead.handoff_remark = lead.remark
    lead.assigned_to = owner
    lead.handed_off_at = now
    return True


def _reactivate(roster: Roster, excluded: set[str], *, now) -> int:
    """
    Rescue anything sitting in Done that the source says is still owed.

    A safety net rather than a normal path. A lead reaches Done because the
    source said so, and the source can change its mind: a payment reversed, a
    status corrected, a verdict restored. Without this the lead would stay in
    Done forever and nobody would ever call about the money again.

    A reactivated lead keeps its remarks and its disposition, because that work
    happened, but goes back to first touch with the clock restarted, because
    whatever was agreed months ago is no longer current.
    """
    candidates = (
        CreditControlLead.objects
        .filter(bucket=CreditControlLead.Bucket.DONE)
        .select_related("invoice")
    )
    revived = 0
    for lead in candidates:
        invoice = lead.invoice
        if invoice is None:
            continue
        if (invoice.payment_status or "").strip() != BookEvent.PaymentStatus.PENDING:
            continue
        if invoice.payment_date:
            continue
        if not invoice.invoice_date:
            continue
        if invoice.event_code in excluded:
            continue
        if is_spex(invoice.booking_code):
            continue
        lead.bucket = CreditControlLead.Bucket.ACTIVE
        lead.resolved = False
        lead.done_reason = ""
        lead.assigned_to = roster.lead
        lead.first_seen = now
        lead.handed_off_at = None
        lead.save(update_fields=[
            "bucket", "resolved", "done_reason", "assigned_to",
            "first_seen", "handed_off_at", "updated_at",
        ])
        revived += 1
    return revived


# ── Recording a caller's edit ───────────────────────────────────────────────

# THREE FIELDS, not four. Next Action was removed on request: it duplicated
# what a remark already says and gave the caller two places to write the same
# sentence. The database column is deliberately left in place, holding whatever
# was typed before, because dropping it would destroy that text to tidy a schema
# nobody is reading. It is simply no longer writable or displayed.
EDITABLE_FIELDS = ("disposition", "remark", "callback_date")


def record_touch(lead: CreditControlLead, user, changes: dict) -> CreditControlLead:
    """
    Apply a caller's edit and log it, in one place.

    Called from the API on every PATCH. It exists so the audit row and the
    lead's own stamp can never disagree: the Apps Script build wrote them from
    two different code paths and lost remarks whenever a rebuild overtook an
    edit.

    Only the four caller-owned fields can be written here. Ownership, bucket
    and everything the classifier decides are not in EDITABLE_FIELDS, so a
    malformed or hostile PATCH cannot move a lead to another queue.
    """
    applied = {}
    for field in EDITABLE_FIELDS:
        if field not in changes:
            continue
        value = changes[field]
        if field == "disposition":
            value = normalize_disposition(value)
        if getattr(lead, field) != value:
            setattr(lead, field, value)
            applied[field] = value

    if not applied:
        return lead

    lead.last_touched_at = timezone.now()
    lead.last_touched_by = user
    lead.save(update_fields=list(applied) + ["last_touched_at", "last_touched_by", "updated_at"])

    CreditControlTouch.objects.create(
        lead=lead,
        user=user,
        disposition=lead.disposition,
        remark=lead.remark[:2000],
        fields_changed=",".join(sorted(applied)),
    )
    return lead


def shift_matrix(*, days: int = constants.ACTIVITY_DAYS, now=None, only=None) -> dict:
    """
    Calls per caller per shift, from the HubSpot cache, with a fallback.

    The shift day is the UTC date. The callers work 6:30pm to 3:30am IST, which
    is 13:00 to 22:00 UTC, so a shift that crosses midnight in IST sits entirely
    inside one UTC day; there is nothing to stitch together and no cutoff hour
    to tune. The date is also the IST evening the shift began, which is how the
    column is labelled.

    Falls back to counting logged dispositions from CreditControlTouch when
    HubSpot has told us nothing, which is the case on any machine without a
    token and on any portal whose private app lacks the calls scope. The
    fallback counts a different thing and the payload says so, so the dashboard
    can label it honestly rather than quietly showing a smaller number.

    `only` NARROWS IT TO ONE PERSON, and it is a permission boundary rather
    than a display preference. The rest of the dashboard already scoped itself
    to the caller asking, and this section did not, so an exec who could see
    only their own leads was nevertheless shown every colleague's call count.
    When `only` is set the off-team row is dropped as well: it is an aggregate
    of other people's calls, so leaving it in would leak the same thing one
    level up.
    """
    from django.db.models import Count

    from hubspot.models import HubSpotCall

    now = now or timezone.now()
    dates = [(now - timedelta(days=offset)).date() for offset in range(days - 1, -1, -1)]
    roster = Roster.load()
    people = roster.people()
    if only is not None:
        people = [p for p in people if p.pk == only.pk] or [only]

    # ATTRIBUTED BY EMAIL, which the calls sync resolves from HubSpot's owners
    # endpoint and stores on each row. An earlier version read
    # `user.hubspot_owner_id`, a field the User model does not have, so every
    # count came out zero and the matrix looked like a quiet week rather than a
    # broken join. Email is also the mapping that survives a team change without
    # anybody editing a constant.
    counted: dict[tuple[str, object], int] = {}
    for owner_email, day, n in (
        HubSpotCall.objects
        .filter(shift_date__gte=dates[0])
        .exclude(owner_email="")
        .values_list("owner_email", "shift_date")
        .annotate(n=Count("call_id"))
    ):
        counted[((owner_email or "").strip().lower(), day)] = n

    source = "hubspot" if counted else "dispositions"
    matrix = []
    if source == "dispositions":
        touches = (
            CreditControlTouch.objects
            .filter(created_at__date__gte=dates[0])
            .exclude(disposition="")
            .values_list("user", "created_at__date")
            .annotate(n=Count("id"))
        )
        by_user = {(uid, day): n for uid, day, n in touches}
        for person in people:
            matrix.append({
                "user": person.get_full_name() or person.username,
                "counts": [by_user.get((person.pk, day), 0) for day in dates],
            })
    else:
        for person in people:
            email = (getattr(person, "email", "") or "").strip().lower()
            matrix.append({
                "user": person.get_full_name() or person.username,
                "counts": [counted.get((email, day), 0) for day in dates],
            })
        # Calls placed by somebody outside the team are reported rather than
        # dropped, so the column totals still add up to what HubSpot holds and a
        # caller working under the wrong HubSpot account is visible instead of
        # silently missing.
        team_emails = {
            (getattr(p, "email", "") or "").strip().lower() for p in people
        }
        other = [
            sum(n for (email, day), n in counted.items()
                if email not in team_emails and day == d)
            for d in dates
        ]
        # Only for somebody who may see the whole team; for one person it would
        # be a bucket of other people's calls under a different name.
        if only is None and any(other):
            matrix.append({"user": "Outside the team", "counts": other})

    return {
        "source": source,
        "dates": [d.isoformat() for d in dates],
        "rows": matrix,
    }
