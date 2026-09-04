import { CRM_MODULES, PERM_ACTIONS, PERM_ACTION_LABEL } from '../lib/constants';

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
 *
 * THE LAST COLUMN IS NOT AN ACTION. view/create/update/delete say whether the
 * module opens; "All records" says which rows are inside it once it does —
 * only the ones the person's assigned events cover, or every one. It is how a
 * single person is given, say, every paper review without also being given
 * every booking, which is all an is_all_access team or the admin role can do.
 * It is inert on a module that was never row-scoped, and those cells render as
 * a dash rather than an empty box.
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
          {PERM_ACTIONS.map((a) => (
            <th key={a} className={a === 'all' ? 'pm-scope' : undefined}
                title={a === 'all'
                  ? 'Every row in the module, not only the ones this person’s assigned events cover.'
                  : undefined}>
              {PERM_ACTION_LABEL[a] || a}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {CRM_MODULES.map((mo) => (
          <tr key={mo.k}>
            <td>
              {mo.l}
              {/* Said in the row rather than only in a tooltip: an administrator
                  looking for why Performance Matrix will not grant needs the
                  answer where they are looking, not on hover. */}
              {mo.adminOnly ? <span className="dim" style={{ marginLeft: 6, fontSize: 11 }}>Admins only</span> : null}
            </td>
            {PERM_ACTIONS.map((a) => {
              // The scope cell only exists on a module whose rows are filtered
              // per person. Elsewhere it is shown as a dash, not as an empty
              // box: an empty box says "not shared yet" when the truth is
              // "nothing here was ever hidden".
              if (a === 'all' && !mo.scoped) {
                return (
                  <td key={a} className="pm-c pm-scope dim"
                      title="Everyone who can open this module already sees all of it.">
                    &mdash;
                  </td>
                );
              }
              const state = cellState(mo.k, a);
              // An adminOnly module is not a capability any role can hold — the
              // surface behind it checks for an administrator, not for this tick
              // — so the cell is shown locked rather than hidden. Hiding the row
              // would leave the grid one row short of the backend's module list
              // and read as "no such module".
              const locked = disabled || !!mo.adminOnly;
              return (
                <td key={a} className={'pm-c pm-' + state + (a === 'all' ? ' pm-scope' : '')}>
                  <input
                    type="checkbox"
                    className="ck"
                    checked={!!(value[mo.k] || {})[a]}
                    disabled={locked}
                    onChange={() => { if (!mo.adminOnly) onToggle(mo.k, a); }}
                    aria-label={`${mo.l} ${PERM_ACTION_LABEL[a] || a}`}
                    title={mo.adminOnly
                      ? 'Restricted to administrators; it cannot be granted to a role.'
                      : a === 'all' && !inherited
                        ? `Show every ${mo.l.toLowerCase()} record, not only the ones the assigned events cover.`
                      : inherited ? {
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
