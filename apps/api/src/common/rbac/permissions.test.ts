import { describe, expect, it } from 'vitest';

import { hasPermission, MEMBERSHIP_ROLES, type Permission, PERMISSIONS } from './permissions';

/** docs/05 "Auth and RBAC" table, row by row (Y = allowed). */
const EXPECTED: Record<Permission, [boolean, boolean, boolean, boolean, boolean]> = {
  //                    owner admin manager member viewer
  'research.run': [true, true, true, true, false],
  'contacts.view': [true, true, true, true, true],
  'exports.create': [true, true, true, true, false],
  'lists.manage': [true, true, true, true, false],
  'integrations.manage': [true, true, false, false, false],
  'billing.manage': [true, true, false, false, false],
  'team.manage': [true, true, false, false, false],
  'workspace.manage': [true, true, true, false, false],
};

describe('RBAC matrix', () => {
  it('covers every permission', () => {
    expect(Object.keys(EXPECTED).sort()).toEqual([...PERMISSIONS].sort());
  });

  it.each(Object.entries(EXPECTED))('%s matches docs/05', (permission, allowed) => {
    MEMBERSHIP_ROLES.forEach((role, i) => {
      expect(hasPermission(role, permission as Permission), `${role} / ${permission}`).toBe(
        allowed[i],
      );
    });
  });
});
