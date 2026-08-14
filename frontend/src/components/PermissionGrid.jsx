import { CRM_MODULES, PERM_ACTIONS } from '../lib/constants';

/**
 * The module × view/create/update/delete checkbox grid.
 *
 * Used in two places that mean subtly different things by a tick, so both are
 * expressed through one component rather than two that drift:
 *
 *   Team grid   `inherited` omitted. A tick is simply on.
 *   User grid   `inherited` is the team's matrix. The checkbox shows the
 *               EFFECTIVE answer, and the cell is tinted to say where that
 *               answer came from — the team, an extra grant, or a revoke.
 *
 * Showing effective rather than "the team's, plus separate override controls" is
 * deliberate. The question being answered is "what can this person do", and a
 * reader should not have to combine two grids in their head to work it out.
 */
export default function PermissionGrid({ value, inherited, onToggle, disabled }) {
  const cellState = (module, action) => {
    const on = !!(value[module] || {})[action];
    if (!inherited) return on ? 'on' : 'off';
    const team = !!(inherited[module] || {})[action];
    if (on && !team) return 'granted';
    if (!on && team) return 'revoked';
    return on ? 'inherited' : 'off';
  };

  return (
    <table className="pm pm-grid">
      <thead>
        <tr>
          <th>Module</th>
          {PERM_ACTIONS.map((a) => <th key={a}>{a}</th>)}
        </tr>
      </thead>
      <tbody>
        {CRM_MODULES.map((mo) => (
          <tr key={mo.k}>
            <td>{mo.l}</td>
            {PERM_ACTIONS.map((a) => {
              const state = cellState(mo.k, a);
              return (
                <td key={a} className={'pm-c pm-' + state}>
                  <input
                    type="checkbox"
                    className="ck"
                    checked={!!(value[mo.k] || {})[a]}
                    disabled={disabled}
                    onChange={() => onToggle(mo.k, a)}
                    aria-label={`${mo.l} ${a}`}
                    title={inherited ? {
                      inherited: 'From the team',
                      granted: 'Given to this person on top of the team',
                      revoked: 'Taken away from this person; the team has it',
                      off: 'Not granted',
                    }[state] : undefined}
                  />
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** The colour key. Only meaningful where `inherited` is in play. */
export function PermissionLegend() {
  return (
    <div className="pm-key">
      <span><i className="pm-sw pm-inherited" />From the team</span>
      <span><i className="pm-sw pm-granted" />Added for this person</span>
      <span><i className="pm-sw pm-revoked" />Removed for this person</span>
    </div>
  );
}
