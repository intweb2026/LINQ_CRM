import { useNavigate } from 'react-router-dom';
import { Icon } from '../lib/icons';
import { homeFor } from '../lib/nav';
import { useSession } from '../context/SessionContext';

/**
 * `reason` overrides the default explanation, which tells the user to ask an
 * administrator for the module under Roles. That sentence is right for every
 * module-gated page and wrong for the one page gated on an account instead of a
 * role: no administrator can grant Data API Keys, so pointing the user at Roles
 * would send them to ask for something nobody can give them.
 */
export default function NoAccessPage({ module, reason }) {
  const nav = useNavigate();
  const { canView, user } = useSession();
  // The way out has to be a page this user can actually open. homeFor() resolves
  // that against the live permission matrix, so the button cannot offer a module
  // this user just bounced off — and cannot offer Dashboard either, now that
  // Dashboard is itself gated.
  const home = homeFor(canView, user?.username);
  return (
    <div className="card">
      <div className="mt">
        <div className="mt-i"><Icon name="lock" size={21} /></div>
        <h3>No access to {module}</h3>
        <p>{reason || 'Your role does not include view permission for this module. Ask an administrator to grant access under Roles.'}</p>
        <button className="btn btn-p btn-sm" onClick={() => nav(home.path)}><Icon name={home.ic} size={13} /> Back to {home.label}</button>
      </div>
    </div>
  );
}
