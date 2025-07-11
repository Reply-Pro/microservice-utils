import argparse
import typing
import re

from dataclasses import dataclass
from microservice_utils.openai.adapters import OpenAiLlm
from pinecone import Pinecone, Vector
from pprint import pprint
from sentence_transformers import SentenceTransformer
from uuid import uuid4


EMBEDDING_MODEL = "all-MiniLM-L6-v2"

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

    def _get_index(self):
        return self._client.Index(index_name=self._index_name)

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


@dataclass
class Document:
    content: str
    metadata: dict
    id: str = None

class PineconeAssistant:
    def __init__(
            self,
            openai_api_key: str,
            pinecone_api_key: str,
            pinecone_env: str,
            index_name: str,
            namespaces: list[str],
    ):
        # Initialize the embedding model
        self.embedding_model = SentenceTransformer(EMBEDDING_MODEL)

        # Initialize Pinecone client
        self.pinecone = PineconeAdapter(
            api_key=pinecone_api_key,
            index_name=index_name,
            environment=pinecone_env
        )

        # Store namespaces
        self.namespaces = namespaces

        # Initialize OpenAI LLM
        self.llm = OpenAiLlm(api_key=openai_api_key)

        # Conversation history
        self.chat_history = []

    @staticmethod
    def chunk_text(text: str, chunk_size: int = 1000, chunk_overlap: int = 200) -> list[str]:
        """
        Split text into overlapping chunks of a specified size
        """
        # Clean and normalize text
        text = re.sub(r'\s+', ' ', text).strip()

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
                sentence_break = chunk.rfind('.')
                word_break = chunk.rfind(' ')

                # Use the closest boundary found
                break_point = int(max(sentence_break, word_break))
                if break_point != -1:
                    chunk = chunk[:break_point + 1]
                    end = start + break_point + 1

            chunks.append(chunk.strip())
            start = end - chunk_overlap

        return chunks

    def add_document(self, document: Document, namespace: str) -> list[str]:
        """
        Add a document to a specific namespace
        """
        if not document.id:
            document.id = str(uuid4())
        if namespace not in self.namespaces:
            raise ValueError(f"Namespace {namespace} not configured")

        # Split document into chunks
        chunks = self.chunk_text(document.content)
        count = len(chunks)
        chunk_ids = []

        # Process each chunk
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
                    **document.metadata
                }
            }

            # Store in Pinecone
            self.pinecone.set_namespace(namespace)
            self.pinecone.upsert([item])
            chunk_ids.append(chunk_id)
        return chunk_ids

    def search(
            self,
            query: str,
            namespaces: list[str] = None,
            limit: int = 3
    ) -> list[dict]:
        """
        Search across specified namespaces
        """
        # Use configured namespaces if none specified
        search_namespaces = namespaces if namespaces else self.namespaces

        # Validate namespaces
        invalid_namespaces = set(search_namespaces) - set(self.namespaces)
        if invalid_namespaces:
            raise ValueError(f"Invalid namespaces: {invalid_namespaces}")

        # Generate query embedding
        query_embedding = self.embedding_model.encode([query])[0]

        # Search in each namespace
        results = []
        for namespace in search_namespaces:
            self.pinecone.set_namespace(namespace)
            namespace_results = self.pinecone.query(
                queries=[query_embedding.tolist()],
                limit=limit
            )
            for result in namespace_results:
                results.append({
                    "id": result.id,
                    "score": result.score,
                    "metadata": result.metadata,
                    "namespace": namespace
                })

        # Sort by score
        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def ask(
            self,
            question: str,
            namespaces: list[str] = None
    ) -> dict:
        """
        Ask a question and get an AI-generated response based on searched documents
        """
        # Search for relevant documents
        search_results = self.search(question, namespaces=namespaces)

        if not search_results:
            return {
                "answer": "I couldn't find any relevant information to answer your question.",
                "sources": []
            }

        # Prepare context from search results
        context = "\n\n".join([
            f"[From {r['namespace']}]: {r['metadata']['content']}"
            for r in search_results
        ])

        # Prepare prompt
        prompt = ("Based on the following information, please answer the question.\n"
        "If you cannot answer the question based on the provided information, say so.\n"

        "\nInformation:\n"
        f"{context}\n\n"

        f"Question: {question}")

        # Get response from LLM
        response = self.llm.generate_response(prompt)

        # Update chat history
        self.chat_history.append((question, response))

        return {
            "answer": response,
            "sources": search_results
        }

    def clear_history(self):
        """Clear conversation history"""
        self.chat_history = []


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
        model = SentenceTransformer(EMBEDDING_MODEL)
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
    arguments = parser.parse_args()
    arguments.func(arguments)
