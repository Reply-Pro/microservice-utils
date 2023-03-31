from uuid import UUID

import pytest
from microservice_utils.graphql import (
    GlobalId,
    construct_global_id,
    deconstruct_global_id,
)


@pytest.fixture
def test_global_id() -> str:
    return "Tm90aWZpY2F0aW9uOmM0MWY5ZjA5LWY1ZWYtNGE3OC05ZDJiLTU5MzQ5MjA5MzIwMQ=="


@pytest.fixture
def test_uuid() -> UUID:
    return UUID("c41f9f09-f5ef-4a78-9d2b-593492093201")


def test_construct_global_id(test_global_id, test_uuid):
    constructed = construct_global_id("Notification", test_uuid)

    assert constructed == test_global_id


def test_deconstruct_global_id(test_global_id, test_uuid):
    deconstructed = deconstruct_global_id(test_global_id)

    assert deconstructed == GlobalId(node="Notification", uuid=test_uuid)
