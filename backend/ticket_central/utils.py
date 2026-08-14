"""
ticket_central/utils.py
────────────────────────
Ticket number auto-generation + Smart Import row coercion.
"""
import logging
from datetime import date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.timezone import make_aware

from .models import Ticket
from .constants import DMD_WORK_FIELDS, MR_ACTIVITY_FIELDS

User = get_user_model()
logger = logging.getLogger(__name__)

# D25: allowlist derived from the model — prevents unknown column names from
# crashing Ticket.objects.create(**coerced) with TypeError.
_AUTO_FIELDS = frozenset({"id", "created_at", "updated_at"})
_WRITABLE_FIELDS = frozenset(
    f.name for f in Ticket._meta.get_fields()
    if hasattr(f, "name") and not f.auto_created
) - _AUTO_FIELDS
_INTERNAL_KEYS = frozenset({"_preserved_created_at", "_modified_time"})

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


def extract_purpose_code(purpose):
    """Strip whitespace, return canonical form."""
    if not purpose:
        return ""
    return str(purpose).strip()


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
    Returns the next ticket number for this purpose, reusing gaps left by
    deleted tickets before advancing past last_number.

    Algorithm:
      1. Lock the TicketSequence row for this purpose.
      2. Collect every number currently in use across ALL tickets for this purpose
         (regardless of type_code — the sequence is per-purpose).
      3. Scan [min_used .. last_number] for the first missing slot (gap).
      4. If a gap exists, reuse it.  Otherwise use last_number + 1.
      5. Only update last_number when we advance beyond it.

    Thread-safe: select_for_update ensures two concurrent creates for the same
    purpose cannot pick the same number.
    """
    from django.db import transaction
    from .models import TicketSequence

    with transaction.atomic():
        seq, _ = TicketSequence.objects.select_for_update().get_or_create(
            purpose_key=purpose_code,
            defaults={"last_number": 10000},
        )

        # All numbers currently occupied for this purpose (any type_code).
        used = set()
        for tn in (
            Ticket.objects
            .filter(purpose=purpose_code, ticket_number__gt="")
            .values_list("ticket_number", flat=True)
        ):
            try:
                used.add(int(tn.split(" ")[-1]))
            except (ValueError, IndexError):
                pass

        if used:
            lo = min(used)
            # First missing slot in [lo, last_number]; fall back to last_number+1.
            next_num = next(
                (n for n in range(lo, seq.last_number + 1) if n not in used),
                seq.last_number + 1,
            )
        else:
            next_num = (seq.last_number or 10000) + 1

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

    # D15: preserve Added Time / created_at if provided
    created_at_val = row.get("created_at") or row.get("Added Time")
    if created_at_val:
        parsed_cat = _parse_datetime(created_at_val)
        if parsed_cat:
            out["_preserved_created_at"] = parsed_cat

    # NEW: preserve Modified Time (read but don't write directly — used for audit derivation)
    if "modified_time" in row or "Modified Time" in row:
        mt = row.get("modified_time") or row.get("Modified Time")
        parsed_mt = _parse_datetime(mt)
        if parsed_mt:
            out["_modified_time"] = parsed_mt

    # NEW: status inference (D20)
    if "status" not in out:  # only auto-infer if not explicitly provided in import
        out["status"] = infer_status_from_row(out)

    # NEW: audit timestamps (D22)
    audit = derive_audit_timestamps(out, out["status"])
    out.update(audit)

    # Strip private keys before returning (these aren't model fields)
    out.pop("_modified_time", None)

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
