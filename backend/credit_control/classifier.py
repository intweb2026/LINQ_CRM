"""
credit_control/classifier.py
─────────────────────────────
Blind Invoice or Requested, for speaker invoices, and the handoff brief.

WHAT THE QUESTION IS. A blind invoice is one IQ-Hub raised and sent to a
speaker who never registered online and never supplied billing or registration
details. A requested invoice is one where the speaker gave registration or
billing details, went through the booking link themselves, or asked for an
invoice or a quote. Credit control chases the two completely differently, and
the answer is not in the CRM: it is in the email history, which is why this
file exists at all.

FOUR THINGS MAKE THE VERDICT TRUSTWORTHY RATHER THAN MERELY AUTOMATIC.

  1. MOST ROWS NEVER REACH THE MODEL. Three deterministic rules settle the easy
     cases for nothing: not a speaker booking, so no verdict at all; no HubSpot
     contact for the address, so blind; the contact went through a form or
     booking, so requested. Asking a model to re-derive what a database lookup
     already knows is slower, dearer and less accurate.

  2. THE ANSWER IS TYPED, NOT PROSE. `messages.parse` validates the response
     against a Pydantic model, so a malformed answer is a caught exception and
     never a badly-parsed string written into a column.

  3. A QUOTE, WHERE THERE IS ONE, IS CHECKED. When the verdict rests on
     something said, the model must quote it verbatim and that line has to
     appear in the evidence sent, or the verdict is discarded and the row goes
     to a human. One substring test, and the cheapest possible guard against a
     confident invention. A blind invoice is usually proved by the ABSENCE of
     any request, so an empty quote is allowed and expected there; demanding one
     regardless only taught the model to quote a greeting to fill the field.

  4. IT IS ALLOWED TO SAY IT DOES NOT KNOW. Below the confidence floor, or with
     insufficient evidence, the row stays unclassified and visible. Unclassified
     is a row of its own on the dashboard for exactly this reason: a guess
     folded into Delegates would be invisible, and invisible is worse than
     unknown.

The human override is never touched by anything in this file.

ponytail: one synchronous call per row, capped per run. The Batches API would
halve the cost and is the right move for a large backfill, but it adds submit,
poll and reconcile for a first pass that is about 121 rows; switch when a
backfill is thousands of rows rather than hundreds.
"""
from __future__ import annotations

import logging
from datetime import date

from django.conf import settings
from django.utils import timezone

from hubspot import client as hs
from hubspot.models import HubSpotContact

from . import constants
from .engine import is_spex, is_speaker
from .models import CreditControlLead

logger = logging.getLogger(__name__)

MODEL = "claude-opus-5"

# The rubric. Long, stable and first in the request, which is what makes prompt
# caching work: every row after the first reads this from cache instead of
# paying for it again. Nothing volatile may be added to this string, not a
# timestamp, not a row id, or the cache is invalidated on every call and the
# saving disappears silently.
RUBRIC = """You classify invoices raised by IQ-Hub, a conference organiser, to \
speakers at its events. Exactly two classes exist.

BLIND INVOICE. IQ-Hub raised and sent the invoice to a speaker who never \
registered online and never supplied billing or registration details. Nobody on \
the speaker's side asked for it. Typical evidence: no reply to the invoice at \
all; a reply asking what the invoice is for; a reply saying they never booked or \
never agreed to pay; only outbound mail from IQ-Hub with no substantive inbound \
reply; the speaker asking who authorised the booking.

REQUESTED. The speaker's side asked for the invoice, or supplied what was needed \
to raise one. Typical evidence: they asked for an invoice, a quote or a proforma; \
they supplied billing details, a VAT or tax number, a PO number, or a purchase \
order; they completed a registration or booking form; they confirmed they would \
pay or asked how to pay; they asked to be invoiced to a specific entity or \
address; they nominated an accounts contact to receive it.

RULES.
1. Decide only from the evidence given. Never infer from the company name, the \
country, the amount, the event, or how plausible a booking sounds.
2. Weigh INBOUND mail from the speaker's side far above OUTBOUND mail from \
IQ-Hub. What IQ-Hub said proves nothing about whether the speaker asked.
3. A polite acknowledgement, an out-of-office, or a request for more time is \
NOT a request for an invoice. It is not evidence either way.
4. Quote verbatim. The quote field must be copied character for character from \
the evidence, and it must be the single line that most decides your verdict. If \
your verdict rests on the ABSENCE of any request, which is the usual case for a \
blind invoice, leave quote EMPTY and say so in the basis. Never quote a greeting, \
a sign-off or an unrelated line merely to fill the field; an empty quote is \
honest, a meaningless one is worse than none.
5. If the evidence does not decide it, set insufficient_evidence true and give \
your best guess with a low confidence. Saying so is a correct answer; guessing \
confidently is not.
6. Confidence is your probability that the verdict is right, from 0 to 1. \
Reserve above 0.9 for evidence that states the answer outright."""


