/**
 * The licence notice a CSV carries, when the leads in it came from a dataset that asks for one.
 *
 * Overture Places is CDLA-Permissive-2.0, whose only real obligation is section 2.1: share the
 * Data and the text of the agreement has to be available *with* it. A link on our own site is
 * not with a file somebody downloaded, so the text travels in the file.
 *
 * It goes after the data, behind a blank line, one line per cell. In a spreadsheet -- which is
 * where these are opened -- it reads as a block of notes under the table, clearly not part of
 * it. A CRM import sees rows with one column and somebody deletes them, which is the cost of
 * keeping this a CSV rather than a zip.
 *
 * Only the sources actually present get a notice. A workspace exporting leads it found by
 * crawling companies' own sites owes nobody anything, and a licence block on that file would be
 * noise that teaches people to ignore licence blocks.
 *
 * The text is a constant rather than a file read at runtime. An export must not be able to fail
 * because a path resolved differently inside a container, and a licence that only ships when
 * somebody remembers a COPY line is a licence that will one day not ship. `export-notice.test.ts`
 * holds it to `docs/licenses/CDLA-Permissive-2.0.md`, so the copy cannot drift.
 */

/** Verbatim from docs/licenses/CDLA-Permissive-2.0.md; a test fails if the two disagree. */
export const CDLA_PERMISSIVE_2_0 = `Community Data License Agreement - Permissive - Version 2.0

This is the Community Data License Agreement - Permissive, Version 2.0 (the "agreement"). Data Provider(s) and Data Recipient(s) agree as follows:

1. Provision of the Data

1.1. A Data Recipient may use, modify, and share the Data made available by Data Provider(s) under this agreement if that Data Recipient follows the terms of this agreement.

1.2. This agreement does not impose any restriction on a Data Recipient's use, modification, or sharing of any portions of the Data that are in the public domain or that may be used, modified, or shared under any other legal exception or limitation.

2. Conditions for Sharing Data

2.1. A Data Recipient may share Data, with or without modifications, so long as the Data Recipient makes available the text of this agreement with the shared Data.

3. No Restrictions on Results

3.1. This agreement does not impose any restriction or obligations with respect to the use, modification, or sharing of Results.

4. No Warranty; Limitation of Liability

4.1. All Data Recipients receive the Data subject to the following terms:

THE DATA IS PROVIDED ON AN "AS IS" BASIS, WITHOUT REPRESENTATIONS, WARRANTIES OR CONDITIONS OF ANY KIND, EITHER EXPRESS OR IMPLIED INCLUDING, WITHOUT LIMITATION, ANY WARRANTIES OR CONDITIONS OF TITLE, NON-INFRINGEMENT, MERCHANTABILITY OR FITNESS FOR A PARTICULAR PURPOSE.

NO DATA PROVIDER SHALL HAVE ANY LIABILITY FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING WITHOUT LIMITATION LOST PROFITS), HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE DATA OR RESULTS, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.

5. Definitions

5.1. "Data" means the material received by a Data Recipient under this agreement.

5.2. "Data Provider" means any person who is the source of Data provided under this agreement and in reliance on a Data Recipient's agreement to its terms.

5.3. "Data Recipient" means any person who receives Data directly or indirectly from a Data Provider and agrees to the terms of this agreement.

5.4. "Results" means any outcome obtained by computational analysis of Data, including for example machine learning models and models' insights.`;

/** What each source obliges us to say, and whether its full text has to come too. */
interface Obligation {
  /** The line naming the source and its licence. */
  attribution: string;
  /** Set where the licence requires its own text to travel with the data. */
  text?: string;
}

const OBLIGATIONS: Record<string, Obligation> = {
  // CDLA-Permissive-2.0 asks for no attribution at all; the line is ours, so that a reader
  // knows what the wall of text underneath is about.
  overture: {
    attribution:
      'Place data from Overture Maps, used under the Community Data License Agreement - ' +
      'Permissive, Version 2.0. The full text of that agreement is included below.',
    text: CDLA_PERMISSIVE_2_0,
  },
  // ODbL 1.0 requires attribution. It does not require its text to travel.
  osm: {
    attribution: 'Place data © OpenStreetMap contributors, ODbL 1.0.',
  },
  // Google's terms govern caching and display rather than a notice, but naming the source is
  // what makes a disputed value checkable (ADR-0011).
  google_places: {
    attribution: 'Some place data from Google Maps.',
  },
  // Our own crawler, reading each business's own website. Nobody to credit.
  website: { attribution: "Contact details read from each business's own website." },
};

/** Sources that oblige us to say something. Anything else exports with no notice at all. */
export const LICENSED_SOURCES: readonly string[] = Object.keys(OBLIGATIONS);

/**
 * The notice rows for an export, or none when nothing in it carries an obligation.
 *
 * Driven by the sources the leads actually have, so it follows the data rather than a list
 * somebody has to remember to update when a connector is added.
 */
export function noticeLines(sources: Iterable<string>): string[] {
  const present = [...new Set(sources)]
    .map((source) => OBLIGATIONS[source])
    .filter((obligation): obligation is Obligation => obligation !== undefined)
    .sort((a, b) => (a.attribution < b.attribution ? -1 : 1));
  if (present.length === 0) return [];

  // A blank row first, so a spreadsheet shows this as a separate block rather than as more data.
  const lines = ['', 'Sources and licences'];
  for (const obligation of present) {
    lines.push(obligation.attribution);
  }
  for (const obligation of present) {
    if (obligation.text) lines.push('', ...obligation.text.split('\n'));
  }
  return lines;
}
