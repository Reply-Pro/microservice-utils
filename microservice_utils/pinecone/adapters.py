import argparse
import typing
import re

from dataclasses import dataclass
from pinecone.db_data.dataclasses.vector import Vector
from pinecone.pinecone import Pinecone
from pprint import pprint
from sentence_transformers import SentenceTransformer
from uuid import uuid4

from microservice_utils.pinecone.utils import calculate_batch_size

EMBEDDING_MODEL = "all-MiniLM-L6-v2"


@dataclass
class Document:
    """
    Represents a document with content, metadata, and an optional identifier.

    This class serves as a wrapper for textual content, associated metadata,
    and an optional unique identifier that can be used for various purposes
    such as indexing or tracking.

    Attributes:
    content: The textual content of the document.
    metadata: A dictionary representing metadata associated with the
              document, such as title, author, or publication date.
    id: Optional unique identifier for the document. Defaults to None.
    """

    content: str
    metadata: dict
    id: str = None


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
        self._client = Pinecone(api_key=api_key, environment=environment)
        self._index_name = index_name
        self._namespace = namespace
        self.embedding_model = SentenceTransformer(EMBEDDING_MODEL)

    def _get_index(self):
        return self._client.Index(name=self._index_name)

    @property
    def index(self):
        return self._get_index()

    @property
    def namespaces(self) -> list[str]:
        return [namespace.name for namespace in self.index.list_namespaces()]

    def add_document(self, document: Document, namespace: str) -> list[str]:
        """
        Add a document to a specific namespace
        """
        if not document.id:
            document.id = str(uuid4())

        # Split document into chunks
        chunks = self.chunk_text(document.content)
        count = len(chunks)
        chunk_ids = []
        self.set_namespace(namespace)

        # Process each chunk
        items_to_upsert = []
        batch_size = None
        for i, chunk in enumerate(chunks):
            # Generate chunk ID
            chunk_id = f"{document.id}_chunk_{i}"

            # Generate embedding for chunk
            embedding = self.embedding_model.encode([chunk])[0]

            # Prepare item for Pinecone
            item = {
                "id": chunk_id,
                "values": embedding.tolist(),
                "metadata": {
                    "content": chunk,
                    "chunk_index": i,
                    "total_chunks": count,
                    **document.metadata,
                },
            }

            # Store in Pinecone
            items_to_upsert.append(item)
            if not batch_size and len(items_to_upsert) >= 10:
                batch_size = calculate_batch_size(items_to_upsert) - 1
            if i + 1 == count or (batch_size and len(items_to_upsert) >= batch_size):
                self.upsert(items_to_upsert)
                items_to_upsert = []
            chunk_ids.append(chunk_id)
        return chunk_ids

    @staticmethod
    def chunk_text(
        text: str, chunk_size: int = 1000, chunk_overlap: int = 200
    ) -> list[str]:
        """
        Split text into overlapping chunks of a specified size
        """
        # Clean and normalize text
        text = re.sub(r"\s+", " ", text).strip()

        # If the text is shorter than the chunk size, return it as is
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0
        text_length = len(text)

        while start < text_length:
            # Get a chunk of text
            end = start + chunk_size
            chunk = text[start:end]

            # If not at the end, try to break at sentence or word boundary
            if end < text_length:
                # Try to find a sentence boundary
                sentence_break = chunk.rfind(".")
                word_break = chunk.rfind(" ")

                # Use the closest boundary found
                break_point = int(max(sentence_break, word_break))
                if break_point != -1:
                    chunk = chunk[: break_point + 1]
                    end = start + break_point + 1

            chunks.append(chunk.strip())
            start = end - chunk_overlap

        return chunks

    def delete(self, ids: typing.List[str]):
        """Remove items from the Pinecone index."""
        self.index.delete(ids=ids, namespace=self._namespace)

    def query(
        self,
        queries: typing.List[typing.Iterable[float]],
        limit: int = 1,
    ) -> list[EmbeddingResult]:
        """Query the Pinecone index."""
        results = self.index.query(
            vector=queries[0],
            top_k=limit,
            include_metadata=True,
            namespace=self._namespace,
        )

        results = results["matches"]
        return [
            EmbeddingResult(
                id=r["id"],
                score=r["score"],
                values=r["values"],
                metadata=r.get("metadata"),
            )
            for r in results
        ]

    def remove_document(self, document_id: str, namespace: str = None) -> None:
        """
        Remove a document and all its associated chunks from the specified namespace.

        Args:
            document_id: The ID of the document to remove
            namespace: Optional namespace to remove the document from.
                      If not provided, uses the current namespace.
        """
        if not document_id:
            raise ValueError("Document ID is required")
        if namespace:
            self.set_namespace(namespace)

        # Get all vectors to find how many chunks exist
        results = self.index.query(
            vector=[0.0] * 384,  # Stand-in vector to match an embedding dimension
            filter={"id": {"$startsWith": f"{document_id}_chunk_"}},
            namespace=self._namespace,
            include_metadata=False,
        )

        # Extract all chunk IDs for this document
        chunk_ids = [match["id"] for match in results["matches"]]

        if chunk_ids:
            # Delete all chunks at once
            self.delete(chunk_ids)

    def search(
        self,
        query: str,
        namespaces: list[str] = None,
        _filter: typing.Optional[
            typing.Dict[str, typing.Union[str, float, int, bool, typing.List, dict]]
        ] = None,
        limit: int = 3,
    ) -> list[EmbeddingResult]:
        """
        Search across specified namespaces
        """
        # Use configured namespaces if none specified
        search_namespaces = namespaces or self.namespaces

        # Generate query embedding
        query_embedding = self.embedding_model.encode([query])[0]

        # Search in each namespace
        results = self.index.query_namespaces(
            vector=query_embedding.tolist(),
            top_k=limit,
            include_metadata=True,
            namespaces=search_namespaces,
            metric="cosine",
            filter=_filter,
        )

        results = results["matches"]
        results.sort(key=lambda x: x["score"], reverse=True)
        return [
            EmbeddingResult(
                id=r["id"],
                score=r["score"],
                values=r["values"],
                metadata=r.get("metadata"),
            )
            for r in results
        ]

    def set_namespace(self, namespace: str):
        self._namespace = namespace

    def upsert(
        self,
        items: typing.Union[typing.List[Vector], typing.List[tuple], typing.List[dict]],
    ):
        """Upsert items to the Pinecone index."""
        self.index.upsert(items, namespace=self._namespace)


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

    def add_document(args):
        adapter = PineconeAdapter(
            args.api_key, args.index_name, args.environment, namespace=args.namespace
        )

        docs = [args.data]
        embeddings = adapter.embedding_model.encode(docs)
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

        query_embedding = adapter.embedding_model.encode([args.data])

        query_vector = query_embedding[0].tolist()

        query_results = adapter.query([query_vector], limit=10)

        print("Query results")
        pprint(query_results)

    def delete_documents(args):
        adapter = PineconeAdapter(
            args.api_key, args.index_name, args.environment, namespace=args.namespace
        )
        ids = [args.data]
        adapter.delete(ids)
        print(f"Deleted vectors with ids: {ids}")

    def add_document_with_adapter(args):
        def dcode(code):
            from base64 import b64decode

            try:
                return b64decode(code).decode()
            except Exception as e:
                print(e.__repr__())
                return code

        doc_id = str(uuid4())

        print(f"Adding document with document_id:\n{doc_id}")

        adapter = PineconeAdapter(
            dcode(args.api_key), args.index_name, args.environment, namespace=doc_id
        )

        with open(args.file, "r") as f:
            data = f.read()

        file_name = args.file.rsplit("/", maxsplit=1)[-1]
        print(f"file:\n{file_name}")

        # Create a Document object with the provided data
        document = Document(
            content=data,
            metadata={"file_name": file_name},  # Add basic metadata
            id=doc_id,  # Let the adapter generate an ID
        )

        # Add the document and get the chunk IDs
        chunk_ids = adapter.add_document(document, namespace=doc_id)
        print(f"Added document with chunk IDs: {chunk_ids}")

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

    # Update the add-parser to use the new function
    adapt_parser = subparsers.add_parser(
        "adapt", help="Add a document using the adapter"
    )
    adapt_parser.add_argument("--file", type=str, required=True, help="Document string")
    adapt_parser.set_defaults(func=add_document_with_adapter)

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
    arguments = parser.parse_args()
    arguments.func(arguments)
