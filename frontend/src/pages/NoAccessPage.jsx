import { useNavigate } from 'react-router-dom';
import { Icon } from '../lib/icons';

export default function NoAccessPage({ module }) {
  const nav = useNavigate();
  return (
    <div className="card">
      <div className="mt">
        <div className="mt-i"><Icon name="lock" size={21} /></div>
        <h3>No access to {module}</h3>
        <p>Your role does not include view permission for this module. Ask an administrator to grant access under Roles.</p>
        <button className="btn btn-p btn-sm" onClick={() => nav('/dashboard')}><Icon name="grid" size={13} /> Back to Dashboard</button>
      </div>
    </div>
  );
}
