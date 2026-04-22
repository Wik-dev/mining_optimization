"""
RAG Ingest Workflow — builds a vector index from the organizational knowledge corpus.

DAG: load_documents → chunk_documents → embed_chunks → build_index → build_receipt

Triggered once (or whenever the corpus changes) to produce ``index.json``, which
is then consumed at query time by the ``knowledge_query`` catalog task.

Single-file usage:
    python workflow.py run rag_ingest --params '{"input_file": "azure://container/path/doc.txt"}'

Multi-file usage:
    python workflow.py run rag_ingest --params '{"source_prefix": "azure://container/path/prefix/"}'
"""
from validance import Task, Workflow

# Image built from Dockerfile.rag-tasks. Bundles python-dotenv, httpx, numpy,
# azure-storage-blob and bakes in modules/rag/ + tasks/knowledge_query.py.
RAG_IMAGE = "rag-tasks:latest"


def create_rag_ingest_workflow() -> Workflow:
    """Create the RAG document ingestion workflow."""
    workflow = Workflow("rag_ingest")

    load_documents = Task(
        name="load_documents",
        docker_image=RAG_IMAGE,
        command="python modules/rag/tasks/load_documents.py",
        inputs={"input.txt": "${input_file}"},
        output_files={"result": "documents.json"},
        timeout=600,
    )

    chunk_documents = Task(
        name="chunk_documents",
        docker_image=RAG_IMAGE,
        command="python modules/rag/tasks/chunk_documents.py documents.json",
        inputs={"documents.json": "@load_documents:result"},
        output_files={"result": "chunks.json"},
        depends_on=["load_documents"],
        timeout=300,
    )

    embed_chunks = Task(
        name="embed_chunks",
        docker_image=RAG_IMAGE,
        command="python modules/rag/tasks/embed_chunks.py chunks.json",
        inputs={"chunks.json": "@chunk_documents:result"},
        output_files={"result": "embeddings.json"},
        depends_on=["chunk_documents"],
        timeout=600,
    )

    build_index = Task(
        name="build_index",
        docker_image=RAG_IMAGE,
        command="python modules/rag/tasks/build_index.py chunks.json embeddings.json",
        inputs={
            "chunks.json": "@chunk_documents:result",
            "embeddings.json": "@embed_chunks:result",
        },
        output_files={"result": "index.json"},
        depends_on=["embed_chunks"],
        timeout=300,
    )

    build_receipt = Task(
        name="build_receipt",
        docker_image=RAG_IMAGE,
        command="python modules/rag/tasks/build_receipt.py documents.json chunks.json embeddings.json index.json",
        inputs={
            "documents.json": "@load_documents:result",
            "chunks.json": "@chunk_documents:result",
            "embeddings.json": "@embed_chunks:result",
            "index.json": "@build_index:result",
        },
        environment={"WORKFLOW_NAME": "rag_ingest"},
        output_files={"result": "receipt.json"},
        depends_on=["build_index"],
        timeout=300,
    )

    workflow.add_task(load_documents)
    workflow.add_task(chunk_documents)
    workflow.add_task(embed_chunks)
    workflow.add_task(build_index)
    workflow.add_task(build_receipt)

    return workflow


WORKFLOWS = {
    "rag_ingest": create_rag_ingest_workflow,
}
