import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")

DATABASE_URL = os.environ["DATABASE_URL"]
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "25"))

EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "gemini").strip().lower()
EMBEDDING_API_KEY = os.environ["EMBEDDING_API_KEY"]
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "3072"))

LLM_PROVIDER = os.getenv("LLM_PROVIDER", EMBEDDING_PROVIDER).strip().lower()
LLM_API_KEY = os.getenv("LLM_API_KEY", EMBEDDING_API_KEY)
LLM_MODEL = os.getenv(
    "LLM_MODEL",
    "gemini-2.5-flash" if LLM_PROVIDER == "gemini" else "gpt-4o-mini",
)
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")

_IN_CONTAINER = str(Path(__file__).resolve()).startswith("/app/")
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/data/uploads" if _IN_CONTAINER else str(REPO_ROOT / "data" / "uploads"))
JWT_SECRET = os.getenv("JWT_SECRET", "docpulse-local-jwt-secret-change-me")
