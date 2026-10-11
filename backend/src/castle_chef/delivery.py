"""Validate the household's destination quickly; Instacart matching happens in jobs."""

import logging
from functools import lru_cache
from time import monotonic
from typing import Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from castle_chef_agent.address import Address, same_address
from castle_chef_agent.instacart import InstacartError
from pydantic import BaseModel, ValidationError

from castle_chef.models import Household

ADDRESS_REQUIRED = "Delivery address: enter your address in Settings, then try again."
ADDRESS_CHANGED = "Delivery address: it changed in Settings. Shop your list again for that address."
log = logging.getLogger(__name__)


class AddressInvalid(Exception):
    """No exact known street address matched the supplied destination."""


class AddressUnavailable(Exception):
    """The address provider could not be reached or returned an invalid reply."""


class Geocoder(Protocol):
    def geocode(
        self, *, QueryText: str, IntendedUse: str, MaxResults: int, Filter: dict[str, list[str]]
    ) -> object: ...

    def autocomplete(
        self,
        *,
        QueryText: str,
        IntendedUse: str,
        MaxResults: int,
        Filter: dict[str, list[str]],
        AdditionalFeatures: list[str],
    ) -> object: ...


@lru_cache
def geocoder() -> Geocoder:
    client: Geocoder = boto3.client(
        "geo-places",
        region_name="us-east-1",
        config=Config(connect_timeout=2, read_timeout=5, retries={"total_max_attempts": 1}),
    )
    return client


class _Region(BaseModel):
    Code: str


class _Country(BaseModel):
    Code3: str


class _LocationAddress(BaseModel):
    AddressNumber: str | None = None
    Street: str | None = None
    Locality: str | None = None
    Region: _Region | None = None
    Country: _Country | None = None
    PostalCode: str | None = None


class _Scores(BaseModel):
    Overall: float


class _Result(BaseModel):
    PlaceType: str
    Address: _LocationAddress | None = None
    MatchScores: _Scores | None = None
    AddressNumberCorrected: bool = False
    EstimatedPointAddress: bool = False


class _Reply(BaseModel):
    ResultItems: list[_Result]


def validate_address(address: Address) -> Address:
    """Return AWS's standardized form of a confirmed US street address.

    Geocoding confirms the building, not postal deliverability or apartment existence.
    Instacart must separately have exactly one saved address with the same unit.
    """
    street = address.model_copy(update={"apartment_number": ""})
    started = monotonic()
    try:
        reply = _Reply.model_validate(
            geocoder().geocode(
                QueryText=street.label,
                IntendedUse="Storage",
                MaxResults=3,
                Filter={"IncludeCountries": ["USA"]},
            )
        )
    except (BotoCoreError, ClientError, ValidationError) as error:
        raise AddressUnavailable(
            "Address verification is unavailable. Please try saving again."
        ) from error
    finally:
        log.info("address.geocode", extra={"ms": round((monotonic() - started) * 1000)})
    for item in reply.ResultItems:
        if (
            item.PlaceType != "PointAddress"
            or item.EstimatedPointAddress
            or item.AddressNumberCorrected
            or item.MatchScores is None
            or item.MatchScores.Overall != 1
            or item.Address is None
            or item.Address.Country is None
            or item.Address.Country.Code3 != "USA"
        ):
            continue
        candidate = _point_address(item)
        if candidate is not None and same_address(candidate, street):
            return candidate.model_copy(update={"apartment_number": address.apartment_number})
    raise AddressInvalid(
        "We couldn't verify that street address. Check the street, city, state and ZIP code."
    )


def _point_address(item: _Result) -> Address | None:
    if (
        item.PlaceType != "PointAddress"
        or item.EstimatedPointAddress
        or item.Address is None
        or item.Address.Country is None
        or item.Address.Country.Code3 != "USA"
    ):
        return None
    found = item.Address
    if (
        found.AddressNumber is None
        or found.Street is None
        or found.Locality is None
        or found.Region is None
        or found.PostalCode is None
    ):
        raise AddressUnavailable("AWS returned an incomplete address. Please try again.")
    try:
        return Address(
            street_address=f"{found.AddressNumber} {found.Street}",
            city=found.Locality,
            state=found.Region.Code,
            postal_code=found.PostalCode,
        )
    except ValidationError as error:
        raise AddressUnavailable("AWS returned an invalid address. Please try again.") from error


def suggestions(query: str) -> list[Address]:
    """Transient AWS completions; Geocode with Storage intent confirms the selected one."""
    started = monotonic()
    try:
        reply = _Reply.model_validate(
            geocoder().autocomplete(
                QueryText=query,
                IntendedUse="SingleUse",
                MaxResults=5,
                AdditionalFeatures=["Core"],
                Filter={"IncludeCountries": ["USA"], "IncludePlaceTypes": ["PointAddress"]},
            )
        )
    except (BotoCoreError, ClientError, ValidationError) as error:
        raise AddressUnavailable(
            "Address search is unavailable. Please try typing again."
        ) from error
    finally:
        log.info("address.autocomplete", extra={"ms": round((monotonic() - started) * 1000)})
    addresses: list[Address] = []
    for item in reply.ResultItems:
        address = _point_address(item)
        if address is not None and address not in addresses:
            addresses.append(address)
    return addresses


def selected(household: Household) -> Address | None:
    return household.delivery_address


def require_address(household: Household) -> Address:
    address = selected(household)
    if address is None:
        raise InstacartError(ADDRESS_REQUIRED)
    return address
