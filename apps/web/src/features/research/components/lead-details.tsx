import type { LeadValue, LeadView } from '../types';
import { ProvenanceDot } from './provenance';

/**
 * Everything known about one lead, and where each piece came from (task 3.15, docs/09).
 *
 * A native `<details>` rather than a dialog. It needs no client JavaScript, it works before
 * hydration and with it off, and the keyboard and screen-reader behaviour is the browser's rather
 * than ours to get wrong. "Basic" is what the phase asked for and this is honestly that.
 *
 * The grid shows the handful of fields a person scans; this shows the rest, and it is the only
 * place the full provenance of a value is visible: which source, on what date, how confident, and
 * a link to the page it was read from. That link is the point. "Every value says where it came
 * from" is a claim, and a claim nobody can check is marketing.
 */
export function LeadDetails({ lead }: { lead: LeadView }) {
  const grouped = group(lead.values);
  const name = valueFor(lead.values, 'name');
  return (
    <details className="group">
      <summary className="hover:text-foreground cursor-pointer list-none font-medium">
        <span className="inline-flex items-center gap-1.5">
          {name ? <ProvenanceDot value={name} /> : null}
          <span>{lead.name}</span>
          <span aria-hidden className="text-muted-foreground text-xs group-open:hidden">
            ▸
          </span>
          <span aria-hidden className="text-muted-foreground hidden text-xs group-open:inline">
            ▾
          </span>
        </span>
        <span className="sr-only">
          {lead.values.length} field{lead.values.length === 1 ? '' : 's'}; expand for provenance
        </span>
      </summary>

      <div className="mt-3 space-y-4 pb-2">
        {grouped.map(([heading, values]) => (
          <section key={heading}>
            <h4 className="text-muted-foreground text-xs font-medium uppercase">{heading}</h4>
            <dl className="mt-1 space-y-1.5">
              {values.map((value) => (
                <div key={value.field} className="grid gap-x-3 sm:grid-cols-[10rem_1fr]">
                  <dt className="text-muted-foreground text-sm">{label(value.field)}</dt>
                  <dd className="text-sm">
                    <span className="inline-flex flex-wrap items-baseline gap-x-2">
                      <span className="inline-flex items-center gap-1.5">
                        <ProvenanceDot value={value} />
                        <span>{display(value.value)}</span>
                      </span>
                      <Evidence value={value} />
                    </span>
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
        {lead.values.length === 0 && (
          <p className="text-muted-foreground text-sm">
            Nothing stored for this business yet beyond what the search returned.
          </p>
        )}
      </div>
    </details>
  );
}

/**
 * The provenance, in words, with a link to the page the value was read from.
 *
 * Confidence is shown as a percentage because the number is the honest part: 0.85 from a maps
 * listing and 0.55 from a string matched in running text are different claims, and rounding both
 * to "verified" would hide exactly the difference the dot exists to show.
 */
function Evidence({ value }: { value: LeadValue }) {
  const when = value.observedAt.slice(0, 10);
  const detail = `${value.source} · ${when} · ${Math.round(value.confidence * 100)}%`;
  if (!value.sourceUrl) {
    return <span className="text-muted-foreground text-xs">{detail}</span>;
  }
  return (
    <a
      className="text-muted-foreground hover:text-foreground text-xs underline underline-offset-2"
      href={value.sourceUrl}
      rel="noreferrer noopener nofollow"
      target="_blank"
    >
      {detail}
    </a>
  );
}

/** Field groups, in the order someone working a lead reads them. */
const GROUPS: { heading: string; fields: string[] }[] = [
  { heading: 'Contact', fields: ['email', 'phone', 'phone_type', 'whatsapp', 'website'] },
  {
    heading: 'Location',
    fields: ['address', 'city', 'state', 'postal_code', 'country', 'geo'],
  },
  {
    heading: 'Business',
    fields: [
      'name',
      'category',
      'description',
      'opening_hours',
      'business_status',
      'rating',
      'review_count',
      'logo',
    ],
  },
  { heading: 'Profiles', fields: ['facebook', 'instagram', 'linkedin', 'x', 'youtube'] },
];

const LABELS: Record<string, string> = {
  email: 'Email',
  phone: 'Phone',
  phone_type: 'Line type',
  whatsapp: 'WhatsApp',
  website: 'Website',
  address: 'Address',
  city: 'City',
  state: 'State',
  postal_code: 'Postcode',
  country: 'Country',
  geo: 'Coordinates',
  name: 'Name',
  category: 'Category',
  description: 'Description',
  opening_hours: 'Opening hours',
  business_status: 'Status',
  rating: 'Rating',
  review_count: 'Reviews',
  logo: 'Logo',
  google_maps_url: 'Google Maps',
  facebook: 'Facebook',
  instagram: 'Instagram',
  linkedin: 'LinkedIn',
  x: 'X',
  youtube: 'YouTube',
};

function label(field: string): string {
  return LABELS[field] ?? field.replaceAll('_', ' ');
}

/**
 * Values by group, groups with nothing in them omitted, and anything we have no group for last.
 *
 * Unrecognised fields are shown rather than dropped: a value stored and then hidden is worse than
 * one shown under an ugly heading, because the second can be fixed by reading this list.
 */
function group(values: LeadValue[]): [string, LeadValue[]][] {
  const known = new Set(GROUPS.flatMap((g) => g.fields));
  const out: [string, LeadValue[]][] = [];
  for (const { heading, fields } of GROUPS) {
    const found = fields
      .map((field) => values.find((v) => v.field === field))
      .filter((v): v is LeadValue => v !== undefined);
    if (found.length > 0) out.push([heading, found]);
  }
  const rest = values.filter((v) => !known.has(v.field));
  if (rest.length > 0) out.push(['Other', rest]);
  return out;
}

function valueFor(values: LeadValue[], field: string): LeadValue | undefined {
  return values.find((v) => v.field === field);
}

function display(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value;
  if (typeof value === 'number') return value.toLocaleString('en-IN');
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (Array.isArray(value)) return value.map(display).join(' · ');
  if (typeof value === 'object') {
    const geo = value as { lat?: number; lng?: number };
    if (typeof geo.lat === 'number' && typeof geo.lng === 'number') {
      return `${geo.lat.toFixed(4)}, ${geo.lng.toFixed(4)}`;
    }
  }
  return JSON.stringify(value);
}
