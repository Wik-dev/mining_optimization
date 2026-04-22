"""
Load documents from an Azure Blob Storage prefix (multi-file) or a single local file.

Usage: python modules/rag/tasks/load_documents.py

Multi-file mode (env var CTX_SOURCE_PREFIX):
    Downloads all blobs under the given prefix and builds a documents array.
    Example prefix: azure://workflow-data/knowledge-bases/bitcoin-academy/sources/

Single-file fallback (env var CTX_INPUT_FILE or CLI arg):
    Reads a single input.txt already downloaded by the orchestrator.

Env vars (set by orchestrator):
    CTX_SOURCE_PREFIX: Azure blob prefix URI (multi-file mode)
    CTX_INPUT_FILE: Single file URI (backward compat)
    AZURE_STORAGE_ACCOUNT: Storage account name
    AZURE_STORAGE_KEY: Storage account key
"""
import hashlib
import json
import os
import sys
from pathlib import Path

from modules.rag.tasks import load_rag_env


def parse_azure_uri(uri: str) -> tuple[str, str]:
    """Parse azure://container/path into (container, prefix)."""
    # azure://workflow-data/knowledge-bases/bitcoin-academy/sources/
    stripped = uri.replace("azure://", "")
    parts = stripped.split("/", 1)
    container = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""
    return container, prefix


def load_from_prefix(source_prefix: str) -> list[dict]:
    """Download all blobs under prefix, return list of document dicts."""
    from azure.storage.blob import BlobServiceClient

    account = os.environ["AZURE_STORAGE_ACCOUNT"]
    key = os.environ["AZURE_STORAGE_KEY"]

    container_name, prefix = parse_azure_uri(source_prefix)
    connection_string = (
        f"DefaultEndpointsProtocol=https;"
        f"AccountName={account};"
        f"AccountKey={key};"
        f"EndpointSuffix=core.windows.net"
    )
    blob_service = BlobServiceClient.from_connection_string(connection_string)
    container_client = blob_service.get_container_client(container_name)

    documents = []
    for blob in container_client.list_blobs(name_starts_with=prefix):
        # Skip directory markers and non-text files
        if blob.size == 0:
            continue

        blob_name = blob.name
        filename = os.path.basename(blob_name)
        if not filename:
            continue

        print(f"  Downloading: {blob_name} ({blob.size} bytes)")
        blob_client = container_client.get_blob_client(blob_name)
        data = blob_client.download_blob().readall()
        text = data.decode("utf-8")

        content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        # Use path relative to prefix (strip extension) so nested files
        # keep their folder context, e.g. "book_folder/ch01_intro"
        relative = blob_name[len(prefix):] if blob_name.startswith(prefix) else blob_name
        source_name = str(Path(relative).with_suffix(""))

        documents.append({
            "source_file": filename,
            "source_name": source_name,
            "text": text,
            "char_count": len(text),
            "content_hash": content_hash,
        })

    if not documents:
        print(f"ERROR: No files found under prefix: {source_prefix}", file=sys.stderr)
        sys.exit(1)

    return documents


def load_single_file(filepath: str) -> list[dict]:
    """Load a single local file, return as single-element documents list."""
    if not os.path.exists(filepath):
        print(f"ERROR: {filepath} not found in working directory", file=sys.stderr)
        sys.exit(1)

    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
    source_name = Path(filepath).stem

    return [{
        "source_file": os.path.basename(filepath),
        "source_name": source_name,
        "text": text,
        "char_count": len(text),
        "content_hash": content_hash,
    }]


def main():
    load_rag_env()

    source_prefix = os.environ.get("CTX_SOURCE_PREFIX")
    input_file = os.environ.get("CTX_INPUT_FILE")

    if source_prefix:
        print(f"Loading documents from prefix: {source_prefix}")
        documents = load_from_prefix(source_prefix)
    elif input_file or len(sys.argv) > 1:
        # Single-file fallback: orchestrator already downloaded input.txt
        local_path = sys.argv[1] if len(sys.argv) > 1 else "input.txt"
        filepath = os.path.join(os.getcwd(), local_path)
        print(f"Loading single document: {local_path}")
        documents = load_single_file(filepath)
    else:
        print("ERROR: Set CTX_SOURCE_PREFIX (multi-file) or CTX_INPUT_FILE (single-file)", file=sys.stderr)
        sys.exit(1)

    # Combined content hash for manifest
    combined = hashlib.sha256(
        "".join(d["content_hash"] for d in documents).encode()
    ).hexdigest()[:16]

    output = {
        "documents": documents,
        "document_count": len(documents),
        "manifest": {
            "step_type": "deterministic",
            "content_hash": combined,
            "file_manifests": [
                {"source_file": d["source_file"], "content_hash": d["content_hash"]}
                for d in documents
            ],
        },
    }

    with open("documents.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    total_chars = sum(d["char_count"] for d in documents)
    print(f"Wrote documents.json ({len(documents)} documents, {total_chars} total chars, hash={combined})")


if __name__ == "__main__":
    main()
