/**
 * lib/filterSpec.js
 * ─────────────────
 * Translates DataTable's UI filter conditions into the wire shape that
 * backend/accounts/filter_spec.py validates, and decides — per condition —
 * whether the backend can express it at all.
 *
 * WHY THIS EXISTS
 * DataTable used to filter rows already in memory. For `payment_status` that is
 * not merely slower, it is a different answer. The backend resolves the
 * person-level fields as
 *
 *     COALESCE(NULLIF(delegate_payment_status, ''), invoice.payment_status)
 *
 * (filter_spec.py `_resolved_expression`, and the same fallback in
 * book_delegate/filters.py `_effective_filter`). The serializer sends that
 * resolved value as `effective_payment_status`, which api/bookings.js maps onto
 * the row's `payment_status`. So filtering the loaded rows on `payment_status`
 * happens to agree with the API — but only for the rows that were loaded. Since
 * `delegate_payment_status` is NULL on every row today, any filter built against
 * the raw override column would match nothing. Keeping the translation in one
 * file, driven by the server's own `filter_schema`, is what stops those two
 * definitions drifting apart again.
 *
 * DENY BY DEFAULT
 * A condition is sent to the server ONLY when the fetched schema lists both the
 * field and the mapped operator. Anything else stays client-side. That mirrors
 * the backend's own posture — nothing is filterable unless registered — and it
 * means a backend that drops a field degrades to local filtering instead of
 * 400ing every list request.
 */

import {
  DATE_NO_VALUE_OPS,
  dateCondActive, dateCondBound, dateCondWindow, isDateOp,
} from './dateFilter';

// UI operator -> backend operator. `multi` names the operator to use when the
// user supplied more than one value, because the backend's arity rules differ:
// `is` takes a single `value`, `any_of` takes a `values` list.
//
// `Like` used to have NO entry, because the backend vocabulary had no SQL-LIKE
// operator — so every Like condition was evaluated locally, against the page
// that happened to be loaded, while looking exactly like a filter that worked.
// filter_spec.py registers `like` now (translated to an anchored regex by
// _like_regex, which mirrors DataTable's own likeTest), so it travels. Its list
// form ORs the patterns, which is what condPasses does with several values.
const OP_MAP = {
  'Is': { single: 'is', multi: 'any_of' },
  'Is Not': { single: 'is_not', multi: 'none_of' },
  // contains/not_contains accept `value` OR a `values` list server-side, and the
  // list form means "contains any of" / "contains none of" — the same OR the
  // client's condPasses applies. So one operator covers both arities.
  'Contains': { single: 'contains', multi: 'contains', listOk: true },
  'Not Contains': { single: 'not_contains', multi: 'not_contains', listOk: true },
  // No multi form: the backend takes a single value for these. A user who
  // enters two prefixes gets local filtering rather than a silently wrong
  // server answer that matched only the first.
  'Starts With': { single: 'starts_with', multi: null },
  'Ends With': { single: 'ends_with', multi: null },
  'Like': { single: 'like', multi: 'like', listOk: true },
  'Is Empty': { single: 'is_empty', multi: 'is_empty', noValue: true },
  'Is Not Empty': { single: 'is_not_empty', multi: 'is_not_empty', noValue: true },
};

const NO_VALUE_UI_OPS = ['Is Empty', 'Is Not Empty'];

// ── Date conditions ──────────────────────────────────────────────────────────
/**
 * Date operator -> backend operator. Separate from OP_MAP because the operators
 * collide by NAME and not by meaning: a date "Is" is a whole-day (or whole-
 * window) containment test, so it maps to `between`, not to `is`.
 *
 * `not_between` is the one entry that is not merely a rename. "Is not in this
 * window" is `before OR after`, and filter_spec's match mode is `all` — an AND
 * — so it cannot be assembled from two criteria. Without the operator the whole
 * condition falls back to filtering the fetched page, which on a 130,000-row
 * table means a count that describes the page rather than the table. It is
 * registered in accounts/filter_spec.py alongside `between`; deny-by-default
 * still applies, so a backend that does not offer it degrades to local
 * filtering rather than 400ing.
 *
 * Is and Is Not map to the PAIR operators rather than to `is` / `is_not`
 * because a picked day is a window: on a DateTimeField, `is '2026-08-25'` means
 * the instant of midnight and matches almost nothing, where the day's two edges
 * match the day. One mapping that is right for both column kinds beats two that
 * are each right for one.
 */
