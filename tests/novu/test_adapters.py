import pytest
from microservice_utils.novu.adapters import SubscriberManager


@pytest.mark.parametrize(
    "identifier,prefix,expected",
    [
        ("email@test.com", None, "nonusercollaborator:email@test.com"),
        ("abc123", "collaborator", "collaborator:abc123"),
    ],
)
def test_build_collaborator_id(identifier, prefix, expected):
    kwargs = {}

    if prefix:
        kwargs["prefix"] = prefix

    assert SubscriberManager.build_collaborator_id(identifier, **kwargs) == expected
