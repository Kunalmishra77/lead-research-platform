"""The parts of the Places API (New) responses we ask for (docs/08).

Shapes verified against live responses on 2026-09-23. Everything is optional because a field mask
decides what comes back, and a place that has no website simply omits `websiteUri` rather than
sending null — so an absent field means "not known", never "empty".
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LocalizedText(BaseModel):
    """Google returns display strings as {text, languageCode}."""

    model_config = ConfigDict(extra="ignore")

    text: str = ""
    language_code: str | None = Field(default=None, alias="languageCode")


class LatLng(BaseModel):
    model_config = ConfigDict(extra="ignore")

    latitude: float
    longitude: float


class AddressComponent(BaseModel):
    """One piece of a formatted address, tagged with what it is."""

    model_config = ConfigDict(extra="ignore")

    long_text: str | None = Field(default=None, alias="longText")
    short_text: str | None = Field(default=None, alias="shortText")
    types: list[str] = Field(default_factory=list)


class OpeningHourPoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    day: int
    hour: int
    minute: int


class OpeningHoursPeriod(BaseModel):
    model_config = ConfigDict(extra="ignore")

    open: OpeningHourPoint | None = None
    close: OpeningHourPoint | None = None


class OpeningHours(BaseModel):
    model_config = ConfigDict(extra="ignore")

    #: Deliberately not mapped: it is true only at the moment of the call.
    open_now: bool | None = Field(default=None, alias="openNow")
    periods: list[OpeningHoursPeriod] = Field(default_factory=list)
    weekday_descriptions: list[str] = Field(default_factory=list, alias="weekdayDescriptions")


class Place(BaseModel):
    """One business as the Places API returns it."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    display_name: LocalizedText | None = Field(default=None, alias="displayName")
    formatted_address: str | None = Field(default=None, alias="formattedAddress")
    address_components: list[AddressComponent] = Field(
        default_factory=list, alias="addressComponents"
    )
    location: LatLng | None = None
    types: list[str] = Field(default_factory=list)
    primary_type: str | None = Field(default=None, alias="primaryType")
    primary_type_display_name: LocalizedText | None = Field(
        default=None, alias="primaryTypeDisplayName"
    )
    national_phone_number: str | None = Field(default=None, alias="nationalPhoneNumber")
    international_phone_number: str | None = Field(default=None, alias="internationalPhoneNumber")
    website_uri: str | None = Field(default=None, alias="websiteUri")
    rating: float | None = None
    user_rating_count: int | None = Field(default=None, alias="userRatingCount")
    business_status: str | None = Field(default=None, alias="businessStatus")
    google_maps_uri: str | None = Field(default=None, alias="googleMapsUri")
    regular_opening_hours: OpeningHours | None = Field(default=None, alias="regularOpeningHours")

    def component(self, *types: str) -> str | None:
        """The first address component tagged with any of `types`, long form."""
        for component in self.address_components:
            if any(t in component.types for t in types):
                return component.long_text or component.short_text
        return None

    def component_short(self, *types: str) -> str | None:
        for component in self.address_components:
            if any(t in component.types for t in types):
                return component.short_text or component.long_text
        return None


class TextSearchResponse(BaseModel):
    """`places:searchText`. Both fields are absent when nothing matched."""

    model_config = ConfigDict(extra="ignore")

    places: list[Place] = Field(default_factory=list)
    next_page_token: str | None = Field(default=None, alias="nextPageToken")


class ApiError(BaseModel):
    """Google's error envelope, used only to explain a failure in a log or a reply."""

    model_config = ConfigDict(extra="ignore")

    code: int | None = None
    message: str | None = None
    status: str | None = None

    @classmethod
    def from_body(cls, body: dict[str, Any]) -> "ApiError | None":
        error = body.get("error")
        return cls.model_validate(error) if isinstance(error, dict) else None
