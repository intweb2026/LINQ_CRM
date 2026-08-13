import { useNavigate } from 'react-router-dom';
import { Icon } from '../lib/icons';
import { useSession } from '../context/SessionContext';
import { homeFor } from '../lib/nav';

export default function NoAccessPage({ module }) {
  const nav = useNavigate();
  const { canView } = useSession();
  // The way out has to be a page this user can actually open. Hardcoding the
  // landing page would send a user who lacks Reports access — the only kind who
  // can see "No access to Reports" — straight back to the page they just bounced
  // off. homeFor() returns Dashboard for exactly those roles.
  const home = homeFor(canView);
  return (
    <div className="card">
      <div className="mt">
        <div className="mt-i"><Icon name="lock" size={21} /></div>
        <h3>No access to {module}</h3>
        <p>Your role does not include view permission for this module. Ask an administrator to grant access under Roles.</p>
        <button className="btn btn-p btn-sm" onClick={() => nav(home.path)}><Icon name={home.ic} size={13} /> Back to {home.label}</button>
      </div>
    </div>
  );
}
