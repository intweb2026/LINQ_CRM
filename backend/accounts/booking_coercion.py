"""
accounts/booking_coercion.py
─────────────────────────────
ONE typed coercion table for every path that writes a booking.

WHY THIS FILE EXISTS
Booking columns are typed in the database and were not typed at any write
boundary. Six paths wrote bookings and each coerced values by its own rules, so a
value one path did not recognise was quietly replaced with a blank, with a
default, or left as whatever was stored before. Nothing was reported. The
26 August master import counted 15,180 rows inserted, returned no errors, and
lost or flattened seven columns.

Three of those rules met that one file at once.

  * `Payable` is the word the CRM displays for the stored value `Paid`
    (frontend/src/lib/constants.js PAID_OR_FREE_LABEL). No importer had ever
    accepted it, because every importer built its lookup from the model's two
    stored values alone. 11,205 rows lost their Payable/Free value.
  * A discount cell reading `20%` went through a bare `Decimal(...)` in a bare
    `try`, and the handler substituted `0.00`. 671 rows imported as no discount.
  * A value that was not recognised on an invoice that already existed left the
    stored value untouched, so the outcome depended on row order.

THE CONTRACT THIS TABLE DECLARES
  1. Allowed values are read FROM THE MODEL, so this cannot drift from the
     schema. Adding a choice makes it importable in the same commit.
  2. Input spellings the UI shows, or that our own exports use, are declared as
     ALIASES against the stored value. This is the only place they live.
  3. A cell that has content and cannot be read is an ERROR naming the column and
     the value. It is never a blank, never a default and never a silent skip.
  4. A genuinely blank cell is a blank. Where the column is nullable that is
     None, where it is a blank CharField that is "", and where the model declares
     its own default the rule returns UNSET so the write path leaves the column
     alone — which also means a blank cell in an upsert never overwrites a stored
     value with a default.

`book_event/tests_import_coercion.py` asserts every constrained column on
BookEvent and BookDelegate has an entry here, so a new choice-validated column
cannot be added without deciding how it is coerced.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Any, Mapping

from accounts.import_common import parse_import_date

# "The file said nothing about this column." The write path omits the key
# entirely, so the model's own default applies on create and a stored value
# survives on upsert. Distinct from "" and from None, both of which are values a
# file can legitimately state.
UNSET = object()

# Text that means "empty cell" rather than a value. Mirrors the set
# import_common uses for dates, minus "0" — a zero is a real value for
# delegate_count and for discount, and reading it as a blank is the F6 defect in
# another costume.
_BLANK_TEXT = frozenset(("nan", "nat", "none", "null", "n/a", "na", "-", "--"))


def _text(raw: Any) -> str:
    """Any cell as a stripped, single-spaced string; "" when blank."""
    if raw is None:
        return ""
    if isinstance(raw, bool):
        # bool before the numeric branch: bool is an int subclass, and a
        # spreadsheet's TRUE/FALSE must read as the words, not as 1/0.
        return "true" if raw else "false"
    if isinstance(raw, float):
        if raw != raw:                       # NaN, which equals nothing
            return ""
        if raw.is_integer():
            raw = int(raw)
    # A non-breaking space is what a copy-paste out of a browser table leaves
    # behind, and str.strip() does not remove it.
    return " ".join(str(raw).replace(" ", " ").split())


def _is_blank(text: str) -> bool:
    return not text or text.lower() in _BLANK_TEXT


@dataclass(frozen=True)
class Rule:
    """
    How one column is read from a file.

    `label`   what the column is called on screen, so an error names what the
              person mapped rather than the database column.
    `kind`    choice | fraction | int | date | text
    `model`   ("BookEvent", "PaidOrFree") — the TextChoices class the allowed
              values are read from, never a hand-kept list.
    `aliases` extra input spellings, lowered, mapped onto the stored value.
    `blank`   what a genuinely blank cell means for this column.
    """
    label:    str
    kind:     str
    model:    tuple[str, str] | None = None
    aliases:  Mapping[str, str] = dc_field(default_factory=dict)
    blank:    Any = UNSET
    min_value: int | None = None
    max_value: int | None = None


# ── Alias vocabularies ──────────────────────────────────────────────────────
#
# DELIBERATELY NARROW. Every entry below is a spelling this system itself emits
# or displays, or an abbreviation already in use in the sheets we import. An
# alias that GUESSES turns a reported error back into a silent mis-mapping, which
# is the defect this file exists to end — so a value nobody has actually seen in
# a file does not get an entry, it gets an error the first time it appears.

# `Payable` is the single most important entry in this file. It is the word the
# Bookings table, the booking modal and the bulk editor all show for the stored
# value `Paid`; the relabelling was never carried back to the import side. The
# rest match the vocabulary the repair command already accepts — see
# book_event/management/commands/update_delegate_number_paid_free.py.
PAID_OR_FREE_ALIASES = {
    "payable":        "Paid",
    "pay":            "Paid",
    "chargeable":     "Paid",
    "complimentary":  "Free",
    "comp":           "Free",
    "free of charge": "Free",
    "foc":            "Free",
}

PAYMENT_STATUS_ALIASES = {
    "free of charge": "Free",
    "foc":            "Free",
    # The display spellings of the four credit/transfer states, which read with
    # the bracket in some exports and without it in others.
    "credit pending free": "Credit Pending (Free)",
    "credit pending paid": "Credit Pending (Paid)",
    "paid transferred":    "Paid (Transferred)",
}

# Attendance arrives as an "Attendance - IN?" flag in the sheets we import, whose
# vocabulary is true/false. `true` was already recognised; `false` matched
# nothing and fell through to the Pending default, so 13,481 rows reached a
# defensible end state by accident rather than by translation — and the same
# fallback silently absorbed No and Absent, which do NOT mean Pending. Both
# vocabularies are now declared, and the two that mean No-show say so.
ATTENDANCE_ALIASES = {
    "true":  "Confirmed",
    "yes":   "Confirmed",
    "y":     "Confirmed",
    "1":     "Confirmed",
    "in":    "Confirmed",
    "attended": "Confirmed",
    # Not marked in. Pending is where the old fallback landed these and it is
    # the right reading of an unticked flag: nothing is known yet.
    "false": "Pending",
    "no":    "Pending",
    "n":     "Pending",
    "0":     "Pending",
    "not attended": "Pending",
    # Marked as having failed to appear, which is a different fact from "not yet
    # known" and used to be flattened onto it.
    "absent":  "No-show",
    "noshow":  "No-show",
    "no show": "No-show",
    "did not attend": "No-show",
    "dna":     "No-show",
    "cancel":    "Cancelled",
    "cancelled": "Cancelled",
    "canceled":  "Cancelled",
}


# ── The table ───────────────────────────────────────────────────────────────
#
# INVOICE-LEVEL and PERSON-LEVEL columns are one table on purpose: the same cell
# in the same file feeds both, so reading it two different ways is exactly the
# bug. book_event/views.py writes the person-level column from the row and lets
# the invoice follow when every delegate agrees.

RULES: dict[str, Rule] = {
    "payment_status": Rule(
        label="Payment Status", kind="choice",
        model=("BookEvent", "PaymentStatus"),
        aliases=PAYMENT_STATUS_ALIASES,
        # UNSET, not a value. The model's default is now blank, so a blank cell
        # reaches the same place on create WITHOUT this rule claiming the file
        # said so — and on upsert it leaves the stored value alone instead of
        # resetting a paid booking.
        blank=UNSET,
    ),
    "paid_or_free": Rule(
        label="Payable / Free", kind="choice",
        model=("BookEvent", "PaidOrFree"),
        aliases=PAID_OR_FREE_ALIASES,
        # blank=True, default="" on the model, and a blank cell here genuinely
        # means "not stated" rather than "charged" — which is what the webhook's
        # hard-coded Paid fallback got wrong.
        blank="",
    ),
    "payment_type": Rule(
        label="Payment Type", kind="choice",
        model=("BookEvent", "PaymentType"),
        blank="",
    ),
    "ticket_tier": Rule(
        label="Ticket Tier", kind="choice",
        model=("BookEvent", "TicketTier"),
        blank="",
    ),
    "currency": Rule(
        label="Currency", kind="choice",
        model=("BookEvent", "Currency"),
        # UNSET, not USD. The column is non-null with a declared default of USD,
        # so a file that carries no Currency still stores USD; the difference is
        # that a file carrying "Dollars" now ERRORS instead of being read as USD.
        blank=UNSET,
    ),
    "attendance": Rule(
        label="Attendance", kind="choice",
        model=("BookDelegate", "Attendance"),
        aliases=ATTENDANCE_ALIASES,
        blank=UNSET,
    ),
    "discount": Rule(
        label="Discount", kind="fraction",
        blank=UNSET,
    ),
    "delegate_count": Rule(
        label="Delegate Count", kind="int",
        # min_value=0, NOT 1. The importer applied max(1, int(...)) and rewrote
        # 4,636 zeros as ones. Whatever a zero means in the source it does not
        # mean one, and nothing recorded the change.
        #
        # Bounded 0-1 because this cell is a PER-PERSON FLAG, which is what
        # BookDelegate.delegate_count declares — choices [(0, "0"), (1, "1")],
        # "strictly 0 or 1" — and it is the column the Bookings table shows.
        # BookEvent.delegate_count is a different fact, "how many delegates are
        # on this invoice", and is DERIVED from the rows rather than imported;
        # book_delegate/views.py labels it "Delegate Count (invoice)" and the
        # website intake has always set it from len(delegates). Importing a
        # person's flag into the invoice's total is the F3 defect in miniature.
        min_value=0, max_value=1,
        blank=UNSET,
    ),
    # Delegated to import_common.parse_edition rather than given int bounds
    # here, because that function is already the authority on this column: it
    # expands a two-digit year the way BookEvent.save() does, and it names an
    # Excel serial for what it is. Two rules for one column is how the drift
    # this file ends got started.
    "edition": Rule(label="Edition", kind="edition", blank=UNSET),
    "payment_date": Rule(label="Payment Date", kind="date", blank=None),
    "request_date": Rule(label="Request Date", kind="date", blank=None),
    "invoice_date": Rule(label="Invoice Date", kind="date", blank=None),
    "created_at":   Rule(label="Added Time",   kind="date", blank=None),
    "booking_code": Rule(label="Booking Code", kind="text", blank=""),
}


@lru_cache(maxsize=1)
def _choices() -> dict[str, dict[str, str]]:
    """
    field -> {lowered accepted spelling: stored value}, built from the models.

    Imported inside the function, not at module scope: book_event and
    book_delegate are loaded by the app registry and accounts is imported by
    both, so a top-level import here is circular. Cached because the answer
    cannot change inside a process.
    """
    from book_delegate.models import BookDelegate
    from book_event.models import BookEvent

    owners = {"BookEvent": BookEvent, "BookDelegate": BookDelegate}
    out: dict[str, dict[str, str]] = {}
    for name, rule in RULES.items():
        if rule.kind != "choice" or rule.model is None:
            continue
        owner, enum_name = rule.model
        values = getattr(owners[owner], enum_name).values
        lookup = {str(v).lower(): v for v in values}
        # Aliases are applied AFTER the stored values, so an alias can never
        # shadow a real choice by accident.
        for spelling, stored in rule.aliases.items():
            if stored not in values:
                raise ValueError(
                    f"booking_coercion: alias {spelling!r} for {name} maps to "
                    f"{stored!r}, which {owner}.{enum_name} does not declare"
                )
            lookup.setdefault(spelling.lower(), stored)
        out[name] = lookup
    return out


def allowed_values(field: str) -> list[str]:
    """The stored values `field` accepts, for an error message or a dropdown."""
    return sorted(set(_choices().get(field, {}).values()))


def percent_to_fraction(raw: Any) -> tuple[Decimal | None, str | None]:
    """
    A discount cell as the FRACTION the database stores. Returns (value, error).

    Both vocabularies for the same fact are accepted, because the 26 August file
    mixed them in one column: "20%" and "0.2" both mean 0.2. The browser has
    applied exactly this rule to the same field since the booking form was
    fixed — frontend/src/api/bookings.js percentToFraction — and the import path
    simply never called it. 671 rows carrying "20%" hit a bare
    `except Exception: Decimal("0.00")` and imported as no discount at all,
    while the 262 rows spelled "0.2" imported correctly.

    A bare number is read as a FRACTION when it is 0-1 and as a PERCENT above 1,
    which is unambiguous for this column: every non-zero value in the export is
    one of 0.1/0.25/0.3/0.5, and a discount of 2000% is not a thing anyone means
    by "20".
    """
    text = _text(raw)
    if _is_blank(text):
        return None, None
    had_sign = text.endswith("%")
    if had_sign:
        text = text[:-1].strip()
    text = text.replace(",", "")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None, (
            f"{raw!r} is not a discount; write it as 20% or as 0.2"
        )
    if had_sign or number > 1:
        number = number / Decimal(100)
    if number < 0 or number > 1:
        return None, (
            f"{raw!r} is not a discount between 0% and 100%"
        )
    # Four places, matching the browser's rounding so the same cell typed into
    # the booking form and imported from a file store the same value.
    return number.quantize(Decimal("0.0001")), None


def coerce(field: str, raw: Any) -> tuple[Any, str | None]:
    """
    One cell, as the value to store. Returns (value, error); error is None on
    success and the value is meaningless when error is set.

    The value may be the UNSET sentinel, which means "omit this column from the
    write". Callers must test `is UNSET` before building their payload.
    """
    rule = RULES.get(field)
    if rule is None:
        # Not a constrained column — text, and the caller's own cleaning applies.
        return _text(raw), None

    text = _text(raw)

    if rule.kind == "date":
        value, error = parse_import_date(raw)
        if error:
            return None, f"{rule.label}: {error}"
        if value is None:
            return rule.blank, None
        return value, None

    if rule.kind == "fraction":
        value, error = percent_to_fraction(raw)
        if error:
            return None, f"{rule.label}: {error}"
        if value is None:
            return rule.blank, None
        return value, None

    if rule.kind == "edition":
        from accounts.import_common import parse_edition
        value, error = parse_edition(raw)
        if error:
            return None, f"{rule.label}: {error}"
        if value is None:
            return rule.blank, None
        return value, None

    if _is_blank(text):
        return rule.blank, None

    if rule.kind == "text":
        return text, None

    if rule.kind == "int":
        try:
            # float() first so "1.0" and a numeric cell both read, then int().
            # A fractional count is not silently truncated: 1.5 is rejected.
            number = float(text.replace(",", ""))
        except ValueError:
            return None, f"{rule.label}: {raw!r} is not a whole number"
        if number != int(number):
            return None, f"{rule.label}: {raw!r} is not a whole number"
        number = int(number)
        if rule.min_value is not None and number < rule.min_value:
            return None, (
                f"{rule.label}: {raw!r} is below {rule.min_value}"
            )
        if rule.max_value is not None and number > rule.max_value:
            return None, (
                f"{rule.label}: {raw!r} is above {rule.max_value}"
                + (
                    " — an Excel serial in a date-formatted column looks like this"
                    if field == "edition" else ""
                )
            )
        return number, None

    if rule.kind == "choice":
        stored = _choices()[field].get(text.lower())
        if stored is None:
            return None, (
                f"{rule.label}: {raw!r} is not a value this column stores. "
                f"Accepted: {', '.join(allowed_values(field))}"
            )
        return stored, None

    raise ValueError(f"booking_coercion: unknown kind {rule.kind!r} for {field}")


def coerce_row(row: Mapping[str, Any], fields=None) -> tuple[dict[str, Any], list[str]]:
    """
    Every constrained column in one row. Returns (values, errors).

    `values` holds only the keys the row actually stated — a column whose rule
    returned UNSET is absent, so a caller can `**values` into create() or apply
    them to an existing instance without a blank cell overwriting anything.

    `errors` is every problem in the row, not the first one. A row is reported
    once with all of its bad cells named, because fixing a spreadsheet one error
    per run is not a workflow anybody completes.
    """
    keys = fields if fields is not None else [k for k in RULES if k in row]
    values: dict[str, Any] = {}
    errors: list[str] = []
    for key in keys:
        if key not in row:
            continue
        value, error = coerce(key, row.get(key))
        if error:
            errors.append(error)
            continue
        if value is UNSET:
            continue
        values[key] = value
    return values, errors


# ── Dry-run counting ────────────────────────────────────────────────────────

def column_report(rows, fields=None) -> list[dict]:
    """
    Per-column counts of what a write WOULD do, for a dry run.

    Returns one entry per constrained column present in the file, each holding
    the number of cells accepted, left blank and rejected, plus up to five
    example rejected values. Fix 7's whole point: on the 26 August file this
    would have read "Payable / Free, 11,210 of 15,180 values not recognised"
    while the import could still be abandoned.

    Nothing here touches the database, and a caller must not need a transaction
    to ask the question — see tests asserting the preview writes nothing.
    """
    present = [
        key for key in (fields if fields is not None else RULES)
        if key in RULES and any(key in row for row in rows)
    ]
    report = []
    for key in present:
        rule = RULES[key]
        accepted = blank = 0
        rejected: dict[str, int] = {}
        for row in rows:
            if key not in row:
                blank += 1
                continue
            value, error = coerce(key, row.get(key))
            if error:
                shown = _text(row.get(key)) or repr(row.get(key))
                rejected[shown] = rejected.get(shown, 0) + 1
            elif value is UNSET or value in ("", None):
                blank += 1
            else:
                accepted += 1
        report.append({
            "field":    key,
            "label":    rule.label,
            "accepted": accepted,
            "blank":    blank,
            "rejected": sum(rejected.values()),
            "examples": [
                {"value": v, "rows": n}
                for v, n in sorted(rejected.items(), key=lambda kv: -kv[1])[:5]
            ],
            "allowed": allowed_values(key) if rule.kind == "choice" else [],
        })
    return report