def enabled() -> bool:
    """False when no key is configured. Callers skip and log once."""
    return bool((getattr(settings, "ANTHROPIC_API_KEY", "") or "").strip())


def _client():
    """
    The Anthropic client.

    Imported lazily so the module, and therefore the whole app registry, loads
    on a machine that has not installed the SDK. Credit Control's routing has to
    work without it; only classification does not.
    """
    import anthropic
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY.strip())


def _verdict_model():
    """
    The typed shape of an answer, built lazily for the same reason as _client.

    Literal on `verdict` is what makes an off-list answer impossible rather
    than merely unlikely.
    """
    from typing import Literal, Optional

    from pydantic import BaseModel, Field

    class InvoiceTypeVerdict(BaseModel):
        verdict: Literal["Blind Invoice", "Requested"]
        confidence: float = Field(ge=0.0, le=1.0)
        # NO max_length. It carried one, and a model that wrote 210 characters
        # of a perfectly good rationale failed validation and threw away the
        # whole verdict with it. A length limit belongs on the COLUMN, which
        # truncates on write, not on the schema, which rejects. The description
        # asks for brevity; nothing depends on getting it.
        basis: str = Field(description="One short sentence, under 200 characters, why.")
        quote: str = Field(
            default="",
            description="Verbatim from the evidence, the deciding line. Empty when "
                        "the verdict rests on the absence of any request.",
        )
        evidence_date: Optional[str] = Field(
            default=None, description="ISO date of the quoted message, if known.",
        )
        insufficient_evidence: bool = False

    return InvoiceTypeVerdict


# ── Evidence ────────────────────────────────────────────────────────────────

def contact_email_for(lead: CreditControlLead) -> str:
    """
    The address to look the contact up by.

    The accounts contact on the invoice first, because that is who credit
    control talks to, then the first delegate's address as the fallback. Same
    order the queue displays, so the phone number shown and the evidence read
    are about the same person.
    """
    invoice = lead.invoice
    if invoice.accounts_contact_email:
        return invoice.accounts_contact_email.strip().lower()
    if invoice.contact_email:
        return invoice.contact_email.strip().lower()
    first = invoice.delegates.order_by("delegate_number").values_list("email", flat=True).first()
    return (first or "").strip().lower()


