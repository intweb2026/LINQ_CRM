import DatePicker from './DatePicker';
import { DATE_RANGE_OPS, dateCondBound, dateCondWindow, fmtWindow } from '../lib/dateFilter';

/**
 * The VALUE half of a date condition. The operator dropdown stays in DataTable
 * beside every other column's, so a date column's editor differs from a text
 * column's in exactly one place rather than being a second filter panel.
 *
 * Two shapes, chosen by the operator:
 *   Between                     two date pickers
 *   Is / Is Not / Before/After  one date picker
 *
 * Nothing here is a preset. The user picks the date and the operator decides
 * what it means, which is why there is no dropdown of relative options to keep
 * in step with the arithmetic behind it.
 *
 * The condition moves only when DatePicker reports a chosen day, so opening a
 * calendar and paging through months changes nothing, filters nothing and
 * refetches nothing. See DatePicker's docstring for why that had to be built
 * rather than configured.
 *
 * `pill` picks the toolbar panel's large rounded control; without it the
 * controls match the compact header-funnel popover. Same component either way,
 * because the two panels drifting apart is how the header funnel ended up
 * offering operators the toolbar did not.
 */
export default function DateFilterEditor({ cond, onChange, pill }) {
  const d = cond.date || {};

  function setDate(next) { onChange({ ...cond, date: { ...d, ...next } }); }

  if (DATE_RANGE_OPS.includes(cond.op)) {
    const win = dateCondWindow(cond);
    return (
      <div className="dflt">
        <div className="dflt-pair">
          <DatePicker pill={pill} label="From date" placeholder="From…" value={d.from || ''}
            onChange={(v) => setDate({ mode: 'range', from: v })} />
          <span className="dflt-and">and</span>
          <DatePicker pill={pill} label="To date" placeholder="To…" value={d.to || ''}
            onChange={(v) => setDate({ mode: 'range', to: v })} />
        </div>
        {/* Both ends are INCLUSIVE, and a range picked end-first is read as the
            range the user drew. Spelling the result out is what makes both of
            those legible without the user having to test them. */}
        {win ? <div className="dflt-res">{fmtWindow(win)} · both days included</div> : null}
      </div>
    );
  }

  const bound = dateCondBound(cond);
  return (
    <div className="dflt">
      <DatePicker pill={pill} label={`${cond.op} date`} value={d.date || ''}
        onChange={(v) => setDate({ mode: 'exact', date: v })} />
      {/* Before and After are STRICT, so the picked day is in neither. Said out
          loud because the alternative is a user discovering it from a row count
          that is one day's worth of records short of what they expected. */}
      {(cond.op === 'Before' || cond.op === 'After') && bound
        ? <div className="dflt-res">the {dayOrdinal(bound)} itself is not included</div>
        : null}
    </div>
  );
}

// Day-of-month alone: the full date is already on the button directly above, and
// repeating it here reads as a second, differently-formatted value.
function dayOrdinal(isoDate) {
  const day = Number(String(isoDate).slice(8, 10));
  return Number.isFinite(day) && day > 0 ? `${day}${ordinal(day)}` : 'chosen day';
}

function ordinal(n) {
  if (n % 100 >= 11 && n % 100 <= 13) return 'th';
  return { 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] || 'th';
}
