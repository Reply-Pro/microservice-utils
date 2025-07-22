import json
from typing import List, Dict, Any


def estimate_vector_size(vector: List[float]) -> int:
    """Estimate the size of a vector in bytes."""
    # Each float is typically 8 bytes in Python
    return len(vector) * 8


def estimate_metadata_size(metadata: Dict[str, Any]) -> int:
    """Estimate the size of metadata in bytes."""
    # Convert to JSON string to get approximate size
    return len(json.dumps(metadata).encode("utf-8"))


def calculate_batch_size(items: List[dict], max_batch_size_mb: int = 2) -> int:
    """
    Calculate the optimal batch size for upsert operations based on item sizes.

    Args:
        items: List of items to be upserted, each containing 'values' and optionally
        'metadata' max_batch_size_mb: Maximum batch size in megabytes (default: 2)

    Returns:
        Recommended batch size that won't exceed Pinecone's limits
    """
    if not items:
        return 0

    max_batch_size_bytes = max_batch_size_mb * 1024 * 1024  # Convert MB to bytes
    max_vectors_per_batch = 1000
    max_metadata_size = 40 * 1024  # 40KB in bytes

    # Sample the first few items to estimate average size
    sample_size = min(10, len(items))
    total_estimated_size = 0

    for item in items[:sample_size]:
        # Estimate vector size
        vector_size = estimate_vector_size(item.get("values", []))

        # Estimate metadata size
        metadata_size = 0
        if "metadata" in item:
            metadata_size = estimate_metadata_size(item["metadata"])
            if metadata_size > max_metadata_size:
                raise ValueError(
                    f"Metadata size ({metadata_size} bytes) "
                    f"exceeds maximum allowed (40KB)"
                )

        # Add some overhead for JSON structure and id
        item_size = (
            vector_size + metadata_size + len(item.get("id", "")) + 100
        )  # 100 bytes overhead
        total_estimated_size += item_size

    # Calculate average item size
    avg_item_size = total_estimated_size / sample_size

    # Calculate batch size based on size limit
    size_based_batch_size = int(max_batch_size_bytes / avg_item_size)

    # Return the minimum of size-based batch size and maximum allowed vectors
    return min(size_based_batch_size, max_vectors_per_batch)
