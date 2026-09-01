"""
ticket_central/utils.py
────────────────────────
Ticket number auto-generation + Smart Import row coercion.
"""
import logging
import re as _re
from datetime import date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.timezone import make_aware

from .models import Ticket
from .constants import (
    DMD_FIELDS, DMD_WORK_FIELDS, MR_ACTIVITY_FIELDS, MR_FIELDS, SHARED_FIELDS,
)

User = get_user_model()
logger = logging.getLogger(__name__)


def display_name(user):
    """
    How a user is named in a payload or a text column. Returns None for no user.

    Lives here rather than in serializers.py because the importer needs it too,
    and serializers imports it under its old private name.
    """
    if not user:
        return None
    return user.get_full_name() or user.username

# D25: allowlist derived from the model — prevents unknown column names from
# crashing Ticket.objects.create(**coerced) with TypeError.
_AUTO_FIELDS = frozenset({"id", "created_at", "updated_at"})
_WRITABLE_FIELDS = frozenset(
    f.name for f in Ticket._meta.get_fields()
    if hasattr(f, "name") and not f.auto_created
) - _AUTO_FIELDS
_INTERNAL_KEYS = frozenset({"_preserved_created_at", "_modified_time"})
# Added Time / Modified Time are read out of the row by hand at the bottom of
# _coerce_row. Skipping them in the typing loop keeps a datetime string from being
# stored as text and then dropped by the _WRITABLE_FIELDS filter one warning per
# row, which is what the loop did with created_at before.
_TIMESTAMP_KEYS = frozenset({
    "created_at", "Added Time", "updated_at", "Modified Time", "modified_time",
})

# Fields _coerce_row accepts but that a spreadsheet must not carry: they are set
# from the request user or by the workflow transitions, and letting an import
# name them would let a file rewrite who submitted what.
IMPORT_HIDDEN_FIELDS = frozenset({
    "created_by", "mr_submitted_by", "mr_submitted_at",
    "dmd_submitted_by", "dmd_submitted_at",
    "returned_by", "returned_at", "return_reason",
})


def import_fields():
    """
    [(key, label)] — every column Smart Import may map, derived from the model.

    WHY DERIVED AND NOT WRITTEN OUT
    frontend/src/api/import.js listed 15 ticket fields by hand while _coerce_row
    accepts every writable column on the model — roughly forty. The twenty-five it
    omitted (the whole DMD result block, the LX-2 second pass, mined_count,
    complete_date, status, ticket_type, …) had nowhere to map to, so a Zoho export
    carrying them imported as an empty shell of a ticket and nothing said so.
    Reading the model means a column added to Ticket is mappable the day it exists.

    `created_at` is included deliberately: _coerce_row honours it through
    _preserved_created_at (D15), so an import can carry Zoho's "Added Time"
    instead of stamping everything with the moment of the upload.
    """
    fields = []
    for f in Ticket._meta.get_fields():
        if not hasattr(f, "name") or f.auto_created:
            continue
        if f.name in IMPORT_HIDDEN_FIELDS or f.name not in _WRITABLE_FIELDS:
            continue
        label = str(getattr(f, "verbose_name", f.name) or f.name).strip()
        fields.append((f.name, label[:1].upper() + label[1:]))
    # created_at is excluded from _WRITABLE_FIELDS as an auto field, so the loop
    # above skips it — but the importer DOES read it, through
    # _preserved_created_at. Appended once, under the name the Zoho export uses.
    fields.append(("created_at", "Added Time"))
    # Same story for updated_at: auto_now keeps it out of _WRITABLE_FIELDS, and it
    # is honoured through _modified_time.
    fields.append(("updated_at", "Modified Time"))
    return fields


# The TC-YYYYMMDD-XXXX generator that used to sit here was removed. It was
# reachable from nothing: ticket numbers come from assign_next_ticket_number()
# below, which builds them from the purpose and type codes and reuses gaps
# through TicketSequence. Keeping a second generator that mints numbers in a
# format the sequence table does not track is a live hazard, not dead weight —
# one call from a future importer and the two schemes are interleaved.

# ── Smart Import: row coercion ──────────────────────────────────────────────

