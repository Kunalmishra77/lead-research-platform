export const WORKSPACE_COOKIE = 'lf_ws';

/** One place for the active-workspace cookie flags; `secure` follows the public APP_URL scheme. */
export function workspaceCookieOptions(appUrl: string) {
  return {
    httpOnly: true,
    sameSite: 'lax' as const,
    secure: new URL(appUrl).protocol === 'https:',
    path: '/',
    maxAge: 60 * 60 * 24 * 365,
  };
}
