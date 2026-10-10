import { kindOf } from '../provenance-kind';
import type { LeadValue, LeadView } from '../types';

/**
 * A lead's social profiles, as links (task 3.15, docs/08 row 50).
 *
 * Handles are stored, not crawled: `docs/08` allows the URL and the handle and nothing inside the
 * platform. So this builds the link from the handle rather than storing a URL we never visited,
 * which also means a handle that arrived from `sameAs` and one scraped from a footer render the
 * same way and are told apart by their dot.
 *
 * Two letters rather than brand icons. A brand icon implies an endorsement and needs a licence to
 * use; initials need neither and survive greyscale, which the provenance dots also depend on.
 */
export function SocialsCell({ lead }: { lead: LeadView }) {
  const found = PLATFORMS.map((platform) => {
    const value = lead.values.find((v) => v.field === platform.field);
    return value ? { platform, value } : null;
  }).filter((entry): entry is { platform: Platform; value: LeadValue } => entry !== null);

  if (found.length === 0) return <span className="text-muted-foreground">—</span>;

  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {found.map(({ platform, value }) => {
        const handle = String(value.value);
        const kind = kindOf(value);
        return (
          <a
            key={platform.field}
            className="border-border hover:border-foreground/40 hover:text-foreground inline-flex size-6 items-center justify-center rounded border text-[10px] font-medium uppercase"
            href={platform.href(handle)}
            rel="noreferrer noopener nofollow"
            target="_blank"
            title={`${platform.name}: ${handle} · ${kind === 'found' ? 'found in a source' : kind} · ${value.source}`}
          >
            <span aria-hidden>{platform.initials}</span>
            <span className="sr-only">
              {platform.name}: {handle}
            </span>
          </a>
        );
      })}
    </span>
  );
}

interface Platform {
  field: string;
  name: string;
  initials: string;
  href: (handle: string) => string;
}

/**
 * The link each handle becomes. LinkedIn's handle keeps its `company/` or `in/` segment because
 * the two are different kinds of page and the prefix is not guessable from the name alone;
 * WhatsApp's is a phone number, so it becomes a `wa.me` link.
 */
const PLATFORMS: Platform[] = [
  {
    field: 'instagram',
    name: 'Instagram',
    initials: 'ig',
    href: (h) => `https://instagram.com/${h}`,
  },
  {
    field: 'facebook',
    name: 'Facebook',
    initials: 'fb',
    href: (h) => `https://facebook.com/${h}`,
  },
  {
    field: 'linkedin',
    name: 'LinkedIn',
    initials: 'in',
    href: (h) => `https://linkedin.com/${h}`,
  },
  { field: 'x', name: 'X', initials: 'x', href: (h) => `https://x.com/${h}` },
  {
    field: 'youtube',
    name: 'YouTube',
    initials: 'yt',
    href: (h) => `https://youtube.com/${h}`,
  },
  {
    field: 'whatsapp',
    name: 'WhatsApp',
    initials: 'wa',
    href: (h) => `https://wa.me/${h.replace(/\D/g, '')}`,
  },
];