def gather_evidence(lead: CreditControlLead) -> dict:
    """
    Everything known about this lead's contact, as one packet.

    Returns `emails` newest first and `text` as the flattened block the model
    reads and the quote is checked against. A packet with no contact is a valid
    and useful answer: it is the deterministic blind case.
    """
    email = contact_email_for(lead)
    packet = {"email": email, "contact": None, "emails": [], "text": ""}
    if not email or not hs.enabled():
        return packet

    cached = HubSpotContact.objects.filter(email=email).first()
    contact_id = cached.contact_id if cached and cached.contact_id else ""
    if not contact_id:
        try:
            found = hs.contacts_by_email([email])
        except hs.HubSpotError as exc:
            logger.info("credit_control: contact lookup failed for %s: %s", email, exc)
            return packet
        row = found.get(email)
        if not row:
            return packet
        contact_id = str(row.get("id") or "")
    if not contact_id:
        return packet
    packet["contact"] = contact_id

    ids = hs.email_ids_for_contact(contact_id, limit=constants.CLASSIFIER_EVIDENCE_EMAILS * 3)
    if not ids:
        return packet
    rows = hs.emails_by_id(ids)
    rows.sort(key=lambda r: hs.parse_ts(r.get("hs_timestamp")) or timezone.now(), reverse=True)
    rows = rows[:constants.CLASSIFIER_EVIDENCE_EMAILS]

    chunks = []
    for row in rows:
        when = hs.parse_ts(row.get("hs_timestamp"))
        body = (row.get("hs_email_text") or "").strip()
        body = " ".join(body.split())[:constants.CLASSIFIER_EVIDENCE_CHARS]
        item = {
            "id": row.get("id"),
            "date": when.date().isoformat() if when else "",
            "direction": row.get("hs_email_direction") or "",
            "subject": (row.get("hs_email_subject") or "").strip(),
            "body": body,
        }
        packet["emails"].append(item)
        chunks.append(
            f"[{item['date']}] {item['direction']} subject: {item['subject']}\n{item['body']}"
        )
    packet["text"] = "\n\n---\n\n".join(chunks)
    return packet


# ── The deterministic pre-pass ──────────────────────────────────────────────

def rule_verdict(lead: CreditControlLead, packet: dict) -> tuple[str, str] | None:
    """
    The verdict the rules can give for free, or None to ask the model.

    Returns (invoice_type, basis). Two rules fire here and both are certain
    rather than probable, which is why they never spend a call:

      * no HubSpot contact for the address at all. Nobody on that side ever
        interacted, so the invoice cannot have been requested.
      * a contact with no email history whatsoever. Same conclusion, one step
        weaker, and still not a judgement call.
    """
    if not packet.get("contact"):
        return constants.INVOICE_TYPE_BLIND, "no contact record in HubSpot"
    if not packet.get("emails"):
        return constants.INVOICE_TYPE_BLIND, "contact exists, no email history"
    return None


def quote_is_grounded(quote: str, evidence: str) -> bool:
    """
    Is the quoted line actually in the evidence we sent?

    Whitespace-insensitive, because the model may normalise spacing inside a
    line it copied faithfully, and that is not an invention. Anything shorter
    than a few characters is not a quote and is refused outright, which stops a
    single word passing the test by accident.
    """
    needle = " ".join((quote or "").split())
    if len(needle) < 8:
        return False
    return needle.lower() in " ".join((evidence or "").split()).lower()


# ── The call ────────────────────────────────────────────────────────────────

