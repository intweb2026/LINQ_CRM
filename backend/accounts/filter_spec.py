"""
accounts/filter_spec.py
────────────────────────
Compound filter engine: N criteria, ANDed, each (field, operator, value(s)).

Sibling to BulkUpdateMixin and deliberately the same shape — declare a registry
on the ViewSet, get a schema endpoint plus a deny-by-default validator. Nothing
is filterable unless it is registered.

COEXISTENCE WITH THE EXISTING FilterSets
The spec is applied by overriding `filter_queryset`, calling super() FIRST:

    def filter_queryset(self, queryset):
        qs = super().filter_queryset(queryset)   # DjangoFilterBackend, Search, Ordering
        return self.apply_filter_spec(qs)

So DjangoFilterBackend, SearchFilter and OrderingFilter all run exactly as they
did — their WHERE clauses and ORDER BY are untouched — and the spec ANDs onto the
result. Pagination happens later in paginate_queryset and is unaffected, so
page/page_size/ordering and the frontend's infinite scroll keep working.

The queryset handed to filter_queryset is already self.get_queryset() output, so
RBAC scoping is inherited rather than re-implemented. This module never touches
Model.objects.

RESOLVED (person-level) FIELDS
On Bookings, payment_status and friends display the delegate override if one is
set, else the invoice's value. Filtering the raw override column would miss every
inheriting row. Those fields are annotated as

    COALESCE(NULLIF(<override>, ''), <invoice field>)

and the operators run against the annotation, so every operator — not just
equality — matches what the table actually shows.
"""
import json
import logging

from django.db.models import BooleanField, DateField, DateTimeField, Q, TextField, Value
from django.db.models.functions import Cast, Coalesce, NullIf
from rest_framework.decorators import action
from rest_framework.exceptions import ParseError
from rest_framework.response import Response

logger = logging.getLogger(__name__)

# ── Registry builder ─────────────────────────────────────────────────────────
# Django field class name -> our filter type. Anything unmapped is skipped, so
# an unrecognised field is silently non-filterable rather than wrongly typed.
_DJANGO_TYPE_MAP = {
    "CharField": "text",
    "TextField": "text",
    "EmailField": "text",
    "URLField": "text",
    "SlugField": "text",
    "BooleanField": "boolean",
    "IntegerField": "number",
    "PositiveIntegerField": "number",
    "PositiveSmallIntegerField": "number",
    "SmallIntegerField": "number",
    "BigIntegerField": "number",
    "FloatField": "number",
    "DecimalField": "number",
    "DateField": "date",
    "DateTimeField": "date",
    "ForeignKey": "user_fk",
}

# Never filterable on any module: surrogate keys, import provenance, and
# timestamps that carry no business meaning to a user building a filter.
DEFAULT_EXCLUDES = {
    "id", "created_at", "updated_at", "external_id", "idempotency_key",
    "source_spreadsheet_id", "source_tab", "source_row_number",
}


def _is_user_fk(field):
    """True when this ForeignKey points at the configured auth user model."""
    from django.contrib.auth import get_user_model
    try:
        return field.related_model is get_user_model()
    except Exception:                                    # noqa: BLE001
        return False


