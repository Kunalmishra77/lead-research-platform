"""A proposed split of a site's substitutions, for a reviewer to check (task 3.14).

The harvester's draft guesses `expect` from the strongest context and dumps everything else into
`absent`. That is too crude to review: one site had 61 absent values, of which 58 were SVG
coordinates and WordPress cache-busters, and a reviewer reading past 58 lines of noise is a
reviewer who stops reading.

So this proposes a split with a reason attached to every entry, and prints only what a human has
to decide. It is still a proposal: `python -m app.golden propose <slug>` writes nothing. What
makes a label a label is somebody agreeing with it (ADR-0020).

The classification is about what a value *is*, never about what the extractors found:

* a contact context (`mailto:`, `tel:`, WhatsApp, JSON-LD) is somebody pointing at a contact
  detail, so it is the business's unless the markup says otherwise;
* a value in running text that `phonenumbers` or `email-validator` accepts is a contact detail
  whose owner a human has to judge;
* everything else is named for what it actually is -- a cache-buster, a date, an element id, SVG
  path data -- because `absent_because` is how the next person knows not to "fix" the label.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from app.golden.corpus import CORPUS, Labels, pages_of
from app.golden.inspect import occurrences
from app.normalize.emails import normalize as normalize_email
from app.normalize.phones import to_e164

#: Contexts where somebody is pointing at their own contact details.
_LINKED: Final[frozenset[str]] = frozenset({"mailto", "tel", "whatsapp", "jsonld"})

#: What a number in running text turns out to be, when it is not a phone number. Matched against
#: the markup immediately before it, which is what `inspect` already shows a reviewer.
_NOT_A_PHONE: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"(?i)[?&]ver=$"), "a cache-busting ?ver= on a stylesheet or script"),
    (re.compile(r"(?i)(date[a-z]*|time)[\"'=:\s]*$"), "a date or time in the markup"),
    (re.compile(r"(?i)data-(id|element_id|widget_id)=[\"']?$"), "a page-builder element id"),
    (re.compile(r"(?i)(facebook|instagram|linkedin|twitter|youtube)\.com/[^\"'<>]*$"),
     "part of a social profile URL, not a phone number"),
    (re.compile(r"(?i)(viewbox|path d|&quot;m\d)"), "SVG path data"),
    (re.compile(r"(?i)(width|height|size|px|rem|em)[=:\s\"']*$"), "a CSS or image dimension"),
    (re.compile(r"(?i)(pin|pincode|postcode|postal)[\s:]*$"), "a postcode"),
    (re.compile(r"(?i)(gst|gstin|reg|licen[cs]e|regd)[\s.:no]*$"), "a registration number"),
    (re.compile(r"(?i)(latitude|longitude|geo\.position|geocoordinates)"),
     "a map coordinate from the page's geo metadata"),
    (re.compile(r"(?i)name=[\"'](form_time|timestamp|_wpnonce)[\"'][^>]*value=[\"']?$"),
     "a form's hidden timestamp field"),
    (re.compile(r"(?i)(/wp-content/uploads/|srcset=)[^\"'<>]*$"),
     "part of an image filename or the srcset beside it"),
    (re.compile(r"(?i)data-cfemail=[\"']?$"), "digits out of Cloudflare's obfuscated-email hex"),
    (re.compile(r"(?i)/cdn-cgi/l/email-protection#?$"),
     "digits inside Cloudflare's email-protection hex, which hides the address from harvesters"),
    (re.compile(r"(?i)(data-token|data-nonce|nonce|csrf)[\"'=:\s]*$"),
     "a form's one-time token"),
    (re.compile(r"(?i)(z-index|opacity|cursor)[^;]*$"), "a CSS value, escaped inside an attribute"),
    (re.compile(r"(?i)(title|alt)=[\"'][^\"'<>]*$"),
     "inside a title or alt attribute, which the text extractor never reads"),
    (re.compile(r"""(?i)\b(src|srcset|href|data-[a-z-]+)=["'][^"'<>]*$"""),
     "inside a src, href or data- attribute, which the text extractor never reads"),
    (re.compile(r"\*{2,}$"),
     "an address the site masks with asterisks, so there is no address to read"),
    (re.compile(r"""(?i)https?://[^"'<>]*$"""), "part of a URL's host"),
    (re.compile(r"""(?i)/[^\s"'<>]{8,}$"""),
     "glued to the end of a URL path, which cannot happen to a number printed in prose"),
    (re.compile(r"""(?i)%[0-9a-f]{2}[^\s"'<>]*$"""), "part of a percent-encoded URL"),
    (re.compile(r"(?i)[?&][a-z_]{1,12}=$"),
     "a parameter in a URL's query string, such as an image resize"),
    (re.compile(r"""(?i)(/img/|/images?/|/uploads?/|amazonaws\.com/)[^"'<>]*$"""),
     "part of a path to an image"),
    (re.compile(r"""(?i)\bid=["'][^"'<>]*$"""), "an element id"),
    (re.compile(r"""(?i)schema[\\/]+[a-z]*[\\/]+$"""),
     "a JSON-LD node id under the page's own schema namespace"),
    (re.compile(r"""(?i)(placeholder|aria-label)=["'][^"'<>]*$"""),
     "an input's placeholder or label, which is an example rather than a contact"),
)