# Field type registry — single source of truth for coercion
DATE_FIELDS = {
    "event_month_year", "assign_date", "complete_date",
    "hubspot_entry_date", "complete_date_lx2",
}
INTEGER_FIELDS = {
    "estimate", "actual_number", "new_contacts_created",
    "source_row_number", "mined_count", "actual_count_lx2",
}
# D4: assignee fields are CharField now — stored as raw Zoho text, no FK resolution.
USER_FK_FIELDS = set()
# D4: priority/relationship are free CharField now (Zoho values vary) — do NOT
# coerce them to a fixed choice set or unrecognized values would be dropped.
CHOICE_FIELDS = {
    "status": {
        "draft": "draft", "mr_submitted": "mr_submitted",
        "completed": "completed", "returned": "returned",
    },
}


def _parse_date(v):
    if v in (None, "", " "):
        return None
    # D27: Excel serial number (e.g. 44197.0 → 2021-01-01).
    # Covers rows where cellDates:true wasn't applied or cell format was "General".
    # Two-epoch formula accounts for the Lotus-1900 leap-year bug (fake Feb 29 = serial 60):
    #   serials 1–59  → epoch 1899-12-31 (Excel day 1 = 1900-01-01)
    #   serials 60+   → epoch 1899-12-30 (standard conversion, handles post-Feb-1900 correctly)
    if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < float(v) < 100000:
        try:
            serial = float(v)
            epoch = datetime(1899, 12, 31) if serial < 60 else datetime(1899, 12, 30)
            return (epoch + timedelta(days=serial)).date()
        except (OverflowError, ValueError):
            pass
    if isinstance(v, (date, datetime)):
        return v.date() if isinstance(v, datetime) else v
    s = str(v).strip().rstrip("\t").strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y",
    ):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_int(v):
    if v in (None, "", " "):
        return None
    try:
        if isinstance(v, float):
            return int(v)
        # Strip commas, handle float-like strings: "1,234" → 1234, "60.0" → 60
        s = str(v).replace(",", "").strip()
        return int(float(s)) if ("." in s) else int(s)
    except (ValueError, TypeError):
        return None


# ── Auto-generation helpers (mirror Zoho Deluge logic) ──────────────────────

def extract_type_code(type_of_ticket):
    """
    'Blue - BX' → 'BX', 'ZID' → 'ZID', '' → ''.
    Last segment after '-', or the whole string if there is no dash.
    """
    if not type_of_ticket:
        return ""
    s = str(type_of_ticket).strip()
    if "-" in s:
        return s.split("-")[-1].strip()
    return s


def normalize_purpose(purpose):
    """
    Storage form of `purpose`. Upper-cased and whitespace-collapsed, full length.

    Purpose is stored upper-case, not merely displayed that way. Webhook senders
    push lower-case codes, and a free-text column keyed by a counter turned
    "CCU", "ccu" and "CCU  " into three sequences that each restarted at 10001.
    Ticket.save() and _coerce_row both run this, so no write path stores a
    lower-case purpose.

    It cannot merge genuinely different text. Production holds "ODU b" next to
    "ODU", and those stay two purposes; guessing that a stray token is a typo
    would silently file tickets under the wrong code. A fixed purpose list is
    the only real fix for that.
    """
    if not purpose:
        return ""
    return " ".join(str(purpose).split()).upper()


def extract_purpose_code(purpose):
    """
    Sequence key and ticket-number middle, i.e. normalize_purpose truncated to
    50 to match TicketSequence.purpose_key. `purpose` is a 255-char column, so a
    longer one used to overflow the key and raise DataError at submit time.
    """
    return normalize_purpose(purpose)[:50]


def build_ticket_number(type_code, purpose_code, number):
    """Format: 'TYPE-PURPOSE NUMBER', or just 'PURPOSE NUMBER' if no type."""
    if not purpose_code:
        return ""  # cannot build without a purpose
    num_str = str(number)
    max_prefix_len = 50 - len(num_str) - 1
    prefix = f"{type_code}-{purpose_code}" if type_code else purpose_code
    if len(prefix) > max_prefix_len:
        prefix = prefix[:max_prefix_len]
    return f"{prefix} {num_str}"


