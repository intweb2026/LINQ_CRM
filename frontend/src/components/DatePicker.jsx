import { useEffect, useRef, useState } from 'react';
import { Icon } from '../lib/icons';
import {
  WEEKDAY_INITIALS, WEEKDAY_NAMES, fmtISO, inMonth, isCompleteDate, monthGrid,
  monthLabel, monthOf, shiftMonth, todayISO,
} from '../lib/dateFilter';

/**
 * One date, picked from a calendar this app draws.
 *
 * WHY NOT `<input type="date">`
 * Because its calendar is a browser-native popup, drawn outside the page, that
 * we can neither position nor coordinate with. Two bugs came out of that pairing
 * and neither had a fix on our side of the boundary. It reported a complete
 * value after every keystroke in the year segment, so typing 2026 committed the
 * years 2, 20 and 202 on the way past. And because the filter panel re-renders
 * whenever a condition changes, React wrote `input.value` back to a node whose
 * picker was open, which Chrome answers by resetting the picker to that value —
 * so moving to another month looked like the CRM selecting a date by itself and
 * applying the filter.
 *
 * THE RULE THIS COMPONENT IS BUILT AROUND
 * `onChange` fires in exactly one place: the click handler on a day cell.
 * Navigating months and years moves `view`, which is local state that nothing
 * outside this component can see. There is no code path from a navigation
 * control to a value, so no amount of clicking around the calendar can select a
 * date or move the filter.
 */
export default function DatePicker({ value, onChange, pill, label, placeholder = 'Select a date…' }) {
  const [open, setOpen] = useState(false);
  // The month on screen. Deliberately NOT derived from `value` on every render:
  // that is what would snap the calendar back to the selected month the moment
  // anything else re-rendered the panel, mid-navigation.
  const [view, setView] = useState(() => monthOf(value));
  const ref = useRef(null);
  const popRef = useRef(null);

  /**
   * The toolbar's filter list is a scroll container (`.pop-mx`), so a calendar
   * opened on a field near the bottom would be cut off by it. Nudging the
   * container is enough; the alternative — portalling the calendar to the body
   * and tracking the field's position — is the arrangement that made the native
   * picker unusable in the first place.
   *
   * `block: 'nearest'` scrolls the minimum needed and does nothing when the
   * calendar already fits, so opening one in a short panel does not jump the
   * page. Popover's own scroll listener sees this, but its position memo turns
   * a scroll that did not move the anchor into no re-render at all.
   */
  useEffect(() => {
    if (!open || !popRef.current) return;
    popRef.current.scrollIntoView({ block: 'nearest' });
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    function onDown(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false); }
    function onKey(e) { if (e.key === 'Escape') { e.stopPropagation(); setOpen(false); } }
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  function toggle() {
    // Opening lands on the selected date's month, or on this one when the field
    // is empty. Done here rather than in an effect so that re-opening re-centres
    // the calendar while navigating never does.
    if (!open) setView(monthOf(value));
    setOpen((o) => !o);
  }

  const today = todayISO();
  const cells = monthGrid(view);
  const selected = isCompleteDate(value) ? value : null;

  return (
    <div className="dpk" ref={ref}>
      <button type="button" className={`${pill ? 'flt-pill' : 'in in-xs'} dpk-btn${selected ? '' : ' dpk-empty'}`}
        onClick={toggle} aria-haspopup="dialog" aria-expanded={open} aria-label={label}>
        <span>{selected ? fmtISO(selected) : placeholder}</span>
        <Icon name="calendar" size={14} />
      </button>

      {open ? (
        <div className="dpk-pop" ref={popRef} role="dialog" aria-label={label || 'Choose a date'}>
          {/* Navigation only. None of these four buttons can reach onChange. */}
          <div className="dpk-nav">
            <button type="button" className="dpk-nb" onClick={() => setView(shiftMonth(view, 0, -1))} aria-label="Previous year" title="Previous year">
              <Icon name="chevL" size={13} /><Icon name="chevL" size={13} />
            </button>
            <button type="button" className="dpk-nb" onClick={() => setView(shiftMonth(view, -1, 0))} aria-label="Previous month" title="Previous month">
              <Icon name="chevL" size={14} />
            </button>
            <span className="dpk-mn" aria-live="polite">{monthLabel(view)}</span>
            <button type="button" className="dpk-nb" onClick={() => setView(shiftMonth(view, 1, 0))} aria-label="Next month" title="Next month">
              <Icon name="chevR" size={14} />
            </button>
            <button type="button" className="dpk-nb" onClick={() => setView(shiftMonth(view, 0, 1))} aria-label="Next year" title="Next year">
              <Icon name="chevR" size={13} /><Icon name="chevR" size={13} />
            </button>
          </div>

          <div className="dpk-wd" aria-hidden="true">
            {WEEKDAY_INITIALS.map((d, i) => <span key={WEEKDAY_NAMES[i]}>{d}</span>)}
          </div>

          <div className="dpk-grid">
            {cells.map((d) => {
              const out = !inMonth(d, view);
              const cls = ['dpk-d'];
              if (out) cls.push('out');
              if (d === selected) cls.push('on');
              else if (d === today) cls.push('now');
              return (
                <button type="button" key={d} className={cls.join(' ')} aria-label={fmtISO(d)}
                  aria-current={d === selected ? 'date' : undefined}
                  onClick={() => { onChange(d); setOpen(false); }}>
                  {Number(d.slice(8, 10))}
                </button>
              );
            })}
          </div>

          <div className="dpk-foot">
            {/* Moves the VIEW. Named for what it does, because a button in a
                calendar that reads "Today" and silently picks today's date is
                exactly the surprise this component exists to remove. */}
            <button type="button" className="dpk-fb" onClick={() => setView(todayISO().slice(0, 7))}>
              Go to this month
            </button>
            <button type="button" className="dpk-fb dpk-clear" disabled={!selected}
              onClick={() => { onChange(''); setOpen(false); }}>
              Clear
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