#: Someone else's number or address, named in the markup around it.
_SOMEBODY_ELSE: Final[re.Pattern[str]] = re.compile(
    r"(?i)(site|website|designed|developed|powered|maintained|hosted|marketing)\s+(by|with)"
)

#: What a value is by its own digits, with no markup needed. Checked before dialability, because
#: a latitude that `phonenumbers` happens to accept is still a latitude -- and it did accept
#: them: `1794677085`, a WordPress `form_time`, normalises to `+911794677085` and types as a
#: fixed_line, because `179` is a real Chandigarh-region area code. Four of those reached
#: `also_theirs` before this ran first.
_SHAPES: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"^\d{13,}$"), "a run of 13+ digits: longer than any phone number, so an id"),
    (re.compile(r"^\d{1,3}\.\d{3,}$"), "a decimal number -- a coordinate, a price or a version"),
    (re.compile(r"^(19|20)\d\d[-/]"), "a date"),
    (re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$"), "an IP address"),
)

#: A unix timestamp in seconds: 1.6 to 1.9 billion for the years this corpus is built in, so ten
#: digits beginning with 1. By digits alone it cannot be told from a `+91 17x` landline, so it is
#: only named outside a contact context -- WordPress writes these into `form_time`, `?ver=` and
#: element ids, and never into a `tel:` link.
_TIMESTAMP: Final[re.Pattern[str]] = re.compile(r"^1[6-9]\d{8}$")


@dataclass(slots=True)
class Proposal:
    expect: dict[str, str]
    also_theirs: list[str]
    absent: list[str]
    absent_because: dict[str, str]
    #: Values the rules will not judge. Printed for a human, and the only thing they must read.
    undecided: list[tuple[str, str, str]]


def _kind_of(value: str) -> str:
    return "email" if "@" in value else "phone"


def _is_a_contact(value: str) -> bool:
    """Whether this could be dialled or written to at all.

    Uses the normalizers, which is the one place in this module where the code under test is
    consulted -- and only to ask "is this a phone number", never "is this the business's". A
    value they reject cannot be a contact detail for anybody, whoever extracted it.
    """
    if _kind_of(value) == "email":
        return normalize_email(value) is not None
    return to_e164(value, "IN") is not None


def propose(site: Labels, root: Path = CORPUS) -> Proposal:
    """A split with a reason for every entry, and a list of what a human has to decide."""
    out = Proposal({}, [], [], {}, [])
    best: dict[str, tuple[int, str]] = {}
    contacts: dict[str, list[str]] = {"email": [], "phone": []}
    rank = {"jsonld": 3, "mailto": 3, "tel": 3, "whatsapp": 2, "text": 1}

    by_value: dict[str, list[tuple[str, str]]] = {}
    for occurrence in occurrences(site, root):
        by_value.setdefault(occurrence.value, []).append((occurrence.before, occurrence.after))

    for value, places in by_value.items():
        kind = _kind_of(value)
        context = _strongest_context(site, value, root)
        linked = context in _LINKED

        # The order is the whole design, and it was wrong in the first version. Dialability was
        # asked first, so every value `phonenumbers` accepted became the business's -- four
        # WordPress timestamps among them. What a value *is* comes first; whether it can be
        # dialled only matters once nothing has named it as something else.
        if any(_SOMEBODY_ELSE.search(before) for before, _ in places):
            out.absent.append(value)
            out.absent_because[value] = "credited to whoever built the site, not the business"
            continue

        if linked and _is_a_contact(value):
            # An explicit claim: somebody wrote `mailto:`, `tel:` or JSON-LD around it. No
            # heuristic about the surrounding markup gets to overrule that.
            pass
        elif (reason := _what_it_is(value, linked, places)) is not None:
            out.absent.append(value)
            out.absent_because[value] = reason
            continue
        elif not _is_a_contact(value):
            out.undecided.append((value, context, _shorten(places[0][0])))
            continue

        contacts[kind].append(value)
        score = rank.get(context, 0)
        if kind not in best or score > best[kind][0]:
            best[kind] = (score, value)

    for kind, (_score, value) in best.items():
        out.expect[kind] = value
    out.also_theirs = [
        value
        for kind, values in contacts.items()
        for value in values
        if best.get(kind, (0, ""))[1] != value
    ]
    return out