def classify(lead: CreditControlLead, packet: dict | None = None,
             *, dry_run: bool = False) -> dict:
    """
    Decide one lead's invoice type. Returns what it did.

    Never raises. Every outcome, including a failure, is written back with the
    attempt count and the error, so a row that cannot be classified is not
    retried on every run forever and the reason is visible in the UI.

    `dry_run` computes the verdict and writes NOTHING, which is what the
    --check scoring mode needs: it has to classify rows that already carry a
    human verdict without touching the very column it is scoring against.

    One save, at the end, built from a dict. The earlier shape saved at each of
    six exit points, which meant a dry run had six places to remember to skip.
    """
    outcome = {"invoice": lead.invoice_id, "action": "", "verdict": "", "detail": ""}
    packet = packet if packet is not None else gather_evidence(lead)

    updates = {
        "invoice_type_attempts": lead.invoice_type_attempts + 1,
        "invoice_type_at": timezone.now(),
        "invoice_type_prompt_version": constants.CLASSIFIER_PROMPT_VERSION,
        "invoice_type_error": "",
    }

    def finish(action: str, verdict: str = "", detail: str = "") -> dict:
        if not dry_run:
            for field, value in updates.items():
                setattr(lead, field, value)
            lead.save(update_fields=list(updates) + ["updated_at"])
        outcome.update(action=action, verdict=verdict, detail=detail)
        return outcome

    ruled = rule_verdict(lead, packet)
    if ruled:
        verdict, basis = ruled
        updates.update({
            "invoice_type": verdict,
            "invoice_type_basis": basis,
            "invoice_type_source": CreditControlLead.TypeSource.RULE,
            "invoice_type_confidence": 1.0,
            "invoice_type_quote": "",
        })
        return finish("rule", verdict, basis)

    if not enabled():
        updates["invoice_type_error"] = "ANTHROPIC_API_KEY is not set"
        return finish("skipped", "", updates["invoice_type_error"])

    facts = [
        f"Booking code: {lead.invoice.booking_code or 'unknown'}",
        f"Event: {lead.invoice.event_code or 'unknown'}",
        f"Company: {lead.invoice.company_name or 'unknown'}",
        f"Contact: {packet.get('email') or 'unknown'}",
        "",
        f"EVIDENCE, {len(packet['emails'])} most recent emails, newest first:",
        "",
        packet["text"],
    ]
    question = "\n".join(facts)

    try:
        response = _client().messages.parse(
            model=MODEL,
            max_tokens=2000,
            thinking={"type": "adaptive"},
            system=[{
                "type": "text",
                "text": RUBRIC,
                # The rubric is byte-identical on every call, so it is cached
                # and every row after the first reads it instead of paying for
                # it again. Nothing volatile may be added to it.
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": question}],
            output_format=_verdict_model(),
        )
        answer = response.parsed_output
    except Exception as exc:  # noqa: BLE001 - one row must never stop the pass
        updates["invoice_type_error"] = f"{type(exc).__name__}: {exc}"[:300]
        logger.warning("credit_control: classify failed for %s: %s", lead.invoice_id, exc)
        return finish("error", "", updates["invoice_type_error"])

    if answer is None:
        updates["invoice_type_error"] = "no parsed answer"
        return finish("error", "", updates["invoice_type_error"])

    # The two gates. Either one sends the row to a human instead of storing a
    # verdict, and which one fired is recorded so the UI can say why.
    # The grounding gate applies to a quote that EXISTS. A blind invoice is
    # usually proved by the absence of any request, and demanding a quote
    # regardless pushed the model into quoting a greeting to fill the field. A
    # gate a meaningless quote passes is theatre; an empty quote now rests on
    # the confidence floor alone, and a non-empty one still has to be real.
    if answer.quote and not quote_is_grounded(answer.quote, packet["text"]):
        updates["invoice_type_error"] = "quote not found in evidence, sent for review"
        updates["invoice_type_confidence"] = answer.confidence
        return finish("ungrounded", answer.verdict, updates["invoice_type_error"])

    if answer.insufficient_evidence or answer.confidence < constants.CLASSIFIER_MIN_CONFIDENCE:
        updates["invoice_type_error"] = (
            f"confidence {answer.confidence:.2f} below "
            f"{constants.CLASSIFIER_MIN_CONFIDENCE}, sent for review"
        )
        updates["invoice_type_confidence"] = answer.confidence
        updates["invoice_type_quote"] = answer.quote[:2000]
        return finish("unsure", answer.verdict, updates["invoice_type_error"])

    updates.update({
        "invoice_type": answer.verdict,
        "invoice_type_basis": (answer.basis or "")[:200],
        "invoice_type_quote": (answer.quote or "")[:2000],
        "invoice_type_confidence": answer.confidence,
        "invoice_type_source": CreditControlLead.TypeSource.MODEL,
        "invoice_type_model": MODEL,
        "invoice_type_evidence_date": _as_date(answer.evidence_date),
    })
    return finish("classified", answer.verdict, updates["invoice_type_basis"])


def _as_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def candidates(limit: int = constants.CLASSIFIER_MAX_PER_RUN):
    """
    The speaker leads worth classifying now, neediest first.

    ACTIVE LEADS ONLY. A not-invoiced or closed speaker is not being chased, so
    classifying it spends a call on a question nobody is asking; it becomes a
    candidate on the run after it enters the queue, which is the moment the
    answer is worth having. This cut the first run here from 195 rows to 123.

    Also excluded: anything a human has ruled on, anything already decided by
    the current rubric version, and anything that has failed three times, which
    is a row with a real problem rather than a row that needs another try.
    """
    from django.db.models import Q

    rows = (
        CreditControlLead.objects
        .select_related("invoice")
        .filter(bucket=CreditControlLead.Bucket.ACTIVE)
        .filter(invoice_type_override="")
        .filter(invoice_type_attempts__lt=3)
        .filter(
            Q(invoice_type="")
            | ~Q(invoice_type_prompt_version=constants.CLASSIFIER_PROMPT_VERSION)
        )
        .order_by("invoice_type_attempts", "-invoice__invoice_date")
    )
    picked = []
    for lead in rows.iterator():
        code = lead.invoice.booking_code
        if is_spex(code) or not is_speaker(code):
            continue
        picked.append(lead)
        if len(picked) >= limit:
            break
    return picked


def classify_pending(limit: int = constants.CLASSIFIER_MAX_PER_RUN) -> dict:
    """Classify up to `limit` speaker leads. Returns a tally per outcome."""
    tally = {"considered": 0, "rule": 0, "classified": 0, "unsure": 0,
             "ungrounded": 0, "error": 0, "skipped": 0}
    rows = candidates(limit)
    tally["considered"] = len(rows)
    for lead in rows:
        result = classify(lead)
        tally[result["action"]] = tally.get(result["action"], 0) + 1
    # So the dashboard can say when this last ran, not only when it next will.
    from .engine import _record_run

    _record_run("credit_control_classifier",
                tally.get("classified", 0) + tally.get("rule", 0))
    return tally


# ── The handoff brief ───────────────────────────────────────────────────────

BRIEF_SYSTEM = """You write a two or three sentence handover note for a credit \
control caller who is about to phone a company about an unpaid invoice, and who \
has never spoken to them before. A colleague worked the case first.

Write only what the next caller needs in order to open the call well: what has \
been tried, what the contact said, and what was agreed or refused. Use plain \
past tense. Name no colleague. Do not repeat the invoice number, the amount or \
the company name, which are already on the screen in front of them. Do not \
advise, instruct or speculate. If there is nothing substantive on the record, \
say exactly: No contact made yet."""


def write_brief(lead: CreditControlLead) -> str:
    """
    Two or three lines of what happened so far, for whoever picks this up next.

    Written at the moment a lead changes hands, not on a schedule, so it costs
    one call per handoff rather than one per lead per day. The remarks and the
    disposition travel with the invoice regardless; this is the part that turns
    a list of terse notes into something a caller can open a conversation from.

    Returns the brief, or "" when it could not be written. Never raises.
    """
    if not enabled():
        return ""
    touches = list(
        lead.touches.exclude(remark="").order_by("-created_at")[:8]
    )
    history = "\n".join(
        f"[{t.created_at:%d %b}] {t.disposition or 'no disposition'}: {t.remark}"
        for t in reversed(touches)
    )
    if not history.strip():
        history = f"{lead.disposition or 'No disposition'}: {lead.remark or 'nothing logged'}"

    try:
        response = _client().messages.create(
            model=MODEL,
            max_tokens=400,
            thinking={"type": "adaptive"},
            system=[{
                "type": "text",
                "text": BRIEF_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": f"The record so far:\n\n{history}"}],
        )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        ).strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("credit_control: brief failed for %s: %s", lead.invoice_id, exc)
        return ""

    if text:
        CreditControlLead.objects.filter(pk=lead.pk).update(
            handoff_brief=text[:2000], handoff_brief_at=timezone.now(),
        )
    return text


def write_pending_briefs(limit: int = 40) -> dict:
    """
    Write briefs for leads handed over since the last run.

    A lead qualifies when it has been handed off and has no brief yet. Nothing
    re-writes an existing brief, because the handover already happened and the
    note describes that moment.
    """
    tally = {"considered": 0, "written": 0, "empty": 0}
    rows = list(
        CreditControlLead.objects
        .filter(bucket=CreditControlLead.Bucket.ACTIVE)
        .filter(handed_off_at__isnull=False, handoff_brief="")
        .select_related("invoice")
        .order_by("-handed_off_at")[:limit]
    )
    tally["considered"] = len(rows)
    for lead in rows:
        if write_brief(lead):
            tally["written"] += 1
        else:
            tally["empty"] += 1
    return tally
