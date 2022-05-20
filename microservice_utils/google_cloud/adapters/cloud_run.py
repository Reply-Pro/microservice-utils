from google.cloud import run_v2

from microservice_utils.google_cloud.dtos import GCPProjectConfig


async def get_cloud_run_urls(project: GCPProjectConfig) -> list[str]:
    client = run_v2.ServicesAsyncClient()
    request = run_v2.ListServicesRequest(parent=project.location_path)
    page_result = await client.list_services(request=request)

    return [response.uri async for response in page_result]
