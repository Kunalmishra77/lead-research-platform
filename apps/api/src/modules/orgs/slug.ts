import { randomBytes } from 'node:crypto';

import { SLUG_PATTERN } from './orgs.dto';

/** URL slug from an org name plus a short random suffix (uniqueness is enforced by the DB). */
export function slugFromName(name: string): string {
  const base = name
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40)
    .replace(/-+$/g, '');
  const suffix = randomBytes(3).toString('hex');
  const slug = base.length >= 2 ? `${base}-${suffix}` : `org-${suffix}`;
  return SLUG_PATTERN.test(slug) ? slug : `org-${suffix}`;
}
