/** RBAC matrix from docs/05 "Auth and RBAC". Role applies per workspace membership. */
export const MEMBERSHIP_ROLES = ['owner', 'admin', 'manager', 'member', 'viewer'] as const;
export type MembershipRole = (typeof MEMBERSHIP_ROLES)[number];

export const PERMISSIONS = [
  'research.run', // run research / spend credits (member: within user cap, enforced later)
  'contacts.view',
  'exports.create', // member: row cap, enforced later
  'lists.manage', // lists, tags, notes
  'integrations.manage', // integrations, API keys
  'billing.manage',
  'team.manage',
  'workspace.manage', // workspace settings, scoring models
] as const;
export type Permission = (typeof PERMISSIONS)[number];

const MEMBER: readonly Permission[] = [
  'research.run',
  'contacts.view',
  'exports.create',
  'lists.manage',
];

const MATRIX: Record<MembershipRole, readonly Permission[]> = {
  owner: PERMISSIONS,
  admin: PERMISSIONS,
  manager: [...MEMBER, 'workspace.manage'],
  member: MEMBER,
  viewer: ['contacts.view'],
};

export function hasPermission(role: MembershipRole, permission: Permission): boolean {
  return MATRIX[role].includes(permission);
}
