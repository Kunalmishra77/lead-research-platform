"""The parts of a serper.dev response we use (ADR-0006).

Shape verified against live responses on 2026-09-23. A field mask does not exist here: the vendor
returns what Google showed, so everything is optional and an absent block means "not on the page",
never "empty".
"""

from pydantic import BaseModel, ConfigDict, Field


class OrganicResult(BaseModel):
    """One ordinary search result."""

    model_config = ConfigDict(extra="ignore")

    title: str = ""
    link: str = ""
    snippet: str = ""
    position: int | None = None


class SitelinkResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = ""
    link: str = ""


class KnowledgeGraph(BaseModel):
    """Google's panel for an entity it recognises. Present only sometimes."""

    model_config = ConfigDict(extra="ignore")

    title: str = ""
    type: str | None = None
    website: str | None = None
    description: str | None = None


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    organic: list[OrganicResult] = Field(default_factory=list)
    knowledge_graph: KnowledgeGraph | None = Field(default=None, alias="knowledgeGraph")
    #: Credits this call consumed; the vendor's own count, used for metering sanity.
    credits: int | None = None
