"""
RAG task entry points for Validance workflow containers.

Each module is a standalone script: python modules/rag/tasks/<name>.py <args>

NOTE: This __init__.py intentionally does NOT import from modules.rag
to avoid triggering heavy dependencies (httpx, numpy) at import time.
Tasks that need those modules import them directly.
"""
from pathlib import Path


def load_rag_env():
    """Load module defaults from modules/rag/.env (won't override existing env vars)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)