def assign_next_ticket_number(purpose_code, type_code):
    """
    Returns the next ticket number for this purpose: one past the highest
    number already in use, never a gap.

    Gap reuse was removed. It scanned upward from the LOWEST number in use, and
    the Zoho import left purposes holding two disjoint ranges — FLE, for
    instance, has 5 and 21-24 sitting alongside 7041-7221. min() picked 5, the
    scan found 6 free, and every new FLE ticket came out numbered 6, 7, 8 while
    the live series sat at 7221. Deleted tickets now leave their number retired,
    which is what a ticket number should do anyway.

    Thread-safe: select_for_update ensures two concurrent creates for the same
    purpose cannot pick the same number.
    """
    from django.db import transaction
    from .models import TicketSequence

    # Normalised here too, not just in the callers. It is idempotent, and every
    # caller having to remember is how one unnormalised path reopens the split
    # counters this exists to prevent.
    purpose_code = extract_purpose_code(purpose_code)

    with transaction.atomic():
        seq, created = TicketSequence.objects.select_for_update().get_or_create(
            purpose_key=purpose_code,
            defaults={"last_number": 10000},
        )

        # All numbers currently occupied for this purpose (any type_code).
        used = set()
        for tn in (
            Ticket.objects
            .filter(purpose__iexact=purpose_code, ticket_number__gt="")
            .values_list("ticket_number", flat=True)
        ):
            try:
                used.add(int(tn.split(" ")[-1]))
            except (ValueError, IndexError):
                pass

        # The data wins over the default. A counter row that already existed is
        # real history and counts toward the high-water mark, because it also
        # remembers numbers whose tickets have since been deleted. A row created
        # just now carries only the 10000 default, and that must NOT out-rank
        # what the data holds: FLE tops out at 7221, so seeding from the default
        # would number the next one 10001 and abandon the live series. 10000 is
        # the starting point for a purpose with no history at all, nothing more.
        marks = set(used)
        if not created:
            marks.add(seq.last_number)
        next_num = (max(marks) if marks else 10000) + 1

        if next_num > seq.last_number:
            seq.last_number = next_num
            seq.save(update_fields=["last_number"])

        return build_ticket_number(type_code, purpose_code, next_num)


def _resolve_user(v):
    """Match by username (primary) or email (fallback). Returns user_id or None."""
    if not v:
        return None
    s = str(v).strip()
    if not s:
        return None
    user = (
        User.objects.filter(username__iexact=s).first()
        or User.objects.filter(email__iexact=s).first()
    )
    return user.id if user else None


def _parse_datetime(v):
    if v in (None, "", " "):
        return None
    if isinstance(v, datetime):
        return v if timezone.is_aware(v) else make_aware(v)
    if isinstance(v, date):
        dt = datetime.combine(v, datetime.min.time())
        return make_aware(dt)
    s = str(v).strip().rstrip("\t").strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d-%b-%Y %H:%M:%S",
        "%d-%b-%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
        "%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            return make_aware(dt)
        except ValueError:
            continue
    return None


def infer_status_from_row(coerced_row: dict) -> str:
    """
    Infer ticket status from which fields are populated.
    D20: DMD work present → completed; only MR fields → mr_submitted; else draft.
    """
    def has_value(field):
        v = coerced_row.get(field)
        if v in (None, ""):
            return False
        if isinstance(v, str) and not v.strip():
            return False
        return True

    if any(has_value(f) for f in DMD_WORK_FIELDS):
        return "completed"
    if any(has_value(f) for f in MR_ACTIVITY_FIELDS):
        return "mr_submitted"
    return "draft"


def derive_audit_timestamps(coerced_row: dict, status: str) -> dict:
    """
    Populate mr_submitted_at and dmd_submitted_at based on status.
    D22: use Added Time / Complete Date / Modified Time as best-effort sources.
    """
    from datetime import date as date_type, datetime
    out = {}

    def _to_aware_dt(v):
        """Coerce date / naive datetime to UTC-aware datetime."""
        if v is None:
            return None
        if isinstance(v, date_type) and not isinstance(v, datetime):
            # date-only → midnight UTC
            return make_aware(datetime.combine(v, datetime.min.time()))
        if isinstance(v, datetime):
            return v if timezone.is_aware(v) else make_aware(v)
        return v  # already a timezone-aware datetime (or None)

    if status in ("mr_submitted", "completed"):
        # mr_submitted_at: prefer the row's created_at (Added Time)
        out["mr_submitted_at"] = _to_aware_dt(
            coerced_row.get("_preserved_created_at")
            or coerced_row.get("created_at")
        )

    if status == "completed":
        # dmd_submitted_at: Complete Date if present, else Modified Time fallback
        out["dmd_submitted_at"] = _to_aware_dt(
            coerced_row.get("complete_date")
            or coerced_row.get("_modified_time")
            or coerced_row.get("_preserved_created_at")  # last resort
        )

    # D23: submitter user FKs stay NULL for migrated tickets
    # mr_submitted_by, dmd_submitted_by remain unset

    return out



