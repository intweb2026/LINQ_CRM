/**
 * The role a team's NAME implies.
 *
 * A MIRROR of backend/accounts/models.py TEAM_NAME_ROLE_KEYWORDS, which is what
 * User.save() applies when someone is placed in a team. It exists on this side
 * so the Add/Edit user form can fill the Role field in the moment a team is
 * chosen, rather than letting the user pick a role, save, and discover the
 * server had a different opinion.
 *
 * ORDER IS THE BEHAVIOUR. The first keyword found in the name wins, so
 * "Telesales" resolves to Telemarketing through 'tele' before 'sales' is
 * considered, and "Speaker Sales Ops" resolves to Operations through 'ops'.
 * Reordering these rows makes this copy disagree with the server's.
 *
 * Two copies of a rule drift; that is what copies do. So they are not trusted to
 * stay in step by inspection — accounts/tests_wire_probe.py loads THIS file
 * under Node, runs both implementations over the same team names, and fails if
 * any answer differs. Change one, and the test tells you to change the other.
 */
export const TEAM_NAME_ROLE_KEYWORDS = [
  ['admin', 'admin'],
  ['market research', 'market_research'],
  ['data mining', 'data_mining'],
  ['dmd', 'data_mining'],
  ['spex', 'spex'],
  ['operation', 'operations'],
  ['ops', 'operations'],
  ['speaker sales', 'speaker_sales'],
  ['telemarketing', 'telemarketing'],
  ['tele marketing', 'telemarketing'],
  ['tele', 'telemarketing'],
  ['sales', 'sales'],
];

/** The role `teamName` implies, or null when no keyword matches. */
export function roleFromTeamName(teamName) {
  const haystack = String(teamName || '').toLowerCase().trim();
  if (!haystack) return null;
  for (const [keyword, role] of TEAM_NAME_ROLE_KEYWORDS) {
    if (haystack.includes(keyword)) return role;
  }
  return null;
}
