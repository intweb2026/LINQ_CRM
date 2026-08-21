/**
 * BulkUpdateModal — mass update: pick one field, set one value, apply to the
 * selected rows. Drives backend/accounts/bulk_update.py.
 *
 * The field list comes entirely from `schema` (the server's bulk_update_schema
 * response). Nothing about what is editable is hardcoded here — the backend
 * denies by default, and a field it has not declared must not be offered.
 *
 * WHY THE PREVIEW IS NOT OPTIONAL
 * bulk_update runs preview and commit down the SAME resolution path; `commit`
 * only decides whether the write block executes. The preview is therefore an
 * accurate account of what is about to happen, and it is the only place three
 * things become visible:
 *
 *   • `permitted` < `requested`  — rows the caller cannot edit (RBAC scoping)
 *   • `collateral`               — a parent-group field writes to the shared
 *                                 invoice, so it also changes rows the user
 *                                 never selected
 *   • `side_effects`             — declared model consequences, e.g. setting
 *                                 delegate_payment_status to Cancelled also
 *                                 sets delegate_count to 0
 *
 * Any of those makes review mandatory. A plain row-scoped change with none of
 * them stays two clicks (`fastPath`).
 *
 * plan_hash: the fingerprint of the plan the user was shown. It is echoed back
 * on commit and the backend refuses with 409 if the underlying data moved,
 * handing back the refreshed plan — which this component shows rather than
 * silently applying a stale one.
 *
 * Contract with the caller:
 *   onPreview(field, value)          -> resolves to the plan object
 *   onCommit(field, value, planHash) -> resolves to the result, or throws
 * The caller must NOT raise its own toast; this component owns success and
 * error messaging so a 409 retry does not double-notify.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import Modal from './Modal';
import { Icon } from '../lib/icons';
import { nf } from '../lib/helpers';
import { NumField } from './UI';
import { useToast } from '../context/ToastContext';

// Just the (possibly pluralised) noun. `plur` from lib/helpers returns the count
// as well, and stripping it back off with a regex breaks the moment nf() inserts
// a thousands separator ("1,234 delegates").
function noun(n, label) {
  return n === 1 ? label : `${label}s`;
}

// Above this many fields the picker gets a search box. Bookings declares 46
// across both groups and Events 34: a bare radio list that long is a scroll hunt.
const SEARCH_THRESHOLD = 8;

/**
 * Settle time before a value change is priced.
 *
 * The preview used to be one request, so firing it per keystroke was merely
 * wasteful. A selection now spans as many batches as it has thousands of rows
 * (useBulkUpdate), so a full Ticket Central select-all costs 36 requests per
 * keystroke — typing a six-character value would issue over two hundred. Matches
 * useServerRows' DEBOUNCE_MS, which the filter inputs already feel like.
 */
const PREVIEW_DEBOUNCE_MS = 350;

// The server sends the distribution keyed by str(value), so a BooleanField
// arrives as Python's "True"/"False" while the picker offers Yes/No. Showing
// both spellings for one column reads as two different things.
function display(value, config) {
  if (value === null || value === 'null' || value === '') return '(none)';
  if (config?.type !== 'boolean') return String(value);
  const s = String(value);
  if (s === 'True' || s === 'true') return 'Yes';
  if (s === 'False' || s === 'false') return 'No';
  return s;
}

