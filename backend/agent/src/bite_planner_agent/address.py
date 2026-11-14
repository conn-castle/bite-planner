"""The requested delivery destination, independent of Instacart's saved IDs."""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Equivalent spellings, not fuzzy matching: house and unit numbers remain exact.
_ALIASES: dict[str, str] = {
    "street": "st",
    "avenue": "ave",
    "boulevard": "blvd",
    "court": "ct",
    "drive": "dr",
    "lane": "ln",
    "road": "rd",
    "place": "pl",
    "terrace": "ter",
    "circle": "cir",
    "parkway": "pkwy",
    "highway": "hwy",
    "trail": "trl",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
    "northeast": "ne",
    "northwest": "nw",
    "southeast": "se",
    "southwest": "sw",
}


def normalized(value: str) -> str:
    """Normalize punctuation, spacing and common US street/direction abbreviations."""
    return " ".join(
        _ALIASES.get(token, token) for token in re.findall(r"[a-z0-9]+", value.casefold())
    )


def unit_number(value: str) -> str:
    return normalized(re.sub(r"^(?:apt|apartment|unit|suite|ste)\.?\s*", "", value, flags=re.I))


class Address(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    street_address: str = Field(min_length=1, max_length=100)
    apartment_number: str = Field(default="", max_length=30)
    city: str = Field(min_length=1, max_length=60)
    state: str = Field(pattern=r"^[A-Z]{2}$")
    postal_code: str = Field(pattern=r"^\d{5}(?:-\d{4})?$")

    @field_validator("state", mode="before")
    @classmethod
    def uppercase_state(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @property
    def label(self) -> str:
        street = ", ".join(filter(None, [self.street_address, self.apartment_number]))
        return f"{street}, {self.city}, {self.state} {self.postal_code}"


def same_address(left: Address, right: Address) -> bool:
    return (
        normalized(left.street_address) == normalized(right.street_address)
        and unit_number(left.apartment_number) == unit_number(right.apartment_number)
        and normalized(left.city) == normalized(right.city)
        and left.state == right.state
        and left.postal_code[:5] == right.postal_code[:5]
    )


def normalized_label(value: str) -> str:
    # Checkout labels may include a ZIP+4 and an apartment prefix.
    value = re.sub(r"\b(\d{5})-\d{4}\b", r"\1", value)
    value = re.sub(r"\b(?:apt|apartment|unit|suite|ste)\.?\s*", "", value, flags=re.I)
    return normalized(value)
