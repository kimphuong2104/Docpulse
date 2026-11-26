import psycopg
from pgvector.psycopg import register_vector

from app.config import DATABASE_URL


def connect(*, vectors: bool = False):
    conn = psycopg.connect(DATABASE_URL, connect_timeout=5, options="-c statement_timeout=8000")
    if vectors:
        register_vector(conn)
    return conn
