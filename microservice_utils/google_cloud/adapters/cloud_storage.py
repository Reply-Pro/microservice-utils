import typing
from dataclasses import dataclass
from datetime import timedelta

from gcloud.aio.storage import Storage, Bucket


@dataclass(frozen=True)
class GcsObject:
    content: bytes
    metadata: dict


class GcsObjectRepository:
    _client: typing.Optional[Storage] = None
    _namespace: typing.Optional[str] = None
    PUBLIC_IMAGE_TTL = 30

    def __init__(self, bucket_name: str, delimiter: str = None):
        self._bucket_name = bucket_name
        self._delimiter = delimiter or "/"

    async def __aenter__(self, namespace: str) -> "GcsObjectRepository":
        self._client = Storage()
        self._namespace = namespace

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._client.close()
        self._client = None
        self._namespace = None

    @property
    def bucket(self) -> Bucket:
        return Bucket(self._get_client(), self._bucket_name)

    def get_full_object_id(self, object_id: str) -> str:
        if not self._namespace:
            raise RuntimeError("Namespace not set!")

        return f"{self._namespace}{self._delimiter}{object_id}"

    def _get_client(self) -> Storage:
        if self._client:
            return self._client
        else:
            raise RuntimeError("The GCS Storage client has not been instantiated.")

    async def add(self, object_id: str, content: bytes, content_type: str) -> str:
        result = await self._get_client().upload(
            self._bucket_name,
            self.get_full_object_id(object_id),
            content,
            content_type=content_type,
        )

        return result["id"]

    async def get(self, object_id: str) -> GcsObject:
        remote_object_name = self.get_full_object_id(object_id)
        client = self._get_client()

        content = await client.download(self._bucket_name, remote_object_name)
        metadata = await client.download_metadata(self._bucket_name, remote_object_name)

        return GcsObject(content=content, metadata=metadata)

    async def get_public_url(
        self,
        object_id: str,
        file_name: str,
        ttl_in_minutes: int = PUBLIC_IMAGE_TTL,
        content_type: typing.Optional[str] = None,
    ) -> str:
        blob = await self.bucket.get_blob(self.get_full_object_id(object_id))
        query_params = {
            "response-content-disposition": f"attachment; filename={file_name}"
        }

        if content_type:
            query_params["response-content-type"] = content_type

        return await blob.get_signed_url(
            int(timedelta(minutes=ttl_in_minutes).total_seconds()),
            query_params=query_params,
        )

    async def remove(self, object_id: str):
        await self._get_client().delete(
            self._bucket_name, self.get_full_object_id(object_id)
        )
