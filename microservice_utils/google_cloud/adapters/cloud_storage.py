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
    PUBLIC_URL_TTL = 30

    def __init__(self, bucket_name: str, delimiter: str = None):
        self._bucket_name = bucket_name
        self._delimiter = delimiter or "/"

    async def __aenter__(self) -> "GcsObjectRepository":
        self._client = Storage()

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._client.close()

        self._client = None
        self._namespace = None

    @property
    def bucket(self) -> Bucket:
        return Bucket(self._get_client(), self._bucket_name)

    def _get_client(self) -> Storage:
        if self._client:
            return self._client
        else:
            raise RuntimeError("The GCS Storage client has not been instantiated.")

    def get_full_object_id(self, object_id: str) -> str:
        if self._namespace is None:
            raise RuntimeError("Namespace not set!")

        if self._namespace:
            return f"{self._namespace}{self._delimiter}{object_id}"

        return object_id

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
        ttl_in_minutes: int = PUBLIC_URL_TTL,
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

    def set_namespace(self, namespace: str):
        self._namespace = namespace


if __name__ == "__main__":
    import argparse
    import asyncio
    from uuid import uuid4

    parser = argparse.ArgumentParser(
        description="Manually test the GcsObjectRepository adapter."
    )
    parser.add_argument(
        "action",
        choices=["add", "get", "get_public_url", "remove"],
        help="Action to perform.",
    )
    parser.add_argument("--object_id", help="Object ID to use for the action.")
    parser.add_argument("--content", type=str, help="Content for add action.")
    parser.add_argument(
        "--bucket_name", type=str, required=True, help="Bucket name to operate on."
    )
    parser.add_argument(
        "--namespace", type=str, required=True, help="Namespace to operate on."
    )
    args = parser.parse_args()

    async def main():
        repository = GcsObjectRepository(bucket_name=args.bucket_name)
        repository.set_namespace(args.namespace)
        object_id = args.object_id or str(uuid4())
        content_type = "text/plain"
        file_name = "test.txt"

        async with repository as repo:
            if args.action == "add":
                if args.content:
                    result = await repo.add(
                        object_id, args.content.encode(), content_type
                    )
                    print(f"Object added with ID: {result}")
                else:
                    print("Content is required for add action.")
            elif args.action == "get":
                obj = await repo.get(object_id)
                print(f"Content: {obj.content}, Metadata: {obj.metadata}")
            elif args.action == "get_public_url":
                url = await repo.get_public_url(object_id, file_name)
                print(f"Public URL: {url}")
            elif args.action == "remove":
                await repo.remove(object_id)
                print(f"Object {object_id} removed.")

    asyncio.run(main())
