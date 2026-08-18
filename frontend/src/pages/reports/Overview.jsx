import { useState } from 'react';
import { Icon } from '../../lib/icons';
import { Who, RoleBadge } from '../../components/Badge';
import { nf, plur } from '../../lib/helpers';
import { ROLE_FULL } from '../../lib/constants';
import * as reportsApi from '../../api/reports';
import { useFetch } from '../../hooks/useFetch';

export default function ReportsOverview() {
  const { data } = useFetch(reportsApi.overview, [], { initialData: { booking_team_productivity: [] } });
  const S = data || { booking_team_productivity: [] };
  const [open, setOpen] = useState(new Set());
  const toggle = (id) => setOpen((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });

  return (
    <>
      <div className="sl">Team productivity report — booking pipelines</div>
      {S.booking_team_productivity.map((t) => {
        const lab = t.team_type === 'spex' ? ['Sponsors booked', 'Sponsors paid'] : t.team_type === 'speaker_sales' ? ['Speakers', 'Speakers paid'] : ['Bookings', 'Paid bookings'];
        const tag = t.team_type === 'spex' ? 'SpEx' : t.team_type === 'speaker_sales' ? 'Speaker Sales' : ROLE_FULL[t.team_type] || 'Sales';
        const isOpen = open.has(t.team_id);
        return (
          <div className="ac" key={t.team_id}>
            <div className="ac-h" onClick={() => toggle(t.team_id)}>
              <span className="ac-i" style={{ background: t.color + '14', color: t.color }}><Icon name="team" size={15} /></span>
              <span className="ac-t"><span className="n">{t.team_name}<span className="tg bg-neutral">{tag}</span></span><span className="s">{plur(t.members.length, 'member')}</span></span>
              <span className={'ac-a' + (isOpen ? ' op' : '')}><Icon name="chevD" size={16} /></span>
            </div>
            <div className={'ac-b' + (isOpen ? ' op' : '')}>
              {!t.members.length ? <div className="mt" style={{ padding: 22 }}><p style={{ margin: 0 }}>No members assigned.</p></div> : (
                <table className="gt">
                  <thead><tr><th>Member</th><th>Role</th><th className="num">{lab[0]}</th><th className="num">{lab[1]}</th><th className="num">Conversion</th></tr></thead>
                  <tbody>
                    {t.members.slice().sort((a, b) => b.bookings - a.bookings).map((mb) => {
                      const tn = mb.conv >= 70 ? 'var(--green)' : mb.conv >= 50 ? 'var(--t-500)' : 'var(--amber)';
                      return (
                        <tr key={mb.user_id}>
                          <td><Who name={mb.name} avatar={false} /></td><td><RoleBadge value={mb.role} /></td>
                          <td className="num" style={{ fontWeight: 650 }}>{nf(mb.bookings)}</td>
                          <td className="num" style={{ color: 'var(--green)', fontWeight: 650 }}>{nf(mb.paid)}</td>
                          <td className="num"><span className="cv"><span>{mb.conv}%</span><span className="cv-b"><i style={{ width: mb.conv + '%', background: tn }} /></span></span></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        );
      })}
    </>
  );
}
