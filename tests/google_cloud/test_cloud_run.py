import pytest

from microservice_utils.google_cloud.adapters.cloud_run import get_service_url


class FakeGcpProjectConfig:
    pass


async def test_url_provider(*args, **kwargs) -> list[str]:
    return [
        "https://staging-accounts-service-oir932o-uc.a.run.app",
        "https://production-accounts-service-0smn3lu-uc.a.run.app",
        "https://staging-photos-service-9fsnd3w-uc.a.run.app",
        "https://staging-account-photos-service-9fsnd3w-uc.a.run.app",
    ]


@pytest.mark.parametrize(
    "matches,exclude,expected",
    [
        (
            ["staging", "accounts"],
            None,
            "https://staging-accounts-service-oir932o-uc.a.run.app",
        ),
        (
            ["staging", "photos"],
            ["account"],
            "https://staging-photos-service-9fsnd3w-uc.a.run.app",
        ),
        (
            ["staging", "photos"],
            ["account-photos"],
            "https://staging-photos-service-9fsnd3w-uc.a.run.app",
        ),
        (
            ["production", "accounts"],
            None,
            "https://production-accounts-service-0smn3lu-uc.a.run.app",
        ),
    ],
)
async def test_get_service_url(matches, exclude, expected):
    url = await get_service_url(
        FakeGcpProjectConfig(), matches, exclude=exclude, url_provider=test_url_provider
    )

    assert url == expected
