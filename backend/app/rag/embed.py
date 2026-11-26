import time

from google import genai
from openai import OpenAI

from app.config import EMBEDDING_API_KEY, EMBEDDING_MODEL, EMBEDDING_PROVIDER, OPENAI_BASE_URL


def _call_with_retry(func):
    delay = 2.0
    for attempt in range(6):
        try:
            return func()
        except Exception as exc:
            message = str(exc)
            retryable = any(token in message for token in ("429", "503", "RESOURCE_EXHAUSTED", "timed out", "Timeout"))
            if attempt == 5 or not retryable:
                raise
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("Embedding failed.")


def embed_query(text: str) -> list[float]:
    if EMBEDDING_PROVIDER == "gemini":
        client = genai.Client(api_key=EMBEDDING_API_KEY)

        def call():
            resp = client.models.embed_content(model=EMBEDDING_MODEL, contents=text)
            return list(resp.embeddings[0].values)

        return _call_with_retry(call)

    client = OpenAI(api_key=EMBEDDING_API_KEY, base_url=OPENAI_BASE_URL or None)
    resp = client.embeddings.create(model=EMBEDDING_MODEL, input=text)
    return list(resp.data[0].embedding)
