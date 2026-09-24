/**
 * The credits we are required to show, not decoration.
 *
 * Google Places content must carry visible, unmodified Google attribution wherever it is shown
 * (ADR-0011, docs/09). Seeded geography is OpenStreetMap under ODbL and must be credited wherever
 * it is displayed (docs/08). Both are conditions of using the data at all, so these render from
 * what is actually on the page rather than being sprinkled by hand: `SourceAttribution` takes the
 * source keys a result set reported and shows exactly the ones that apply.
 */

const OSM_TEXT = 'Data © OpenStreetMap contributors, ODbL 1.0';
const OSM_HREF = 'https://www.openstreetmap.org/copyright';

/** Attribution for every source that contributed a value on screen. */
export function SourceAttribution({
  sources,
  geography = false,
  className,
}: {
  /** `app.sources.key` values, as the results endpoint reports them. */
  sources: readonly string[];
  /** True when seeded geography (a city, a bounding box) is shown as well. */
  geography?: boolean;
  className?: string;
}) {
  const showsGoogle = sources.includes('google_places');
  if (!showsGoogle && !geography) return null;

  return (
    <p className={`text-muted-foreground text-xs ${className ?? ''}`}>
      {showsGoogle && <span>Business data from Google</span>}
      {showsGoogle && geography && <span aria-hidden> · </span>}
      {geography && (
        <span>
          <a
            className="hover:text-foreground underline underline-offset-2"
            href={OSM_HREF}
            rel="noreferrer noopener"
            target="_blank"
          >
            {OSM_TEXT}
          </a>
        </span>
      )}
    </p>
  );
}

/**
 * The geography credit on its own, for places where we show a seeded city or state but no
 * business data yet — the location chips on the New Research screen, for instance (docs/08).
 */
export function GeographyAttribution({ className }: { className?: string }) {
  return <SourceAttribution sources={[]} geography className={className} />;
}