def active_user_choices():
    """
    [{value: <pk>, label: <name or email or username>}] for active users.

    Object-shaped choices, unlike the scalar lists that come off model enums —
    a user FK stores an id but must display a name. `_choice_values` normalises
    both shapes for validation.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()
    out = []
    for u in User.objects.filter(is_active=True).order_by("first_name", "username"):
        label = (f"{u.first_name} {u.last_name}".strip()
                 or getattr(u, "email", "") or u.get_username())
        out.append({"value": u.pk, "label": label})
    return out


def _choice_values(cfg):
    """Comparable values from either choice shape (scalars or {value,label})."""
    choices = cfg.get("choices")
    if not choices:
        return None
    if isinstance(choices[0], dict):
        return [c["value"] for c in choices]
    return list(choices)


def build_filter_spec_fields(model, exclude=(), extra=None, labels=None):
    """
    Derive a registry from the model's concrete fields, then subtract
    exclusions. Reverse relations and M2M are skipped — filtering across them
    can duplicate rows, and nothing in v1 needs it.
    """
    exclude = set(exclude) | DEFAULT_EXCLUDES
    labels = labels or {}
    out = {}

    for f in model._meta.get_fields():
        if not getattr(f, "concrete", False) or f.many_to_many or f.one_to_many:
            continue
        name = f.name
        if name in exclude:
            continue
        ftype = _DJANGO_TYPE_MAP.get(type(f).__name__)
        if ftype is None:
            continue

        cfg = {
            "type": ftype,
            "label": labels.get(name, name.replace("_", " ").title()),
            "nullable": bool(getattr(f, "null", False)),
        }
        # A DateTimeField is typed "date" here so it filters with the date
        # vocabulary, but a client that sends a bare date against one narrows to
        # MIDNIGHT and silently drops the rest of the day — the trap
        # period_filter.day_bounds() exists to document. Saying which columns
        # carry a time lets the caller send the right edge of the day instead of
        # guessing from the column's name.
        if type(f).__name__ == "DateTimeField":
            cfg["has_time"] = True
        choices = getattr(f, "choices", None)
        if choices:
            cfg["choices"] = [c[0] for c in choices]
        elif ftype == "user_fk":
            # Only USER foreign keys get a populated picker. `company` is also a
            # ForeignKey and lands in this type — the mapping is coarse — but it
            # points at 7,671 companies, so inlining them would bloat every
            # schema response. It is left without choices and the UI falls back
            # to raw id entry; a proper async lookup belongs to a later phase.
            # TODO: the type key "user_fk" is really "fk"; renaming is churn for
            # no behaviour change, so it is flagged rather than done here.
            if _is_user_fk(f):
                cfg["choices_source"] = "active_users"
        out[name] = cfg

    if extra:
        out.update(extra)
    return out

# ── Operator registry, by field type ─────────────────────────────────────────
# NOTE: number carries contains/not_contains, which the original brief listed
# only for text. It is required by the worked example ("delegate_count contains
# 0") and is implemented by casting the column to text. Flagged, not silent.
_LIST_OPS = {"any_of", "none_of"}
_NO_VALUE_OPS = {"is_empty", "is_not_empty"}
_PAIR_OPS = {"between", "not_between"}

# Operators whose operand is not required to be a member of the field's choice
# list: substring matches work on fragments, and ordinal comparisons take
# bounds that may sit outside the set of stored values.
_UNCONSTRAINED_VALUE_OPS = {
    "contains", "not_contains", "like",
    "gt", "gte", "lt", "lte", "between", "not_between", "before", "after",
}

# `like` is a SQL-LIKE pattern — % for any run of characters, _ for one — and it
# is here because the table OFFERS it. Without a backend form, picking "Is Like"
# in the filter panel dropped that condition to the browser and it narrowed the
# loaded page alone, which is indistinguishable from a working filter until the
# count is checked. Evaluated as a case-insensitive anchored regex, translated
# from the pattern by _like_regex below, so it means what the browser's likeTest
# means.
OPERATORS_BY_TYPE = {
    "text": [
        "is", "is_not", "contains", "not_contains", "starts_with", "ends_with",
        "like", "any_of", "none_of", "is_empty", "is_not_empty",
    ],
    # contains / not_contains on a CHOICE field are here because the table used
    # to default every column's filter to "Contains", so filters saved before
    # that changed still carry it — and without a backend form each of those
    # silently reverted to filtering the loaded page. A choice column is a
    # CharField underneath, so the substring match is the same one text gets;
    # _UNCONSTRAINED_VALUE_OPS already exempts both from the membership check,
    # since a fragment of a choice is not itself a choice.
    "choice": ["is", "is_not", "contains", "not_contains", "like",
               "any_of", "none_of", "is_empty", "is_not_empty"],
    # is_not is here so a boolean column answers "not ticked" as one criterion.
    # It was absent, so the table's "Is Not" fell back to the browser and
    # narrowed the loaded page alone. _q_for negates the plain equality for any
    # non-text type, so nothing else was needed.
    "boolean": ["is", "is_not", "is_empty", "is_not_empty"],
    # any_of / none_of are here for the same reason they are on text: the
    # table's "Is" filter accepts SEVERAL values, and multi-value Is maps onto
    # any_of. Without them a two-value filter on a numeric column — Delegate
    # Number is 0 or 1 — had no backend form at all and fell back to filtering
    # whichever page happened to be loaded. _q_for already builds them for
    # non-text types with plain equality, so nothing else was needed.
    "number": [
        "is", "is_not", "gt", "gte", "lt", "lte", "between",
        "any_of", "none_of",
        "contains", "not_contains", "like", "is_empty", "is_not_empty",
    ],
    # not_between is the only operator here without a plain-language twin in the
    # original vocabulary. It exists because "is not in this window" is
    # `before OR after`, and `match` is "all" — an AND — so the client cannot
    # assemble it from two criteria however it tries. Without it, the Advanced
    # Filter's "Is Not" over a date range would have to be evaluated in the browser
    # over whichever page happened to be loaded, and the row count underneath it
    # would then describe that page rather than the table.
    "date": ["is", "is_not", "before", "after", "between", "not_between",
             "is_empty", "is_not_empty"],
    "user_fk": ["is", "is_not", "any_of", "none_of", "is_empty", "is_not_empty"],
}

# Types whose column can meaningfully hold '' as well as NULL. Drives is_empty.
_TEXTISH = {"text", "choice"}


# POSIX ERE metacharacters. Deliberately NOT re.escape(): that also escapes
# '-', '&', '~', '#' and whitespace, and Postgres reads a backslash before a
# non-alphanumeric as the literal character, so those would still work — but it
# escapes them with sequences this file would then have to reason about per
# backend. Escaping exactly the metacharacters is the smaller claim.
_ERE_SPECIAL = set(".^$*+?()[]{}|" + chr(92))


def _like_regex(pattern):
    """
    A SQL-LIKE pattern as an anchored, case-insensitive regex.

    Mirrors likeTest in frontend/src/components/DataTable.jsx exactly — % is any
    run of characters, _ is exactly one, everything else is literal, and the
    whole value must match. The two evaluators must agree: the same filter is
    applied by the browser whenever a condition cannot travel.
    """
    out = ["^"]
    for ch in str(pattern):
        if ch == "%":
            out.append(".*")
        elif ch == "_":
            out.append(".")
        elif ch in _ERE_SPECIAL:
            out.append("\\" + ch)
        else:
            out.append(ch)
    out.append("$")
    return "".join(out)


class FilterSpecError(Exception):
    """Validation failure — carries the message returned to the caller."""


class FilterSpecMixin:
    """
    Mixin for ViewSets. Adds `filter_schema` and applies `?filter_spec=<json>`
    on the list endpoint.

    Field config shape:

        filter_spec_fields = {
            "purpose": {"type": "text", "label": "Purpose"},
            "status":  {"type": "choice", "label": "Status", "choices": [...]},
            "payment_status": {
                "type": "choice", "label": "Payment Status", "choices": [...],
                # person-level: override if set, else invoice
                "resolved": {"override": "delegate_payment_status",
                             "invoice": "invoice__payment_status"},
            },
            "company_name": {"type": "text", "label": "Company",
                             "source": "invoice__company_name"},
            # COMPUTED: no column holds this value, so the filter runs against
            # an annotation. Use it wherever the table displays something the
            # serializer builds — a full name, a related user's display name, a
            # unit conversion — because the alternative is not filtering at all,
            # and "not at all" degrades to filtering the loaded page.
            "name": {"type": "text", "label": "Name",
                     "expression": lambda: Trim(Concat(...))},
        }

    `expression` may be a callable, evaluated per request, so a registry can be
    declared at class-definition time. `source`, `resolved` and `expression` are
    three ways to name the same thing — where the value lives — and exactly one
    of them applies per field.
    """

    filter_spec_fields = {}
    filter_spec_max_criteria = 20

    #: Ceiling on one select-all. See the `ids` action for why it is a refusal
    #: rather than a truncation.
    select_all_max = 100_000

    # ── Schema ────────────────────────────────────────────────────────────────
    @action(detail=False, methods=["get"], url_path="filter_schema")
    def filter_schema(self, request):
        """
        GET {resource}/filter_schema/ — single source of truth so the frontend
        hardcodes neither fields nor operators. Same wrapper convention as
        bulk_update_schema.
        """
        fields = {}
        for key, cfg in self.get_filter_spec_fields().items():
            entry = {
                "type": cfg["type"],
                "label": cfg.get("label", key),
                "operators": self.allowed_operators(cfg),
                "nullable": bool(cfg.get("nullable", False)),
                "resolved": bool(cfg.get("resolved")),
                "empty_shape": self._empty_shape_name(cfg),
                "has_time": bool(cfg.get("has_time", False)),
            }
            if cfg.get("choices") is not None:
                entry["choices"] = list(cfg["choices"])
            fields[key] = entry

        return Response({
            "fields": fields,
            "operators_by_type": OPERATORS_BY_TYPE,
            "max_criteria": self.filter_spec_max_criteria,
            "match_modes": ["all"],
        })

    # ── Select all ────────────────────────────────────────────────────────────
    @action(detail=False, methods=["get"], url_path="ids")
    def ids(self, request):
        """
        GET {resource}/ids/ — every primary key the CURRENT filter matches.

        What the table's select-all checkbox is built on. It used to tick one
        page, so on a filter matching 35,690 tickets "select all" reached the 50
        rows on screen and a mass update silently touched 0.1% of what the user
        had asked for.

        SAME FILTER AS THE LIST, BY CONSTRUCTION
        The one thing that must never drift here is which rows this answers with:
        a select-all that resolves a wider set than the table shows hands bulk
        actions rows the user never saw. So this calls the very same
        filter_queryset(get_queryset()) pair the list endpoint does — RBAC
        scoping, DjangoFilterBackend, SearchFilter, the period window and the
        filter_spec, in that order — rather than re-deriving any of it. A
        criterion the list understands is a criterion this understands, for free.

        The period window is the one that needed a change: PeriodFilterMixin
        applies only to the actions in `period_actions`, which was ("list",)
        alone, so without adding this one a select-all inside a "Last 30 days"
        view would have quietly returned every row of all time.

        REFUSAL, NOT TRUNCATION
        Past select_all_max this answers 400 rather than returning the first N.
        A truncated select-all is indistinguishable from a complete one at the
        call site — the UI would report "all 100,000 selected" and the remainder
        would go silently unedited, which is the same class of bug as the
        one-page selection this replaces, just less visible.

        Read-only: no write, no transaction, and ORDER BY is dropped because the
        caller builds a Set out of the answer and sorting every matching row to
        feed it is pure cost.
        """
        qs = self.filter_queryset(self.get_queryset()).order_by()

        # Counted before the rows are materialised: the cap exists to bound
        # memory, and a check performed after pulling 5,000,000 ids into a list
        # would be enforcing it too late to matter.
        total = qs.count()
        if total > self.select_all_max:
            return Response(
                {
                    "detail": (
                        f"That filter matches {total:,} records, more than the "
                        f"{self.select_all_max:,} that can be selected at once. "
                        f"Narrow the filter and try again."
                    ),
                    "count": total,
                    "max": self.select_all_max,
                },
                status=400,
            )

        # dict.fromkeys, not set(): a filter that joins can repeat a row, and the
        # duplicate would inflate every count the user is shown ("13,264 of
        # 13,264 selected" over 12,900 distinct rows). Order is preserved so the
        # answer is stable between identical requests, which is what makes the
        # response diffable when one of these is ever in a bug report.
        matched = list(dict.fromkeys(qs.values_list("pk", flat=True)))
        return Response({"ids": matched, "count": len(matched), "max": self.select_all_max})

    # ── Registry access (overridable for per-request choices) ─────────────────
    def get_filter_spec_fields(self):
        """
        Resolves `choices_source` markers into real choice lists per request, so
        a user picker reflects who is active now rather than who was active at
        import time. Mirrors the dynamic assigned_mr choices on TicketViewSet.
        """
        fields = self.filter_spec_fields
        if not any(isinstance(c, dict) and c.get("choices_source") for c in fields.values()):
            return fields
        users = None
        resolved = {}
        for key, cfg in fields.items():
            if cfg.get("choices_source") == "active_users":
                if users is None:
                    users = active_user_choices()
                resolved[key] = {**cfg, "choices": users}
            else:
                resolved[key] = cfg
        return resolved

    @staticmethod
    def allowed_operators(cfg):
        """
        Per-FIELD operator list, not merely per-type. A NOT NULL boolean can
        never be empty, so offering is_empty there would be an operator that
        silently matches nothing.
        """
        ops = list(OPERATORS_BY_TYPE.get(cfg["type"], []))
        if cfg["type"] == "boolean" and not cfg.get("nullable"):
            ops = [o for o in ops if o not in _NO_VALUE_OPS]
        return ops

    # ── is_empty shape ────────────────────────────────────────────────────────
    @staticmethod
    def _empty_shape_name(cfg):
        """
        Three shapes, driven by the field definition rather than one generic rule:

          "resolved"       — override is unset AND the invoice value is unset;
                             evaluated on the COALESCE annotation.
          "null_or_blank"  — text-ish column: '' or NULL both count as empty.
          "null_only"      — date/number/boolean/fk: only NULL is empty; a ''
                             comparison would be a database type error.

        A RESOLVED DATE IS null_only, not resolved. `resolved` is not a third
        kind of emptiness, it is null_or_blank evaluated on the annotation
        instead of on a column, so it carries that shape's '' comparison with
        it — and against a date annotation that comparison is the same type
        error the null_only line describes. is_empty on a resolved date raised
        ValidationError("'' value has an invalid date format") for as long as
        both existed; payment_date has been resolved since the overrides went
        in, and it went unnoticed only because nothing asked that column
        whether it was empty until Request Date became resolved too.
        """
        if cfg.get("resolved"):
            return "resolved" if cfg["type"] in _TEXTISH else "null_only"
        if cfg["type"] in _TEXTISH:
            return "null_or_blank"
        return "null_only"

    def _empty_q(self, path, cfg):
        shape = self._empty_shape_name(cfg)
        if shape == "null_only":
            return Q(**{f"{path}__isnull": True})
        # resolved and null_or_blank share the predicate; for resolved, `path` is
        # the annotation, so NULL there already means "neither side had a value".
        # Only the text-ish ones reach here — see _empty_shape_name.
        return Q(**{f"{path}__isnull": True}) | Q(**{path: ""})

    # ── Path resolution + annotations ─────────────────────────────────────────
    def _annotation_name(self, key, cast=False):
        """
        Name of the annotation a criterion filters through.

        Two names, not one. A number field filtered with `contains` is compared
        as TEXT and needs a Cast; the same field filtered with `gt` in the same
        request must stay numeric. One name for both meant the second annotation
        silently replaced the first in `_prepare`'s dict, so a spec carrying
        "discount contains 2" AND "discount gt 0" compared one of them against
        the wrong type. The cast form gets its own name and the two coexist.
        """
        return f"_fs_{key}_txt" if cast else f"_fs_{key}"

    @staticmethod
    def _expression_for(cfg):
        """
        The declared `expression`, resolved.

        Accepts a callable so a registry can be written at class-definition time
        without evaluating ORM expressions at import; anything else is taken as
        the expression itself.
        """
        expr = cfg["expression"]
        return expr() if callable(expr) else expr

    def _resolved_expression(self, cfg):
        """COALESCE(NULLIF(override, ''), invoice) — '' on the override inherits."""
        override = cfg["resolved"]["override"]
        invoice = cfg["resolved"]["invoice"]
        if cfg["type"] == "date":
            # A DateField cannot hold '', so NULLIF would be a type error.
            return Coalesce(override, invoice)
        return Coalesce(NullIf(override, Value("")), invoice)

    def _prepare(self, queryset, criteria):
        """Attach annotations for any resolved, computed or text-cast field in play."""
        annotations = {}
        for c in criteria:
            key = c["field"]
            cfg = self.get_filter_spec_fields()[key]
            textual = cfg["type"] == "number" and c["op"] in ("contains", "not_contains", "like")
            if cfg.get("resolved"):
                base = self._resolved_expression(cfg)
            elif cfg.get("expression"):
                base = self._expression_for(cfg)
            else:
                base = None
            if base is not None:
                annotations[self._annotation_name(key)] = base
                if textual:
                    annotations[self._annotation_name(key, cast=True)] = Cast(base, TextField())
            elif textual:
                annotations[self._annotation_name(key, cast=True)] = Cast(
                    cfg.get("source", key), TextField())
        return queryset.annotate(**annotations) if annotations else queryset

    def _path_for(self, key, cfg, op):
        if cfg["type"] == "number" and op in ("contains", "not_contains", "like"):
            return self._annotation_name(key, cast=True)
        if cfg.get("resolved") or cfg.get("expression"):
            return self._annotation_name(key)
        return cfg.get("source", key)

    # ── Value coercion ────────────────────────────────────────────────────────
    _TRUE = frozenset(["true", "True", "1", 1, True])
    _FALSE = frozenset(["false", "False", "0", 0, False])

    def _coerce_value(self, value, cfg, where):
        """
        Normalise a submitted value to the type the ORM expects.

        Booleans are the case that matters. A <select> yields the STRING
        "false", and Django's BooleanField.to_python raises ValidationError on
        it — which escapes as a 500 rather than a clean 400. Worse, anywhere a
        string were accepted, "false" is truthy in Python and would mean True.
        """
        if cfg["type"] != "boolean":
            return value
        # `1 in {True}` is True in Python, so compare identity for bools first.
        if isinstance(value, bool):
            return value
        if value in self._TRUE:
            return True
        if value in self._FALSE:
            return False
        raise FilterSpecError(f"{where}: '{value}' is not a valid true/false value.")

    # ── Validation ────────────────────────────────────────────────────────────
    def _validate(self, spec):
        if not isinstance(spec, dict):
            raise FilterSpecError("filter_spec must be a JSON object.")

        match = spec.get("match", "all")
        if match != "all":
            raise FilterSpecError(
                f"match='{match}' is not supported. Only 'all' is accepted in this version."
            )

        criteria = spec.get("criteria", [])
        if not isinstance(criteria, list):
            raise FilterSpecError("criteria must be a list.")
        if len(criteria) > self.filter_spec_max_criteria:
            raise FilterSpecError(
                f"Too many criteria: {len(criteria)} (maximum {self.filter_spec_max_criteria})."
            )

        registry = self.get_filter_spec_fields()
        cleaned = []
        for i, c in enumerate(criteria):
            where = f"criterion {i + 1}"
            if not isinstance(c, dict):
                raise FilterSpecError(f"{where} must be an object.")

            key = c.get("field")
            cfg = registry.get(key)
            if cfg is None:
                raise FilterSpecError(f"{where}: field '{key}' is not filterable on this resource.")

            op = c.get("op")
            allowed = self.allowed_operators(cfg)
            if op not in allowed:
                raise FilterSpecError(
                    f"{where}: operator '{op}' is not valid for a {cfg['type']} field. "
                    f"Allowed: {', '.join(allowed)}."
                )

            values = self._validate_arity(where, c, op, cfg)
            values = [self._coerce_value(v, cfg, where) for v in values]
            cleaned.append({"field": key, "op": op, "values": values})
        return cleaned

    def _validate_arity(self, where, c, op, cfg):
        """Returns the normalised value list for the operator."""
        if op in _NO_VALUE_OPS:
            return []

        if op in _LIST_OPS:
            values = c.get("values")
            if not isinstance(values, list) or len(values) < 1:
                raise FilterSpecError(f"{where}: '{op}' needs a non-empty 'values' list.")
        elif op in _PAIR_OPS:
            values = c.get("values")
            if not isinstance(values, list) or len(values) != 2:
                raise FilterSpecError(f"{where}: 'between' needs exactly 2 values.")
        elif op in ("contains", "not_contains", "like"):
            # Single value OR a list. A list means "contains any of" /
            # "contains none of" / "matches any of these patterns" — the worked
            # example uses the list form, and the browser's own evaluator ORs a
            # multi-value condition the same way.
            if "values" in c and c.get("values") is not None:
                values = c["values"]
                if not isinstance(values, list) or len(values) < 1:
                    raise FilterSpecError(f"{where}: '{op}' needs a non-empty 'values' list.")
            elif "value" in c:
                values = [c["value"]]
            else:
                raise FilterSpecError(f"{where}: '{op}' needs 'value' or 'values'.")
        else:
            if "value" not in c:
                raise FilterSpecError(f"{where}: '{op}' needs a 'value'.")
            values = [c["value"]]

        # Membership operators must name a real choice. Ordinal and substring
        # operators must NOT be checked against the choice list: a range BOUND
        # need not itself be a stored value — "delegate_count between 0 and 5"
        # is legitimate even though the model only declares choices 0 and 1.
        choices = _choice_values(cfg)
        if choices is not None and op not in _UNCONSTRAINED_VALUE_OPS:
            for v in values:
                if v not in choices:
                    raise FilterSpecError(
                        f"{where}: '{v}' is not a valid value for this field."
                    )
        return values

    # ── Q construction ────────────────────────────────────────────────────────
    def _q_for(self, criterion):
        key, op, values = criterion["field"], criterion["op"], criterion["values"]
        cfg = self.get_filter_spec_fields()[key]
        path = self._path_for(key, cfg, op)

        if op == "is_empty":
            return self._empty_q(path, cfg)
        if op == "is_not_empty":
            return ~self._empty_q(path, cfg)

        v = values[0] if values else None

        if op == "is":
            if cfg["type"] in ("text", "choice"):
                return Q(**{f"{path}__iexact": v})
            return Q(**{path: v})
        if op == "is_not":
            if cfg["type"] in ("text", "choice"):
                return ~Q(**{f"{path}__iexact": v})
            return ~Q(**{path: v})

        if op == "contains":
            q = Q()
            for item in values:
                q |= Q(**{f"{path}__icontains": item})
            return q
        if op == "not_contains":
            # "contains none of these" — NOT(any of them appears).
            q = Q()
            for item in values:
                q |= Q(**{f"{path}__icontains": item})
            return ~q

        if op == "like":
            q = Q()
            for item in values:
                q |= Q(**{f"{path}__iregex": _like_regex(item)})
            return q

        if op == "starts_with":
            return Q(**{f"{path}__istartswith": v})
        if op == "ends_with":
            return Q(**{f"{path}__iendswith": v})

        if op == "any_of":
            q = Q()
            for item in values:
                q |= (Q(**{f"{path}__iexact": item})
                      if cfg["type"] in ("text", "choice") else Q(**{path: item}))
            return q
        if op == "none_of":
            q = Q()
            for item in values:
                q |= (Q(**{f"{path}__iexact": item})
                      if cfg["type"] in ("text", "choice") else Q(**{path: item}))
            return ~q

        if op == "gt":     return Q(**{f"{path}__gt": v})
        if op == "gte":    return Q(**{f"{path}__gte": v})
        if op == "lt":     return Q(**{f"{path}__lt": v})
        if op == "lte":    return Q(**{f"{path}__lte": v})
        if op == "before": return Q(**{f"{path}__lt": v})
        if op == "after":  return Q(**{f"{path}__gt": v})
        if op == "between":
            return Q(**{f"{path}__gte": values[0], f"{path}__lte": values[1]})
        # An undated row IS returned. Django compiles a negated lookup on a
        # nullable column to NOT(col BETWEEN a AND b AND col IS NOT NULL), so
        # NULL survives the negation — exactly as it already does for is_not and
        # none_of. Left to behave that way rather than bolted shut with an
        # extra isnull=False, because three negations in one vocabulary that
        # disagree about empty values is worse than any one convention; the
        # frontend's local evaluator matches this deliberately.
        if op == "not_between":
            return ~Q(**{f"{path}__gte": values[0], f"{path}__lte": values[1]})

        raise FilterSpecError(f"Unhandled operator '{op}'.")

    # ── Entry point ───────────────────────────────────────────────────────────
    def apply_filter_spec(self, queryset):
        raw = self.request.query_params.get("filter_spec")
        if not raw:
            return queryset
        try:
            spec = json.loads(raw)
        except (TypeError, ValueError):
            raise FilterSpecError("filter_spec is not valid JSON.")

        criteria = self._validate(spec)
        if not criteria:
            # An empty criteria list is a no-op, not an error.
            return queryset

        queryset = self._prepare(queryset, criteria)
        combined = Q()
        for c in criteria:
            combined &= self._q_for(c)     # match=all
        return queryset.filter(combined)

    def filter_queryset(self, queryset):
        """
        super() first so DjangoFilterBackend / SearchFilter / OrderingFilter run
        exactly as before; the spec then ANDs onto their result.
        """
        qs = super().filter_queryset(queryset)
        try:
            return self.apply_filter_spec(qs)
        except FilterSpecError as exc:
            # ParseError renders as 400 {"detail": "..."} — the same shape the
            # rest of the codebase returns for bad input.
            raise ParseError(detail=str(exc))
