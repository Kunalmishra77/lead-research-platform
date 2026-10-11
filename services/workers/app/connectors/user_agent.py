"""How we identify ourselves to every server we talk to (docs/08).

Its own module, and not `factory.py` where it used to live, because the crawler needs it too and
the crawler is not built by the factory. That import ran backwards -- `app/crawl` reaching into
the thing that assembles connectors -- and it became a cycle the moment a connector needed
anything from `app/crawl`: factory -> places -> mapping -> contacts -> factory.

A constant with no dependencies belongs somewhere with no dependencies.
"""

from typing import Final

#: Honest, with a contact URL, as docs/08 requires of every request we make.
USER_AGENT: Final[str] = "LeadForgeBot/1.0 (+https://leadforge.example/bot)"
