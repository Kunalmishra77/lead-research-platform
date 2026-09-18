import type { MembershipRole } from '../../common/rbac/permissions';

/** Verified identity from a Supabase access token. */
export interface AuthUser {
  userId: string;
  /** From the token; display only. Never use it for invite matching or authorization. */
  email: string | null;
  /** Supabase session (auth.sessions.id); checked for revocation by TenantGuard. */
  sessionId: string;
}

/** Resolved by TenantGuard from X-Workspace-Id + the caller's membership. */
export interface TenantInfo {
  orgId: string;
  workspaceId: string;
  role: MembershipRole;
}

declare module 'fastify' {
  interface FastifyRequest {
    auth?: AuthUser;
    tenant?: TenantInfo;
  }
}
