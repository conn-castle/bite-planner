"""Street verification through the AWS client boundary, without real network calls."""

from collections.abc import Iterator

import boto3
import pytest
from botocore.stub import Stubber
from castle_chef_agent.address import Address

from castle_chef import delivery

ADDRESS: Address = Address(
    street_address="1600 Pennsylvania Avenue NW", city="Washington", state="DC", postal_code="20500"
)
LOCATION_ADDRESS: dict[str, object] = {
    "AddressNumber": "1600",
    "Street": "Pennsylvania Ave NW",
    "Locality": "Washington",
    "Region": {"Code": "DC"},
    "Country": {"Code3": "USA"},
    "PostalCode": "20500-0005",
}
RESULT: dict[str, object] = {
    "PlaceId": "test-address",
    "PlaceType": "PointAddress",
    "Title": ADDRESS.label,
    "Position": [-77.0365, 38.8977],
    "Address": LOCATION_ADDRESS,
    "MatchScores": {"Overall": 1.0},
}
PARAMS: dict[str, object] = {
    "QueryText": ADDRESS.label,
    "IntendedUse": "Storage",
    "MaxResults": 3,
    "Filter": {"IncludeCountries": ["USA"]},
}


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[Stubber]:
    client: delivery.Geocoder = boto3.client(
        "geo-places",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    monkeypatch.setattr(delivery, "geocoder", lambda: client)
    with Stubber(client) as stub:
        yield stub
        stub.assert_no_pending_responses()


def test_a_known_point_address_accepts_street_abbreviations_and_zip_extension(
    provider: Stubber,
) -> None:
    provider.add_response("geocode", {"ResultItems": [RESULT], "PricingBucket": "Core"}, PARAMS)
    normalized: Address = delivery.validate_address(ADDRESS)
    assert normalized == Address(
        street_address="1600 Pennsylvania Ave NW",
        city="Washington",
        state="DC",
        postal_code="20500-0005",
    )


def test_units_are_preserved_for_instacart_matching_but_the_building_is_verified(
    provider: Stubber,
) -> None:
    provider.add_response("geocode", {"ResultItems": [RESULT], "PricingBucket": "Core"}, PARAMS)
    entered = ADDRESS.model_copy(update={"apartment_number": "Apt 2"})
    normalized: Address = delivery.validate_address(entered)
    assert normalized.apartment_number == "Apt 2"
    assert normalized.street_address == "1600 Pennsylvania Ave NW"


@pytest.mark.parametrize(
    "change",
    [
        {"PlaceType": "Street", "Address": {"Street": "Pennsylvania Ave NW"}},
        {"EstimatedPointAddress": True},
        {"AddressNumberCorrected": True},
        {"MatchScores": {"Overall": 0.99}},
        {"Address": dict(LOCATION_ADDRESS, AddressNumber="1601")},
        {"Address": dict(LOCATION_ADDRESS, PostalCode="20501")},
    ],
)
def test_approximate_or_different_addresses_are_refused(
    provider: Stubber, change: dict[str, object]
) -> None:
    provider.add_response(
        "geocode", {"ResultItems": [RESULT | change], "PricingBucket": "Core"}, PARAMS
    )
    with pytest.raises(delivery.AddressInvalid, match="couldn't verify"):
        delivery.validate_address(ADDRESS)


def test_no_match_is_an_actionable_validation_error(provider: Stubber) -> None:
    provider.add_response("geocode", {"ResultItems": [], "PricingBucket": "Core"}, PARAMS)
    with pytest.raises(delivery.AddressInvalid, match="Check the street"):
        delivery.validate_address(ADDRESS)


def test_a_provider_failure_is_not_reported_as_an_invalid_address(provider: Stubber) -> None:
    provider.add_client_error(
        "geocode", service_error_code="AccessDeniedException", expected_params=PARAMS
    )
    with pytest.raises(delivery.AddressUnavailable, match="try saving again"):
        delivery.validate_address(ADDRESS)


def test_autocomplete_returns_complete_addresses_as_transient_results(provider: Stubber) -> None:
    params: dict[str, object] = {
        "QueryText": "1600 Penn",
        "IntendedUse": "SingleUse",
        "MaxResults": 5,
        "AdditionalFeatures": ["Core"],
        "Filter": {"IncludeCountries": ["USA"], "IncludePlaceTypes": ["PointAddress"]},
    }
    result: dict[str, object] = {
        key: value for key, value in RESULT.items() if key not in {"Position", "MatchScores"}
    }
    provider.add_response(
        "autocomplete",
        {
            "ResultItems": [
                result,
                result,
                result | {"PlaceType": "Street"},
                result | {"EstimatedPointAddress": True},
            ],
            "PricingBucket": "Core",
        },
        params,
    )
    addresses: list[Address] = delivery.suggestions("1600 Penn")
    assert addresses == [
        Address(
            street_address="1600 Pennsylvania Ave NW",
            city="Washington",
            state="DC",
            postal_code="20500-0005",
        )
    ]


def test_autocomplete_failure_is_actionable(provider: Stubber) -> None:
    provider.add_client_error("autocomplete", service_error_code="AccessDeniedException")
    with pytest.raises(delivery.AddressUnavailable, match="Address search is unavailable"):
        delivery.suggestions("1600 Penn")
