import typing

import httpx
from google.auth import default
from google.cloud import run_v2

from microservice_utils.google_cloud.models import GcpProjectConfig


class AuthorizedHTTPRequest:
    available_request_methods = ["get", "put", "post", "delete"]

    def __init__(self):
        self.credential, self.project_id = default()
        self._headers = {"Authorization": f"Bearer {self.credential.token}"}
        self._setup_methods()

    def _setup_methods(self_outer_scope):
        def add_method(name: str):
            """
            add/replace a method which allows you to call asynchronous
            methods without having to worry about getting the event
            loop + running until complete.
            """

            def new_method(
                self, *args, headers: typing.Optional[dict] = None, **kwargs
            ) -> httpx.Response:
                headers = self._set_bearer_token(headers=headers)

                httpx_method = getattr(httpx, name=name)
                return httpx_method(*args, headers=headers, **kwargs)

            # set up the doc string
            new_method.__doc__ = f"Make an authorized {name} request using httpx."

            # set the name of the method
            new_method.__name__ = name

            # set the method on the class
            setattr(self_outer_scope, name, new_method)

        for available_request_method in self_outer_scope.available_request_methods:
            add_method(name=available_request_method)

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
