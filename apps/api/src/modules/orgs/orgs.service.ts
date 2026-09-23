import {
  eq,
  memberships,
  organizations,
  sql,
  userProfiles,
  withUser,
  workspaces,
} from '@leadforge/db';
import { Inject, Injectable } from '@nestjs/common';
import { uuidv7 } from 'uuidv7';

import { pgCode } from '../../common/db/pg-error';
import { AppError } from '../../common/errors/app-error';
import { type Database, DB } from '../../infra/db/db.module';
import type { AuthUser } from '../auth/auth.types';
import type { CreatedOrg, MeResponse } from './orgs.dto';
import { slugFromName } from './slug';

const DEFAULT_WORKSPACE_NAME = 'Default';

@Injectable()
export class OrgsService {
  constructor(@Inject(DB) private readonly db: Database) {}

  /** Creates org + default workspace + owner membership through app.bootstrap_org (audited). */
  async create(user: AuthUser, name: string, slug?: string): Promise<CreatedOrg> {
    const ids = {
      org: uuidv7(),
      workspace: uuidv7(),
      membership: uuidv7(),
      audit: uuidv7(),
      grant: uuidv7(),
    };
    const finalSlug = slug ?? slugFromName(name);
    try {
      await withUser(this.db, user.userId, (tx) =>
        tx.execute(sql`select app.bootstrap_org(
          ${user.userId}::uuid, ${ids.org}::uuid, ${name}, ${finalSlug},
          ${ids.workspace}::uuid, ${DEFAULT_WORKSPACE_NAME}, ${ids.membership}::uuid, ${ids.audit}::uuid,
          ${ids.grant}::uuid)`),
      );
    } catch (err) {
      throw mapBootstrapError(err);
    }
    return {
      org: { id: ids.org, name, slug: finalSlug },
      workspace: { id: ids.workspace, name: DEFAULT_WORKSPACE_NAME },
      role: 'owner',
    };
  }

  /** The caller's profile and memberships (no active org needed). */
  async me(user: AuthUser): Promise<MeResponse> {
    return withUser(this.db, user.userId, async (tx) => {
      const [profile] = await tx
        .select({ fullName: userProfiles.fullName, isPlatformStaff: userProfiles.isPlatformStaff })
        .from(userProfiles)
        .where(eq(userProfiles.userId, user.userId));
      const rows = await tx
        .select({
          orgId: organizations.id,
          orgName: organizations.name,
          orgSlug: organizations.slug,
          workspaceId: workspaces.id,
          workspaceName: workspaces.name,
          role: memberships.role,
        })
        .from(memberships)
        .innerJoin(organizations, eq(organizations.id, memberships.orgId))
        .innerJoin(workspaces, eq(workspaces.id, memberships.workspaceId))
        .where(eq(memberships.userId, user.userId))
        .orderBy(organizations.name, workspaces.name);
      return {
        user: {
          id: user.userId,
          email: user.email,
          fullName: profile?.fullName ?? null,
          isPlatformStaff: profile?.isPlatformStaff ?? false,
        },
        memberships: rows,
      };
    });
  }
}

function mapBootstrapError(err: unknown): unknown {
  switch (pgCode(err)) {
    case '23505':
      return new AppError({
        code: 'org.slug_taken',
        httpStatus: 409,
        title: 'That organization URL is already taken',
        errorClass: 'invalid_input',
      });
    case '23514':
      return new AppError({
        code: 'org.invalid',
        httpStatus: 422,
        title: 'Organization could not be created',
        detail: 'The name or URL is invalid',
        errorClass: 'invalid_input',
      });
    // Custom SQLSTATEs raised by app.bootstrap_org (db/migrations/0005_auth_session_checks.sql).
    case 'LF001':
      return new AppError({
        code: 'auth.email_not_confirmed',
        httpStatus: 403,
        title: 'Confirm your email address first',
      });
    case 'LF002':
      return new AppError({
        code: 'auth.user_blocked',
        httpStatus: 403,
        title: 'This account cannot create organizations',
      });
    case 'LF003':
      return new AppError({
        code: 'org.limit_reached',
        httpStatus: 422,
        title: 'Organization limit reached',
        errorClass: 'invalid_input',
      });
    case '42501':
      return new AppError({ code: 'auth.forbidden', httpStatus: 403, title: 'Not allowed' });
    default:
      return err;
  }
}