const DATE_OP_MAP = {
  Is: 'between',
  'Is Not': 'not_between',
  Between: 'between',
  Before: 'before',
  After: 'after',
  'Is Empty': 'is_empty',
  'Is Not Empty': 'is_not_empty',
};

/** Does this UI condition carry a date payload? Mirrors DataTable's isDateCond. */
function isDateCondition(cond) {
  return !!(cond && cond.date && isDateOp(cond.op));
}

/**
 * A calendar date as the value the backend should compare against.
 *
 * On a DateField, the date itself is the whole answer. On a DateTimeField it is
 * not: `lte '2026-08-24'` is `lte 2026-08-24 00:00`, which excludes everything
 * that happened during the day the user asked for — the same trap
 * accounts/period_filter.day_bounds() documents. So a datetime field gets an
 * explicit instant at the requested EDGE of the day, offset stated rather than
 * naive: `TIME_ZONE` is UTC, and a naive string would be interpreted by Django
 * under a warning instead of by this file's intent.
 *
 * `has_time` comes from the field's own schema entry, which build_filter_spec_
 * fields() sets from the Django field class. Absent (an older backend), the bare
 * date is sent, which is exactly the behaviour that existed before.
 */
function dateEdge(iso, edge, hasTime) {
  if (!hasTime) return iso;
  return edge === 'end' ? `${iso}T23:59:59.999999+00:00` : `${iso}T00:00:00+00:00`;
}

function dateCriterion(cond, field, cfg) {
  const op = DATE_OP_MAP[cond.op];
  if (!op) return { ok: false, reason: `date operator '${cond.op}' has no backend equivalent` };
  if (!(cfg.operators || []).includes(op)) {
    return { ok: false, reason: `'${op}' not allowed on '${field}'` };
  }
  const hasTime = !!cfg.has_time;

  if (DATE_NO_VALUE_OPS.includes(cond.op)) return { ok: true, criterion: { field, op } };

  // Named one by one rather than by list membership: these two are the only
  // date operators whose backend form takes a single `value`, and everything
  // else — Is and Is Not included, since a picked day is a window of one — takes
  // a two-element `values`. A list that drifted by one entry would send an
  // arity the backend answers with a 400 on every list request.
  if (cond.op === 'Before' || cond.op === 'After') {
    const bound = dateCondBound(cond);
    if (!bound) return { ok: false, reason: 'no date' };
    // `before` is a strict <, so the boundary is the START of the named day and
    // the day itself is excluded; `after` is a strict >, so its boundary is the
    // END of the named day. Both then mean what the words mean.
    const value = dateEdge(bound, cond.op === 'Before' ? 'start' : 'end', hasTime);
    return { ok: true, criterion: { field, op, value } };
  }

  const win = dateCondWindow(cond);
  if (!win) return { ok: false, reason: 'no date window' };
  return {
    ok: true,
    criterion: {
      field,
      op,
      values: [dateEdge(win.from, 'start', hasTime), dateEdge(win.to, 'end', hasTime)],
    },
  };
}

// ── Values the backend can actually compare ──────────────────────────────────
/**
 * Tokens accounts/filter_spec.py `_coerce_value` accepts for a boolean.
 *
 * It answers 400 for anything else, and a 400 on the list request is not a
 * rejected criterion — it is a table that shows an error instead of rows. So a
 * value outside this set is never SENT; the condition stays client-side, which
 * narrows the loaded page but leaves the screen working.
 */
