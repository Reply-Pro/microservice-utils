import typing

import google.auth.transport.requests
import google.oauth2.id_token
from httpx import Client, Response, Timeout
from google.cloud import run_v2

from microservice_utils.google_cloud.models import GcpProjectConfig


class AuthorizedHTTPRequest:
    available_request_methods = ["get", "put", "post", "delete"]

    def __init__(self, service_url: typing.Optional[str] = None):
        # configure httpx client
        # disable read timeout to call GCP cloud run
        timeout = Timeout(10.0, read=None)
        self._client = Client(timeout=timeout)

        self.service_url = None
        self._headers = {}

        if service_url:
            self.set_service_url(service_url=service_url)
            self.set_authorization_header()

    def set_service_url(self, service_url: str) -> "AuthorizedHTTPRequest":
        self.service_url = service_url
        return self

    def set_authorization_header(self) -> "AuthorizedHTTPRequest":
        if not self.service_url:
            raise RuntimeError("Service url is required to set authorization header.")

        request = google.auth.transport.requests.Request()
        id_token = google.oauth2.id_token.fetch_id_token(request, self.service_url)
        self._headers["Authorization"] = f"Bearer {id_token}"
        return self

    def __getattr__(self, item):
        if item not in self.available_request_methods:
            raise AttributeError

        def method(
            *args,
            headers: typing.Optional[dict] = None,
            **kwargs,
        ) -> Response:
            headers = self._set_bearer_token(headers=headers)

            httpx_method = getattr(self._client, item)
            return httpx_method(*args, headers=headers, **kwargs)

        # set up the doc string
        method.__doc__ = f"Make an authorized {item} request using httpx."

        # set the name of the method
        method.__name__ = item

        return method

    def _set_bearer_token(self, headers: typing.Optional[dict] = None) -> dict:
        if not headers:
            return self._headers

        new_headers = dict(headers)
        new_headers.update(self._headers)

        return new_headers


async def get_cloud_run_urls(project: GcpProjectConfig) -> list[str]:
    client = run_v2.ServicesAsyncClient()
    request = run_v2.ListServicesRequest(parent=project.location_path)
    page_result = await client.list_services(request=request)

    return [response.uri async for response in page_result]


async def get_service_url(
    project: GcpProjectConfig,
    matches: list[str],
    exclude: list[str] = None,
    url_provider: typing.Callable[
        [GcpProjectConfig], typing.Awaitable[list[str]]
    ] = get_cloud_run_urls,
) -> str:
    urls = await url_provider(project)
    matches = [url for url in urls if all(match in url for match in matches)]

    if exclude:
        non_excluded_matches = []

        for url in matches:
            for i in exclude:
                if i not in url:
                    non_excluded_matches.append(url)

        matches = non_excluded_matches

    if len(matches) != 1:
        raise RuntimeError(f"Expected 1 service match, got {len(matches)}")

    return matches[0]
