// Appointing somebody manager of a team is recorded on the PERSON
// (`managed_team`), not on the team, so /api/teams/ knows nothing about it. That
// is why a team whose manager was set BEFORE anyone was added to it offered the
// new member the team's leads, of which there were none yet, and the
// administrators, and never the manager who had just been given the team to run.
import { managerOptionGroups, reportingManagerOf } from './reporting';

const SALES = { id: 7, name: 'Sales Team', team_lead_id: null, team_lead_name: null, team_leads: [] };
// The manager sits in Admin and runs Sales; `managed_team` is not `team`.
const MANAGER = { id: 1, name: 'Dana Reyes', status: 'active', role: 'sales', team_id: 2, managed_team_id: 7 };
const ADMIN = { id: 2, name: 'Harrison Peck', status: 'active', role: 'admin', team_id: 2 };
const NEWBIE = { id: 3, name: 'Sam Okafor', status: 'active', role: 'sales', team_id: 7 };
const USERS = [MANAGER, ADMIN, NEWBIE];

test('the team manager is offered to a member of the team they manage', () => {
  const groups = managerOptionGroups(SALES, NEWBIE, USERS);
  const labels = groups.map((g) => g.label);
  expect(labels).toContain('Sales Team manager');
  const names = groups.flatMap((g) => g.items).map((i) => i.name);
  expect(names).toContain('Dana Reyes');
  expect(names).toContain('Harrison Peck');
});

test('an unrecorded member of a lead-less team falls back to its manager, not the admins', () => {
  const got = reportingManagerOf(NEWBIE, [SALES], USERS);
  expect(got).toEqual({ names: ['Dana Reyes'], source: 'manager', team: 'Sales Team' });
});

test("the team's own leads still win over its manager, and nobody reports to themselves", () => {
  const led = { ...SALES, team_lead_id: 4, team_lead_name: 'Terry Tamayo', team_leads: [{ id: 4, name: 'Terry Tamayo' }] };
  expect(reportingManagerOf(NEWBIE, [led], USERS).source).toBe('team');
  const own = managerOptionGroups(SALES, MANAGER, USERS).flatMap((g) => g.items).map((i) => i.id);
  expect(own).not.toContain(MANAGER.id);
});