const BOOL_TOKENS = ['true', 'false', '1', '0'];

/**
 * Can the backend compare this value against a field of this type?
 *
 * Only two types can be handed something they cannot parse. A boolean is
 * rejected outright (above). A NUMBER is worse: `Q(estimate='abc')` raises
 * inside the ORM rather than validating, so a typo in a numeric filter box
 * would take the whole list down. Substring operators are exempt — they run
 * against a text CAST of the column, so any string is a legitimate operand.
 *
 * Anything else — text, choice, date, fk — is either unconstrained or already
 * checked against the field's own choice list further down.
 */
function valueFitsType(value, cfg, op) {
  if (cfg.type === 'boolean') return BOOL_TOKENS.includes(String(value).toLowerCase());
  if (cfg.type === 'number' && !['contains', 'not_contains', 'like'].includes(op)) {
    return String(value).trim() !== '' && Number.isFinite(Number(value));
  }
  return true;
}

/** Every value the condition should match: committed chips plus the live draft. */
export function condValues(cond) {
  const live = cond._live ? [cond._live] : [];
  return [...(cond.values || []), ...live].filter((v) => v !== '' && v != null);
}

/** Mirrors DataTable's condActive: does this condition constrain anything? */
export function condIsActive(cond) {
  if (isDateCondition(cond)) return dateCondActive(cond);
  if (NO_VALUE_UI_OPS.includes(cond.op)) return true;
  return condValues(cond).length > 0;
}

/**
 * Backend field name for a column, or null when the column has no server-side
 * equivalent. Columns opt in by declaring `serverField`; a column that omits it
 * is client-only by construction rather than by omission somewhere else.
 */
export function serverFieldFor(col) {
  if (!col) return null;
  if (col.serverField === false) return null;
  return col.serverField || null;
}

/**
 * Can the server evaluate this condition? Requires the column to declare a
 * server field, the schema to register it, and the schema to allow the mapped
 * operator on it.
 *
 * Returns { ok: true, criterion } or { ok: false, reason }.
 */
export function toCriterion(cond, col, schema) {
  const field = serverFieldFor(col);
  if (!field) return { ok: false, reason: 'column has no server-side field' };

  // Read the schema BEFORE the operator map: a date condition's operator names
  // collide with the text ones, and only the condition's own shape distinguishes
  // them.
  const cfg = schema?.fields?.[field];
  if (!cfg) return { ok: false, reason: `field '${field}' is not filterable on this resource` };

  if (isDateCondition(cond)) return dateCriterion(cond, field, cfg);

  const map = OP_MAP[cond.op];
  if (!map) return { ok: false, reason: `operator '${cond.op}' has no backend equivalent` };

  if (map.noValue) {
    if (!(cfg.operators || []).includes(map.single)) {
      return { ok: false, reason: `'${map.single}' not allowed on '${field}'` };
    }
    // is_empty / is_not_empty carry NO value key at all — the backend's
    // _validate_arity returns [] for them and a stray key is a 400.
    return { ok: true, criterion: { field, op: map.single } };
  }

  const values = condValues(cond);
  if (values.length === 0) return { ok: false, reason: 'no values' };

  const multi = values.length > 1;
  const op = multi ? map.multi : map.single;
  if (!op) {
    return { ok: false, reason: `'${cond.op}' with ${values.length} values has no backend form` };
  }
  if (!(cfg.operators || []).includes(op)) {
    return { ok: false, reason: `'${op}' not allowed on '${field}'` };
  }

  // A value the field's own type cannot parse never travels — see
  // valueFitsType. Half-typed input reaches here on every keystroke (`_live`),
  // so "5" mid-way to "50" is normal and a 400 would blank the table while the
  // user was still typing.
  const unfit = values.find((v) => !valueFitsType(v, cfg, op));
  if (unfit !== undefined) {
    return { ok: false, reason: `'${unfit}' is not a ${cfg.type} value` };
  }

  // Membership operators must name a real choice; the backend rejects anything
  // else with a 400. Substring and ordinal operators are deliberately exempt
  // (filter_spec.py _UNCONSTRAINED_VALUE_OPS), so a "contains" fragment is fine.
  const unconstrained = ['contains', 'not_contains', 'like', 'gt', 'gte', 'lt', 'lte', 'between', 'not_between', 'before', 'after'];
  if (cfg.choices && !unconstrained.includes(op)) {
    const allowed = cfg.choices.map((c) => (typeof c === 'object' ? c.value : c));
    const bad = values.find((v) => !allowed.includes(v));
    if (bad !== undefined) {
      return { ok: false, reason: `'${bad}' is not a registered choice for '${field}'` };
    }
  }

  // Arity: a list operator (or contains' list form) takes `values`; everything
  // else takes a single `value`.
  if (multi || (map.listOk && values.length > 1)) {
    return { ok: true, criterion: { field, op, values } };
  }
  if (op === 'any_of' || op === 'none_of' || op === 'between') {
    return { ok: true, criterion: { field, op, values } };
  }
  return { ok: true, criterion: { field, op, value: values[0] } };
}