def _coerce_row(row, exclude=None, request_user=None):
    """Convert a raw import row dict into model-field-typed values."""
    exclude = exclude or set()
    out = {}
    for key, val in row.items():
        if key in exclude:
            continue
        if val in (None, ""):
            continue   # let model defaults apply
        if key in _TIMESTAMP_KEYS:
            continue   # handled below, as _preserved_created_at / _modified_time
        if key in DATE_FIELDS:
            parsed = _parse_date(val)
            if parsed is not None:
                out[key] = parsed
        elif key in INTEGER_FIELDS:
            parsed = _parse_int(val)
            if parsed is not None:
                out[key] = parsed
        elif key in USER_FK_FIELDS:
            uid = _resolve_user(val)
            if uid is not None:
                out[key + "_id"] = uid
        elif key in CHOICE_FIELDS:
            s = str(val).strip().lower()
            mapped = CHOICE_FIELDS[key].get(s)
            if mapped:
                out[key] = mapped
        else:
            out[key] = str(val).strip()

    # The import's update branch writes this dict through queryset.update()
    # (views.py:585), which never calls Ticket.save(), so the model-level
    # normalisation does not run for it. Normalise here so both import branches
    # store the same upper-case form.
    if out.get("purpose"):
        out["purpose"] = normalize_purpose(out["purpose"])

    # D15: preserve Added Time / created_at if provided
    created_at_val = row.get("created_at") or row.get("Added Time")
    if created_at_val:
        parsed_cat = _parse_datetime(created_at_val)
        if parsed_cat:
            out["_preserved_created_at"] = parsed_cat

    # Preserve Modified Time. "updated_at" is the key Smart Import maps it under
    # (import_fields appends it); the other two are what a Zoho export labels it.
    mt = (row.get("updated_at") or row.get("modified_time")
          or row.get("Modified Time"))
    if mt:
        parsed_mt = _parse_datetime(mt)
        if parsed_mt:
            out["_modified_time"] = parsed_mt

    # NEW: status inference (D20)
    if "status" not in out:  # only auto-infer if not explicitly provided in import
        out["status"] = infer_status_from_row(out)

    # NEW: audit timestamps (D22)
    audit = derive_audit_timestamps(out, out["status"])
    out.update(audit)

    # _modified_time is NOT stripped here. updated_at is auto_now, and auto_now is
    # a save() hook, so it cannot be passed to Ticket.objects.create() — the
    # importer applies it with a queryset update afterwards, exactly as it does
    # _preserved_created_at. Dropping it here is what made every imported row read
    # "Modified Time = moment of the upload".
    # D25: filter to model-known fields — unknown CSV columns become warnings, not crashes.
    filtered = {}
    dropped = []
    for key, val in out.items():
        if key in _WRITABLE_FIELDS or key in _INTERNAL_KEYS:
            filtered[key] = val
        else:
            dropped.append(key)
    if dropped:
        logger.warning("_coerce_row dropped unknown keys: %s", dropped)
    return filtered


# ── Webhook field-name canonicalisation ──────────────────────────────────────
# DRF silently ignores a key it does not recognise, so a ticket posted with
# "Link_URL" (Zoho Creator transmits its own API names) or "Link URL" (the
# column label, which is what a hand-built integration copies) is accepted with
# a 201 and that field left empty — the delivery looks successful and the value
# is simply gone. Folding case, spaces, hyphens and underscores away makes every
# one of those spellings land on the field it obviously means.
_FOLD_RE = _re.compile(r"[^a-z0-9]+")


def _fold(name) -> str:
    return _FOLD_RE.sub("", str(name).lower())


_CANONICAL_FIELDS = {
    _fold(f): f
    # DMD fields are folded too, deliberately: a Data Mining field sent under a
    # loose spelling must still be REFUSED by the serializer's ownership check
    # rather than quietly dropped as an unknown key.
    for f in (MR_FIELDS | DMD_FIELDS | SHARED_FIELDS | {"external_id"})
}
# Spellings that do not fold to a field name on their own. Nothing else on a
# ticket is a link, so neither of these is ambiguous.
_CANONICAL_FIELDS.update({"link": "link_url", "url": "link_url"})


def canonicalise_ticket_fields(payload: dict) -> dict:
    """
    A webhook body with its keys renamed to the ticket field they name.

    Unrecognised keys are passed through untouched so the caller can report
    them; a blank duplicate never overwrites a value already resolved, since a
    sender that emits both "link_url" and "Link URL" usually fills only one.
    """
    if not isinstance(payload, dict):
        return {}
    out = {}
    for key, value in payload.items():
        canon = _CANONICAL_FIELDS.get(_fold(key), key)
        if canon in out and value in ("", None):
            continue
        out[canon] = value
    return out