export default function BulkUpdateModal({
  onClose, selectedIds = [], schema, rowLabel = 'record', onPreview, onCommit,
  // The table's total, so the header can say whether this selection IS the whole
  // filtered set. Since the header checkbox began resolving every match, the two
  // are equal often — and the caveat below correctly disappears when they are,
  // rather than warning about a subset that no longer exists.
  totalMatching = null,
}) {
  const toast = useToast();

  const [step, setStep] = useState('pick');       // 'pick' | 'preview' | 'result'
  const [field, setField] = useState('');
  const [value, setValue] = useState('');
  const [plan, setPlan] = useState(null);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [staleNotice, setStale] = useState('');
  const [clearing, setClearing] = useState(false); // explicit-null mode
  const [query, setQuery] = useState('');          // field-picker search
  // {done, total, updated} while a batched commit walks its batches, else null.
  const [progress, setProgress] = useState(null);
  // Set when a batched commit failed PART WAY: some rows are already written.
  const [partial, setPartial] = useState(null);
  /**
   * Bumped to force a re-preview with the same field and value.
   *
   * Needed because a plan is now a list of per-batch plan_hashes held inside
   * useBulkUpdate, not one hash on this component's `plan`. After a 409 those
   * hashes are ALL stale, and re-confirming would replay them and 409 again
   * forever. Re-pricing is the only thing that mints fresh ones.
   */
  const [refreshTick, setRefreshTick] = useState(0);

  const fields = useMemo(() => schema?.fields || {}, [schema]);
  const config = field ? fields[field] : null;

  const { rowFields, parentFields } = useMemo(() => {
    const row = [], parent = [];
    Object.entries(fields).forEach(([key, cfg]) => {
      (cfg.group === 'parent' ? parent : row).push([key, cfg]);
    });
    return { rowFields: row, parentFields: parent };
  }, [fields]);

  const bothGroups = rowFields.length > 0 && parentFields.length > 0;
  const searchable = Object.keys(fields).length > SEARCH_THRESHOLD;

  // Matched on the label AND the column name: a rep searches "payment", a
  // developer reading a bug report searches "delegate_payment_status".
  const matches = useCallback((entries) => {
    const q = query.trim().toLowerCase();
    if (!q) return entries;
    return entries.filter(([key, cfg]) =>
      key.toLowerCase().includes(q) || (cfg.label || '').toLowerCase().includes(q));
  }, [query]);

  // Re-price the plan whenever field or value changes. Previewing with NO value
  // is valid and useful — the distribution of current values does not depend on
  // the target, so "what am I about to overwrite?" renders the moment a field is
  // picked. no_op and side_effects only arrive once a value is chosen.
  useEffect(() => {
    if (!field || !config) return undefined;
    // Never call the endpoint with an empty selection: a caller whose handler is
    // not memoised re-fires this effect on every parent render, and after a
    // commit clears the selection that would post ids:[] and get back
    // {"detail":"ids list required"}.
    if (!selectedIds.length) { setPlan(null); return undefined; }
    // undefined => omit the key entirely (preview with no target chosen).
    // null      => explicit clear, only offered on nullable fields.
    const outgoing = clearing ? null : (value !== '' && value != null ? value : undefined);
    let cancelled = false;
    setBusy(true); setError('');
    const timer = setTimeout(() => {
      Promise.resolve(onPreview(field, outgoing))
        .then((p) => { if (!cancelled) setPlan(p); })
        .catch((err) => {
          if (!cancelled) setError(err?.response?.data?.detail || 'Could not preview this change.');
        })
        .finally(() => { if (!cancelled) setBusy(false); });
    }, PREVIEW_DEBOUNCE_MS);
    return () => { cancelled = true; clearTimeout(timer); };
    // selectedIds.LENGTH, not the array: callers build it with [...set], a new
    // reference every render, which would re-fire this effect continuously.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [field, value, clearing, config, onPreview, selectedIds.length, refreshTick]);

  const pickField = useCallback((key) => {
    setField(key); setPlan(null); setError(''); setValue(''); setClearing(false);
  }, []);

  const valueChosen = clearing || (value !== '' && value != null);
  const hasSideEffects = (plan?.side_effects?.length || 0) > 0;
  const hasCollateral = (plan?.collateral?.count || 0) > 0;
  const isParent = config?.group === 'parent';

  const fastPath = !!plan && valueChosen && !isParent
    && plan.permitted === plan.requested && !hasSideEffects && !hasCollateral;

  async function doCommit() {
    setBusy(true); setError(''); setStale(''); setPartial(null);
    // Batched commits report after each batch. Seeded so the bar appears on the
    // first click rather than only once batch 1 has come back.
    const batches = plan?.batches || 1;
    setProgress(batches > 1 ? { done: 0, total: batches, updated: 0 } : null);
    try {
      const res = await onCommit(
        field, clearing ? null : value, plan.plan_hash,
        batches > 1 ? setProgress : undefined,
      );
      setResult(res);
      setStep('result');
      toast(`Updated ${nf(res.updated)} ${noun(res.updated, rowLabel)}`, 'ok');
    } catch (err) {
      // Checked BEFORE the 409 branch, and it has to be. A partly-applied commit
      // that came back 409 on a later batch must not offer "confirm again" —
      // confirming would replay the batches that already landed and apply them
      // twice. There is no clean retry here, only an honest account.
      if (err?.partial) {
        setPartial(err.partial);
        setStep('result');
        setResult({ updated: err.partial.updated, no_op: 0, partial: true });
        toast(
          `Stopped after ${nf(err.partial.updated)} ${noun(err.partial.updated, rowLabel)}`,
          'er',
        );
      } else if (err?.response?.status === 409) {
        // The server's refreshed plan goes up immediately so the numbers are
        // current while the re-price runs; the re-price is what replaces the
        // stale batch hashes, without which confirming again would 409 forever.
        setPlan(err.response.data);
        setStep('preview');
        setRefreshTick((t) => t + 1);
        setStale('The underlying data changed since this plan was generated. Review the refreshed numbers and confirm again.');
      } else {
        setError(err?.response?.data?.detail || 'Bulk update failed.');
      }
    } finally {
      setBusy(false);
      setProgress(null);
    }
  }

  const fieldList = (entries) => {
    const shown = matches(entries);
    if (!shown.length) {
      return <div className="bu-hint bu-none">No field here matches “{query}”.</div>;
    }
    return (
      <div className="bu-fl">
        {shown.map(([key, cfg]) => (
          <label className={'pop-i' + (field === key ? ' on' : '')} key={key}>
            <input type="radio" name="bulk-field" checked={field === key} onChange={() => pickField(key)} />
            {cfg.label || key}
          </label>
        ))}
      </div>
    );
  };

  // Capped: a text column across 1000 rows can hold 1000 distinct values, and
  // the full list would bury the counts that matter. The tail is summed rather
  // than dropped, so the numbers still add up to the selection.
  const distribution = plan && Object.keys(plan.distribution || {}).length > 0 ? (() => {
    const all = Object.entries(plan.distribution).sort((a, b) => b[1] - a[1]);
    const head = all.slice(0, 6);
    const rest = all.slice(6).reduce((n, [, count]) => n + count, 0);
    return (
      <div className="bu-dist">
        Currently:{' '}
        {head.map(([k, n]) => `${nf(n)} ${display(k, config)}`).join(' · ')}
        {rest > 0 ? ` · ${nf(rest)} across ${all.length - head.length} other values` : ''}
      </div>
    );
  })() : null;

  const pickBody = (
    <div>
      <div className="bu-sel">
        {nf(selectedIds.length)} {noun(selectedIds.length, rowLabel)} selected
        {totalMatching != null && totalMatching > selectedIds.length ? (
          <> — <b>not</b> all {nf(totalMatching)} matching records. Only what you selected will change.</>
        ) : null}
      </div>

      {searchable ? (
        <input
          className="in bu-search"
          type="search"
          value={query}
          placeholder="Search fields…"
          onChange={(e) => setQuery(e.target.value)}
        />
      ) : null}

      {!bothGroups ? fieldList(rowFields.length ? rowFields : parentFields) : (
        <>
          <div className="bu-sec">Per-{rowLabel} fields</div>
          <div className="bu-hint">Affects exactly the {nf(selectedIds.length)} rows you selected.</div>
          {fieldList(rowFields)}
          <div className="bu-sec bu-sec-d">Shared fields</div>
          <div className="bu-hint bu-hint-d">
            {hasCollateral
              ? `Writes to the shared invoice — also changes ${nf(plan.collateral.count)} row(s) you did not select.`
              : 'Writes to the shared invoice — may change rows you did not select.'}
          </div>
          {fieldList(parentFields)}
        </>
      )}

      {config ? (
        <div className="bu-val">
          <div className="bu-sec">New value</div>
          {config.type === 'boolean' ? (
            /* Sent as the strings "true"/"false"; the backend coerces to a real
               bool so a BooleanField never receives the truthy string "false". */
            <select className="in" value={value} onChange={(e) => setValue(e.target.value)} disabled={clearing}>
              <option value="">Choose a value…</option>
              <option value="true">Yes</option>
              <option value="false">No</option>
            </select>
          ) : config.type === 'choice' ? (
            <select className="in" value={value} onChange={(e) => setValue(e.target.value)} disabled={clearing}>
              <option value="">Choose a value…</option>
              {(config.choices || []).map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          ) : config.type === 'date' ? (
            <input className="in" type="date" value={value} onChange={(e) => setValue(e.target.value)} disabled={clearing} />
          ) : config.type === 'integer' || config.type === 'decimal' ? (
            /* min/max/step come from the schema, which reads them off the model's
               own validators and decimal_places. NumField holds the value inside
               them as it is typed — on a plain number input they are a spinner
               hint only, so a bulk update could carry an out-of-range value to
               every selected row and fail at the API for all of them at once.
               The backend still re-checks: a number input can be bypassed
               entirely, and this modal writes to many records. */
            <NumField
              value={value}
              min={config.min}
              max={config.max}
              step={config.type === 'integer' ? 1
                : (config.decimal_places ? 10 ** -config.decimal_places : 'any')}
              onChange={(e) => setValue(e.target.value)}
              disabled={clearing}
            />
          ) : (
            <input
              className="in"
              type="text"
              value={value}
              maxLength={config.max_length}
              onChange={(e) => setValue(e.target.value)}
              disabled={clearing}
            />
          )}

          {/* Only nullable fields can be emptied; the backend rejects a null on
              anything else, so don't offer it. Bookings is the only resource
              where clearing means "inherit" — everywhere else the column is
              simply emptied, and saying "the invoice's value" there would be
              wrong. */}
          {config.nullable ? (
            <label className="bu-clear">
              <input type="checkbox" checked={clearing} onChange={(e) => { setClearing(e.target.checked); setValue(''); }} />
              {isParent || field.startsWith('delegate_')
                ? "Clear this field instead (revert to the invoice's value)"
                : 'Clear this field instead (leave it empty)'}
            </label>
          ) : null}

          {distribution}
          {fastPath ? (
            <div className="bu-dist">
              {plan.no_op > 0
                ? `${nf(plan.permitted - plan.no_op)} will change · ${nf(plan.no_op)} already ${display(value, config)}`
                : `All ${nf(plan.permitted)} will change.`}
            </div>
          ) : null}
        </div>
      ) : null}

      {error ? <div className="vr er"><Icon name="warn" size={15} /><span>{error}</span></div> : null}
    </div>
  );

  const previewBody = plan ? (
    <div className="bu-prev">
      {staleNotice ? <div className="vr wn"><Icon name="warn" size={15} /><span>{staleNotice}</span></div> : null}

      <div className="bu-set">
        Setting <b>{config?.label || field}</b> to <b>{clearing ? '(cleared)' : display(value, config)}</b>
      </div>

      {/* no_op is absent from a value-less preview; preview is only reachable
          with a value, but guard so a 409 refresh can never render NaN. */}
      <ul className="bu-list">
        <li>{nf(plan.requested)} selected</li>
        <li>{nf(plan.permitted - (plan.no_op ?? 0))} will change</li>
        <li>{nf(plan.no_op ?? 0)} already {clearing ? 'empty' : display(value, config)}</li>
        {plan.requested > plan.permitted ? (
          <li className="bu-d">{nf(plan.requested - plan.permitted)} not editable by you</li>
        ) : null}
      </ul>

      {hasSideEffects ? (
        <div className="vr wn bu-warn">
          <Icon name="warn" size={15} />
          <span>{plan.side_effects.join(' · ')}</span>
        </div>
      ) : null}

      {hasCollateral ? (
        <div className="vr er bu-warn">
          <Icon name="warn" size={15} />
          <div>
            <b>
              {/* "up to" once the selection spans batches: the server computes
                  collateral per batch as "shares a parent with this batch, minus
                  this batch", so rows in a LATER batch — ones the user did
                  select — are counted here too. Overstating rows nobody chose is
                  the safe direction for this warning; presenting the inflated
                  sum as an exact count is not. See mergePlans. */}
              {plan.collateral.batched ? 'Up to ' : ''}
              {nf(plan.collateral.count)} {noun(plan.collateral.count, rowLabel)} you
              did not select will also change
              {plan.collateral.hidden_count > 0
                ? ` — ${plan.collateral.sample.length} shown, ${nf(plan.collateral.hidden_count)} on records outside your access`
                : ''}
            </b>
            {plan.collateral.batched ? (
              <div className="bu-coll">
                Counted per batch of {nf(Math.ceil(plan.requested / plan.batches))}, so this
                figure can include rows further down your own selection.
              </div>
            ) : null}
            {plan.collateral.sample.map((c) => (
              <div className="bu-coll" key={c.id}>{c.label}{c.parent ? ` — ${c.parent}` : ''}</div>
            ))}
            {plan.collateral.overflow > 0 ? (
              <div className="bu-coll">…and {nf(plan.collateral.overflow)} more you can see</div>
            ) : null}
          </div>
        </div>
      ) : null}

      {error ? <div className="vr er"><Icon name="warn" size={15} /><span>{error}</span></div> : null}
    </div>
  ) : null;

  const resultBody = result ? (
    <div className="bu-prev">
      {partial ? (
        <>
          <div className="vr er bu-warn">
            <Icon name="warn" size={15} />
            <div>
              <b>Stopped part way — {nf(partial.updated)} {noun(partial.updated, rowLabel)} were
              already updated.</b>
              <div className="bu-coll">
                {nf(partial.batchesDone)} of {nf(partial.batchesTotal)} batches completed.
                The remaining {nf(partial.rowsRemaining)} {noun(partial.rowsRemaining, rowLabel)} were
                not touched.
              </div>
            </div>
          </div>
          {/* No retry button. The batches that succeeded are committed, and
              re-running the whole selection would apply them a second time.
              The selection is left intact so it can be narrowed and re-run
              deliberately. */}
          <div className="bu-dist">
            The table has been refreshed. Re-select the {noun(2, rowLabel)} that did not
            change and run the update again.
          </div>
        </>
      ) : (
        <>
          Updated <b>{nf(result.updated)}</b> {noun(result.updated, rowLabel)}
          {result.batches > 1 ? <> across {nf(result.batches)} batches</> : null}.
          {result.no_op > 0 ? (
            <div className="bu-dist">{nf(result.no_op)} already held that value and were left alone.</div>
          ) : null}
        </>
      )}
    </div>
  ) : null;

  let footer;
  if (step === 'pick') {
    const changing = plan ? plan.permitted - (plan.no_op ?? 0) : 0;
    footer = (
      <>
        <button className="btn btn-s" onClick={onClose}>Cancel</button>
        <button className="btn btn-p" disabled={!plan || !valueChosen || busy}
          onClick={() => (fastPath ? doCommit() : setStep('preview'))}>
          {/* fastPath commits from this step, and a fast path is not a short
              one — a row-scoped field with no side effects over a whole-set
              selection is still 36 batches. */}
          {progress ? `Applying batch ${nf(progress.done + 1)} of ${nf(progress.total)}…`
            : busy ? 'Working…'
              : fastPath ? `Apply to ${nf(changing)} ${noun(changing, rowLabel)}`
                : 'Review changes →'}
        </button>
      </>
    );
  } else if (step === 'preview') {
    footer = (
      <>
        <button className="btn btn-s" disabled={busy} onClick={() => setStep('pick')}>← Back</button>
        <button className={'btn ' + (hasCollateral ? 'btn-d' : 'btn-p')} disabled={busy} onClick={doCommit}>
          {/* A 36-batch commit takes long enough that a static "Applying…"
              reads as a hang. The batch counter is the only signal that it is
              still moving, so it is shown wherever the eye already is. */}
          {progress ? `Applying batch ${nf(progress.done + 1)} of ${nf(progress.total)}…`
            : busy ? 'Applying…' : 'Apply'}
        </button>
      </>
    );
  } else {
    footer = <button className="btn btn-p" onClick={onClose}>Done</button>;
  }

  return (
    <Modal
      size="mdw"
      onClose={onClose}
      title={step === 'result' ? 'Mass update complete'
        : `Update ${nf(selectedIds.length)} ${noun(selectedIds.length, rowLabel)}`}
      footer={footer}
    >
      {step === 'pick' ? pickBody : null}
      {step === 'preview' ? previewBody : null}
      {step === 'result' ? resultBody : null}
    </Modal>
  );
}