def _strongest_context(site: Labels, value: str, root: Path) -> str:
    """The best context this value appears in, over every page of the site."""
    rank = {"jsonld": 3, "mailto": 3, "tel": 3, "whatsapp": 2, "text": 1}
    best = "text"
    for page, markup in pages_of(site, root):
        del page
        for match in re.finditer(re.escape(value), markup):
            from app.golden.scrub import _context_of  # noqa: PLC0415 -- see below

            # Imported here rather than at module scope only because it is the one private name
            # this module borrows, and importing it at the top would read as part of the public
            # shape of the scrubber.
            context = _context_of(markup, match.start())
            if rank.get(context, 0) > rank.get(best, 0):
                best = context
    return best


def _what_it_is(value: str, linked: bool, places: list[tuple[str, str]]) -> str | None:
    """What this value is, when something says plainly, or None when nothing does.

    The order matters twice over.

    A link the page itself got wrong is named first. `tel:+01240723201` is on a live clinic
    page, and a `+` in front of a trunk zero is not a number anything can dial; "the page's own
    link is malformed" tells the next reviewer what happened, where "inside an href attribute"
    would merely be true. The check is cheap and only fires in a contact context.

    Then the markup, which gives a better reason than the digits do: "a map coordinate from the
    page's geo metadata" says more than "a decimal number", and `absent_because` is read by
    people rather than by code.
    """
    if linked and not _is_a_contact(value):
        return "the page's own link is malformed, so nothing can read a contact out of it"

    for before, _after in places:
        for pattern, reason in _NOT_A_PHONE:
            # The markup *before* the value only. Looking ahead as well named a number in a
            # paragraph after the `<link href=...>` that happened to follow it: what comes next
            # says nothing about what a value is, and every rule here is anchored to its left.
            if pattern.search(before):
                return reason

    if "@" in value and "*" in value.split("@", maxsplit=1)[0]:
        # The site masked its own address: `"email": "**********info@…"` in its own JSON-LD.
        # There is nothing to extract, and `app/normalize/emails.py` refuses it rather than
        # guessing at the hidden characters.
        return "an address the site masks with asterisks, so no address can be read from it"

    if "@" not in value:
        bare = value.strip()
        for pattern, reason in _SHAPES:
            if pattern.match(bare):
                return reason
        if not linked and _TIMESTAMP.match(bare):
            return "a unix timestamp in seconds, not a number in any range this page dials"
    return None


def _shorten(text: str, width: int = 90) -> str:
    return text[-width:] if len(text) > width else text


def render(site: Labels, root: Path = CORPUS, *, reasons: bool = False) -> str:
    """The proposal, with the undecided values last because they are the work."""
    proposal = propose(site, root)
    lines = [f"{site.slug}: proposed labels", ""]
    for field, value in sorted(proposal.expect.items()):
        lines.append(f"  expect {field:6} {value}")
    for value in proposal.also_theirs:
        lines.append(f"  also theirs   {value}")
    lines.append(f"  absent        {len(proposal.absent)} value(s), each with a reason")

    if reasons:
        # Printed on request, because the reasons are the part worth checking: a rule that
        # misfires names a real contact detail as a cache-buster, and that is a false *negative*
        # the report would then score as correct behaviour.
        lines.append("")
        for value in proposal.absent:
            lines.append(f"    {value:34} {proposal.absent_because[value]}")

    if proposal.undecided:
        lines.append("")
        lines.append(f"DECIDE THESE {len(proposal.undecided)}:")
        for value, context, before in proposal.undecided:
            lines.append(f"  {value}   ({context})")
            lines.append(f"      ...{before}")
    else:
        lines.append("")
        lines.append("nothing undecided: every value was named by the markup around it")
    return "\n".join(lines)


def write(site: Labels, root: Path = CORPUS) -> list[str]:
    """Records the proposal in `labels.json`, still as a draft. Returns what is left undecided.

    Writing and reviewing stay two commands. A proposal is a machine's reading of the markup;
    what makes it a label is somebody agreeing with it (ADR-0020), and `review` is where that
    happens. Hand-editing the JSON instead is what dropped a value from a label once already,
    leaving a substituted number in no category and so unchecked by the leak gate.

    Nothing is written while anything is undecided, and that is a correction. The first version
    wrote the decided values and simply left the rest out, trusting the leak gate to complain.
    The gate did complain -- but the dropped value was now missing from the labels too, so the
    next `propose` could not see it and the loss compounded run after run: one site lost a value
    on the second pass and a second site lost one on the third. A partial labelling is refused
    whole instead. `labels.json` is only ever written complete, and an undecided value stays
    where the harvester put it until a rule or a human accounts for it.
    """
    import json  # noqa: PLC0415 -- the only place this module touches the filesystem.

    proposal = propose(site, root)
    left = [value for value, _context, _before in proposal.undecided]
    if left:
        return left

    path = root / site.slug / "labels.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["expect"] = proposal.expect
    raw["also_theirs"] = proposal.also_theirs
    raw["absent"] = proposal.absent
    raw["absent_because"] = proposal.absent_because
    raw["draft"] = True
    path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return []