/**
 * Split the table's conditions into a server spec and a client-side remainder.
 *
 * Returns:
 *   criteria      — backend criteria, for specToJson()
 *   clientConds   — conditions the backend cannot express; DataTable still
 *                   filters the fetched page with these
 *   unsupported   — [{key, reason}] for surfacing/debugging, never silent
 */
export function partitionConds(conds, cols, schema) {
  const criteria = [];
  const clientConds = [];
  const unsupported = [];

  for (const cond of conds || []) {
    if (!condIsActive(cond)) continue;
    const col = cols.find((c) => c.key === cond.key);
    const res = toCriterion(cond, col, schema);
    if (res.ok) {
      criteria.push(res.criterion);
    } else {
      clientConds.push(cond);
      unsupported.push({ key: cond.key, reason: res.reason });
    }
  }
  return { criteria, clientConds, unsupported };
}

/**
 * RAW JSON for the `filter_spec` query param, or null when nothing is filtered.
 *
 * Deliberately NOT percent-encoded — api/client.js serializeParams runs it
 * through URLSearchParams, which encodes exactly once. Pre-encoding here is the
 * double-encoding bug that shipped: Django decodes once, saw the literal text
 * "%7B%22match%22…" and answered 400 "filter_spec is not valid JSON".
 */
export function specToJson(criteria) {
  if (!criteria || criteria.length === 0) return null;
  return JSON.stringify({ match: 'all', criteria });
}

/**
 * Conservative ceiling for the whole `filter_spec=<encoded>` query param.
 * gunicorn's default limit_request_line is 4094 bytes for the entire request
 * line; past it gunicorn answers 414 before Django sees anything, so there is
 * no JSON body and no application log to debug from. 3300 leaves room for the
 * path plus page/page_size/ordering.
 */
export const MAX_SPEC_BYTES = 3300;

export function specByteLength(json) {
  return json ? `filter_spec=${encodeURIComponent(json)}`.length : 0;
}

/**
 * Map the table's sort onto the backend `ordering` param.
 *
 * StableOrderingFilter (accounts/ordering.py) appends `pk` to whatever ordering
 * is in effect, so pagination is stable without the client asking for it. What
 * the client MUST get right is the field name: an unrecognised ordering term is
 * dropped silently by DRF, which leaves rows in the default order while the
 * header claims otherwise. Columns therefore declare `serverOrdering`
 * explicitly, and a column without it sorts locally.
 */
export function orderingParam(sort, cols) {
  if (!sort || !sort.key) return null;
  const col = cols.find((c) => c.key === sort.key);
  const term = col && col.serverOrdering;
  if (!term) return null;
  return sort.dir === 'desc' ? `-${term}` : term;
}
