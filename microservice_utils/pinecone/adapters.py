import argparse
import typing
from dataclasses import dataclass
from pprint import pprint
from uuid import uuid4

import pinecone
from pinecone.core.client.models import Vector


@dataclass(frozen=True)
class EmbeddingResult:
    id: str
    score: float
    values: list[float]
    metadata: dict[str, typing.Any] = None


class PineconeAdapter:
    def __init__(
        self, api_key: str, index_name: str, environment: str, namespace: str = None
    ):
        self._api_key = api_key
        self._environment = environment
        self._index_name = index_name
        self._namespace = namespace

    def _get_index(self):
        pinecone.init(api_key=self._api_key, environment=self._environment)
        return pinecone.Index(index_name=self._index_name)

    def set_namespace(self, namespace: str):
        self._namespace = namespace

    def upsert(
        self,
        items: typing.Union[typing.List[Vector], typing.List[tuple], typing.List[dict]],
    ):
        """Upsert items to the Pinecone index."""
        index = self._get_index()
        index.upsert(items, namespace=self._namespace)

    def query(
        self,
        queries: typing.List[typing.Iterable[float]],
        limit: int = 1,
    ) -> list[EmbeddingResult]:
        """Query the Pinecone index."""
        index = self._get_index()
        results = index.query(
            queries=queries,
            top_k=limit,
            include_metadata=True,
            namespace=self._namespace,
        )

        results = results["results"][0]["matches"]
        return [
            EmbeddingResult(
                id=r["id"],
                score=r["score"],
                values=r["values"],
                metadata=r.get("metadata"),
            )
            for r in results
        ]

    def delete(self, ids: typing.List[str]):
        """Remove items from the Pinecone index."""
        index = self._get_index()
        index.delete(ids, namespace=self._namespace)


if __name__ == "__main__":
    """Use this script to manually test the Pinecone adapter.
    --
    Adding a document
    python microservice_utils/pinecone/adapters.py
    --api-key '6e3cdf98-fake-fake-fake-b0d0a55dc6b5' --index-name 'sandbox-documents'
    --environment 'asia-northeast1-gcp'
    --namespace '2e1dc7a8-9c06-441b-9fa5-c3f0bd7b7114' add --data 'i like dogs'
    --
    Querying documents
    python microservice_utils/pinecone/adapters.py
    --api-key '6e3cdf98-fake-fake-fake-b0d0a55dc6b5' --index-name 'sandbox-documents'
    --environment 'asia-northeast1-gcp'
    --namespace '2e1dc7a8-9c06-441b-9fa5-c3f0bd7b7114' query --data 'dog'
    """

    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer("all-MiniLM-L6-v2")
    except ImportError:
        print(
            "The sentence_transformers library is needed to test the Pinecone adapter."
        )
        exit()

    def add_document(args):
        adapter = PineconeAdapter(
            args.api_key, args.index_name, args.environment, namespace=args.namespace
        )

        docs = [args.data]
        embeddings = model.encode(docs)
        items = []
        ids = []

        for i in range(len(docs)):
            e = embeddings[i].tolist()
            doc_id = str(uuid4())

            items.append({"id": doc_id, "values": e, "metadata": {"len": len(docs[i])}})
            ids.append(doc_id)

        adapter.upsert(items)
        print(f"Upserted with ids: {ids}")

    def query_documents(args):
        adapter = PineconeAdapter(
            args.api_key, args.index_name, args.environment, namespace=args.namespace
        )

        query_embedding = model.encode([args.data])

        query_results = adapter.query(
            [[float(i) for i in query_embedding[0]]], limit=10
        )

        print("Query results")
        pprint(query_results)

    def delete_documents(args):
        adapter = PineconeAdapter(
            args.api_key, args.index_name, args.environment, namespace=args.namespace
        )
        ids = [args.data]
        adapter.delete(ids)
        print(f"Deleted vectors with ids: {ids}")

    parser = argparse.ArgumentParser(description="Add or query documents on Pinecone")
    parser.add_argument("--api-key", type=str, required=True, help="Pinecone API key")
    parser.add_argument(
        "--index-name", type=str, required=True, help="Your Pinecone index name."
    )
    parser.add_argument(
        "--environment", type=str, required=True, help="Pinecone environment."
    )
    parser.add_argument(
        "--namespace",
        type=str,
        required=False,
        default=None,
        help="Pinecone namespace. This can be the tenant id for multi-tenancy.",
    )
    subparsers = parser.add_subparsers(help="sub-command help")

    # Add document sub-command
    add_parser = subparsers.add_parser("add", help="Add a document")
    add_parser.add_argument("--data", type=str, required=True, help="Document string")
    add_parser.set_defaults(func=add_document)

    # Query documents sub-command
    query_parser = subparsers.add_parser("query", help="Query documents")
    query_parser.add_argument("--data", type=str, required=True, help="Query string")
    query_parser.set_defaults(func=query_documents)

    # Delete documents sub-command
    query_parser = subparsers.add_parser("delete", help="Delete documents")
    query_parser.add_argument(
        "--data", type=str, required=True, help="Document ids string"
    )
    query_parser.set_defaults(func=delete_documents)

    # Parse arguments and call sub-command function
    args = parser.parse_args()
    args.func(args)
