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

// UI operator -> backend operator. `multi` names the operator to use when the
// user supplied more than one value, because the backend's arity rules differ:
// `is` takes a single `value`, `any_of` takes a `values` list.
//
// `Like` has NO entry: the backend vocabulary has no SQL-LIKE operator
// (OPERATORS_BY_TYPE in filter_spec.py), so a Like condition can only ever be
// evaluated locally. Leaving it unmapped is what routes it there.
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
  'Is Empty': { single: 'is_empty', multi: 'is_empty', noValue: true },
  'Is Not Empty': { single: 'is_not_empty', multi: 'is_not_empty', noValue: true },
};

const NO_VALUE_UI_OPS = ['Is Empty', 'Is Not Empty'];

/** Every value the condition should match: committed chips plus the live draft. */
export function condValues(cond) {
  const live = cond._live ? [cond._live] : [];
  return [...(cond.values || []), ...live].filter((v) => v !== '' && v != null);
}

/** Mirrors DataTable's condActive: does this condition constrain anything? */
export function condIsActive(cond) {
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

  const map = OP_MAP[cond.op];
  if (!map) return { ok: false, reason: `operator '${cond.op}' has no backend equivalent` };

  const cfg = schema?.fields?.[field];
  if (!cfg) return { ok: false, reason: `field '${field}' is not filterable on this resource` };

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

  // Membership operators must name a real choice; the backend rejects anything
  // else with a 400. Substring and ordinal operators are deliberately exempt
  // (filter_spec.py _UNCONSTRAINED_VALUE_OPS), so a "contains" fragment is fine.
  const unconstrained = ['contains', 'not_contains', 'gt', 'gte', 'lt', 'lte', 'between', 'before', 'after'];
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
