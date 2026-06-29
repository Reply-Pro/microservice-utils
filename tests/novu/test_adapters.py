from unittest.mock import MagicMock

import pytest
from novu_py.models import ResponseValidationError

from microservice_utils.novu.adapters import Notifier, SubscriberManager


def _raw_page():
    """A raw Novu activity-feed page whose only job has NO ``providerId``.

    This is exactly the shape that makes the SDK's strict
    ``ActivityNotificationJobResponseDto`` (providerId required) reject the whole
    page with ``ResponseValidationError`` — non-channel steps (in-app/digest)
    don't carry a providerId.
    """
    return {
        "page": 0,
        "hasMore": False,
        "pageSize": 10,
        "data": [
            {
                "_environmentId": "env1",
                "_organizationId": "org1",
                "transactionId": "txn_1",
                "createdAt": "2026-06-26T18:00:12.000Z",
                "channels": ["email"],
                "subscriber": {
                    "_id": "s1",
                    "email": "user@example.com",
                    "firstName": "A",
                    "lastName": "B",
                    "phone": "",
                },
                "template": {
                    "name": "Weekly",
                    "_id": "t1",
                    "triggers": [
                        {
                            "type": "event",
                            "identifier": "shop-notification-weekly",
                            "variables": [],
                        }
                    ],
                },
                "jobs": [
                    {
                        "_id": "j1",
                        "type": "in_app",
                        "status": "completed",
                        "payload": {
                            "base_url": "https://results.realitybasedgroup.com"
                        },
                    }
                ],
            }
        ],
    }


def _assert_parsed(resp):
    assert resp.page == 0
    assert resp.has_more is False
    assert resp.page_size == 10
    item = resp._data[0]
    assert item.created_at == "2026-06-26T18:00:12.000Z"
    assert item.template.triggers[0].identifier == "shop-notification-weekly"
    assert item.subscriber.email == "user@example.com"
    # jobs stay raw dicts so the consumer's item.jobs[0]["status"]/["payload"] work
    assert item.jobs[0]["status"] == "completed"
    assert item.jobs[0]["payload"]["base_url"].startswith("https://")


def test_build_notification_response_tolerates_missing_provider_id():
    _assert_parsed(Notifier._build_notification_response(_raw_page()))


def test_get_notifications_falls_back_on_response_validation_error():
    notifier = Notifier.__new__(Notifier)  # bypass Novu() construction
    notifier.api = MagicMock()
    raw_response = MagicMock()
    raw_response.json.return_value = _raw_page()
    notifier.api.notifications.list.side_effect = ResponseValidationError(
        "validation failed", raw_response, Exception("providerId Field required")
    )

    _assert_parsed(notifier.get_notifications(page=0))


def test_get_notifications_success_path_normalizes_wrapper():
    notifier = Notifier.__new__(Notifier)
    notifier.api = MagicMock()
    dto = MagicMock()
    dto.model_dump.return_value = _raw_page()["data"][0]
    result = MagicMock(page=0, has_more=False, page_size=10, data=[dto])
    notifier.api.notifications.list.return_value = MagicMock(result=result)

    _assert_parsed(notifier.get_notifications(page=0))
    dto.model_dump.assert_called_once_with(by_alias=True)


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


def test_build_collaborator_id_no_prefix():
    with pytest.raises(ValueError):
        SubscriberManager.build_collaborator_id("random@test.com", prefix=None)
