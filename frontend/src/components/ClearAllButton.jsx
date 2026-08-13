import { Icon } from '../lib/icons';
import { nf } from '../lib/helpers';
import { useSession } from '../context/SessionContext';
import { useConfirm } from '../context/ConfirmContext';
import { useToast } from '../context/ToastContext';
import { apiErrorMessage } from '../api/client';

/**
 * The HP account name, as the UI knows it.
 *
 * Mirrors accounts/permissions.py HP_USERNAME. Declared here once, for the same
 * reason it is declared once there: five pages carry this button, and five copies
 * of a username literal is five chances for one to be typed wrong — which would
 * either hide the control from the only person who may use it, or show it to
 * someone whose click can only ever answer 403.
 */
export const HP_USERNAME = 'HP';

/**
 * "Clear all data" for one module — renders NOTHING for anyone but HP.
 *
 * WHY THIS IS A COMPONENT AND NOT FIVE BUTTONS
 * The action is irreversible and destroys a whole module, and it is the one control
 * in the CRM whose audience is a single named account — not admins, not is_all_access
 * roles, not superusers. Repeating that gate per page means the day one page is
 * copied from another and the check is left out, the button appears for everybody.
 * Here it is one `if`, in front of everything else the component can do.
 *
 * The server gate is the real one (accounts.permissions.IsHPAccount). This exists so
 * that the people who cannot use it never see it.
 *
 * Props:
 *   noun     what is being destroyed, plural — "bookings", "events", "tickets"
 *   count    how many records, for the confirmation. Optional; omitted when unknown.
 *   onClear  () => Promise — the API call. Rejections are shown, not swallowed.
 *   onCleared called after a successful wipe, to refetch the now-empty table.
 *   extra    anything else the confirmation must say about collateral damage.
 */
export default function ClearAllButton({ noun, count, onClear, onCleared, extra }) {
  const { user } = useSession();
  const confirm = useConfirm();
  const toast = useToast();

  if (user?.username !== HP_USERNAME) return null;

  async function run() {
    const ok = await confirm({
      title: `Clear ALL ${noun} data?`,
      sub: `Every ${noun.replace(/s$/, '')} record will be destroyed.`,
      danger: true,
      ok: 'Clear everything',
      // A typed confirmation, not just a click. This is the one action in the CRM
      // with no undo and no partial form — the same guard the bookings wipe used.
      typed: 'CLEAR',
      body: (
        <div>
          <div className="vr er">
            <Icon name="warn" size={15} />
            <span>
              This wipes {count == null ? 'every' : <b>{nf(count)}</b>} {noun} record
              {count === 1 ? '' : 's'} and cannot be undone.
            </span>
          </div>
          {extra ? <p style={{ fontSize: 12.5, color: 'var(--text-3)', lineHeight: 1.55 }}>{extra}</p> : null}
        </div>
      ),
    });
    if (!ok) return;
    try {
      await onClear();
    } catch (err) {
      // The server's own words — "This action is restricted to the HP account."
      // reads very differently from a generic failure, and the difference matters
      // when a button that should be invisible has somehow been clicked.
      toast(apiErrorMessage(err, `Could not clear ${noun} data`), 'er');
      return;
    }
    onCleared && onCleared();
    toast(`All ${noun} data cleared`, 'ok');
  }

  return (
    <button className="btn btn-do btn-ic" title={`Clear all ${noun} data`} onClick={run}>
      <Icon name="trash" size={15} />
    </button>
  );
}
