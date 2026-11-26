"""Tài khoản, workspace, thành viên và các bản ghi mà giao diện đọc trực tiếp."""

import re
import uuid
from typing import Any

from psycopg.types.json import Json

from app.db import connect
from app.indexing import enqueue_index, text_from_blocks
from app.security import (
    ACCESS_TTL,
    REFRESH_TTL,
    decode_access_token,
    hash_password,
    hash_token,
    issue_access_token,
    new_refresh_token,
    verify_password,
)

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class PlatformError(Exception):
    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(detail)


def consume_rate(bucket: str, limit: int) -> bool:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rate_buckets (bucket, window_start, hits)
                VALUES (%s, date_trunc('minute', CURRENT_TIMESTAMP), 1)
                ON CONFLICT (bucket) DO UPDATE SET
                    hits = CASE
                        WHEN rate_buckets.window_start = date_trunc('minute', CURRENT_TIMESTAMP)
                        THEN rate_buckets.hits + 1
                        ELSE 1
                    END,
                    window_start = date_trunc('minute', CURRENT_TIMESTAMP)
                RETURNING hits
                """,
                (bucket,),
            )
            hits = int(cur.fetchone()[0])
        conn.commit()
    return hits <= limit


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
    return slug or "workspace"


def _display_name(email: str) -> str:
    local = email.split("@", 1)[0].replace(".", " ").replace("_", " ").replace("-", " ")
    return " ".join(part.capitalize() for part in local.split()) or email


def _user(row: tuple) -> dict[str, Any]:
    return {"id": str(row[0]), "email": row[1], "display_name": row[2]}


def _store_refresh(cur, user_id: str, token: str) -> None:
    cur.execute(
        """
        INSERT INTO refresh_tokens (user_id, token_hash, expires_at)
        VALUES (%s::uuid, %s, CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'))
        """,
        (user_id, hash_token(token), REFRESH_TTL),
    )


def _issue(user: dict[str, Any], workspace: dict[str, Any]) -> dict[str, Any]:
    refresh = new_refresh_token()
    with connect() as conn:
        with conn.cursor() as cur:
            _store_refresh(cur, user["id"], refresh)
        conn.commit()
    return {
        "access_token": issue_access_token(user["id"], user["email"]),
        "refresh_token": refresh,
        "expires_in": ACCESS_TTL,
        "user": user,
        "workspace": workspace,
    }


def _workspace_for_user(cur, user_id: str) -> dict[str, Any]:
    cur.execute(
        """
        SELECT w.id::text, w.slug, w.name, m.role
        FROM workspace_members m
        JOIN workspaces w ON w.id = m.workspace_id
        WHERE m.user_id = %s::uuid
        ORDER BY m.created_at
        LIMIT 1
        """,
        (user_id,),
    )
    row = cur.fetchone()
    if not row:
        raise PlatformError(404, "Tài khoản chưa thuộc workspace nào.")
    return {"id": row[0], "slug": row[1], "name": row[2], "role": row[3]}


def user_from_token(token: str) -> dict[str, Any]:
    try:
        payload = decode_access_token(token)
    except ValueError as exc:
        raise PlatformError(401, str(exc)) from exc
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id::text, email, display_name FROM users WHERE id = %s::uuid", (payload["sub"],))
            row = cur.fetchone()
    if not row:
        raise PlatformError(401, "Tài khoản không còn tồn tại.")
    return _user(row)


def user_from_api_key(secret: str) -> tuple[dict[str, Any], dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.id::text, u.email, u.display_name, w.id::text, w.slug, w.name, m.role
                FROM api_keys k
                JOIN users u ON u.id = k.created_by
                JOIN workspaces w ON w.id = k.workspace_id
                JOIN workspace_members m ON m.workspace_id = w.id AND m.user_id = u.id
                WHERE k.key_hash = %s AND k.revoked_at IS NULL
                """,
                (hash_token(secret),),
            )
            row = cur.fetchone()
    if not row:
        raise PlatformError(401, "API key không hợp lệ.")
    return _user(row[:3]), {"id": row[3], "slug": row[4], "name": row[5], "role": row[6]}


def require_workspace(user_id: str, slug: str, *, write: bool = False, owner: bool = False) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT w.id::text, w.slug, w.name, w.token_quota, m.role
                FROM workspaces w
                JOIN workspace_members m ON m.workspace_id = w.id
                WHERE w.slug = %s AND m.user_id = %s::uuid
                """,
                (slug, user_id),
            )
            row = cur.fetchone()
    if not row:
        raise PlatformError(404, "Không có workspace này hoặc bạn không phải thành viên.")
    role = row[4]
    if owner and role != "owner":
        raise PlatformError(403, "Chỉ owner được thực hiện thao tác này.")
    if write and role == "viewer":
        raise PlatformError(403, "Viewer chỉ được đọc.")
    return {"id": row[0], "slug": row[1], "name": row[2], "token_quota": int(row[3]), "role": role}


def register(email: str, password: str, workspace_name: str, client_key: str) -> dict[str, Any]:
    if not consume_rate(f"register:{client_key}", 10):
        raise PlatformError(429, "Quá nhiều lượt đăng ký. Hãy thử lại sau một phút.")
    email = email.strip().lower()
    if not _EMAIL.match(email):
        raise PlatformError(400, "Email không hợp lệ.")
    if len(password) < 8:
        raise PlatformError(400, "Mật khẩu cần ít nhất 8 ký tự.")
    name = (workspace_name or "Workspace").strip()[:80] or "Workspace"
    display = _display_name(email)
    user_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                raise PlatformError(409, "Email này đã có tài khoản.")
            slug = _unique_slug(cur, _slug(name))
            cur.execute(
                "INSERT INTO users (id, email, display_name, password_hash) VALUES (%s::uuid, %s, %s, %s)",
                (user_id, email, display, hash_password(password)),
            )
            cur.execute(
                "INSERT INTO workspaces (id, slug, name) VALUES (%s::uuid, %s, %s)",
                (workspace_id, slug, name),
            )
            cur.execute(
                """
                INSERT INTO workspace_members (workspace_id, user_id, role)
                VALUES (%s::uuid, %s::uuid, 'owner')
                """,
                (workspace_id, user_id),
            )
            _seed(cur, workspace_id, user_id, trace_id)
            cur.execute(
                """
                INSERT INTO activity_events (workspace_id, actor_id, action, target, detail)
                VALUES (%s::uuid, %s::uuid, 'created', %s, 'Workspace và tài liệu mẫu đã được tạo, indexing nằm trong outbox.')
                """,
                (workspace_id, user_id, workspace_id),
            )
        conn.commit()
    user = {"id": user_id, "email": email, "display_name": display}
    return _issue(user, {"id": workspace_id, "slug": slug, "name": name, "role": "owner"})


def _unique_slug(cur, base: str) -> str:
    slug = base
    for suffix in range(2, 50):
        cur.execute("SELECT 1 FROM workspaces WHERE slug = %s", (slug,))
        if not cur.fetchone():
            return slug
        slug = f"{base}-{suffix}"
    return f"{base}-{uuid.uuid4().hex[:6]}"


def _block(kind: str, text: str, **extra: Any) -> dict[str, Any]:
    content = {"text": text, "checked": False, "view": "table", "rows": []}
    content.update(extra)
    return {"type": kind, "content": content}


def _seed(cur, workspace_id: str, user_id: str, trace_id: str) -> None:
    pages = [
        (
            "Distributed Queue & Worker Pipeline",
            "⚡",
            False,
            0,
            {"status": "Active", "tags": ["outbox", "worker", "dlq"], "section": "Architecture"},
            [
                _block(
                    "paragraph",
                    "Saving a page commits the blocks and a page.index.requested outbox row in the same PostgreSQL transaction. The worker claims that row with FOR UPDATE SKIP LOCKED.",
                ),
                _block(
                    "heading2",
                    "Fault tolerance",
                ),
                _block(
                    "paragraph",
                    "Temporary embedding and network failures are scheduled again with exponential backoff and jitter. After the attempt limit the event becomes dead, keeps the error and trace id, and can be replayed from System Monitor.",
                ),
                _block(
                    "paragraph",
                    "Search filters every keyword and vector hit by the caller's workspace. A viewer can read. Editing pages, API keys, membership, and dead-letter replay require editor or owner.",
                ),
                _block("database", "Workspace tasks", source="tasks"),
            ],
        ),
        (
            "Database Schema",
            "🗄",
            False,
            1,
            {"status": "Active", "tags": ["postgres"], "section": "Architecture"},
            [
                _block(
                    "paragraph",
                    "Identity lives in users, workspaces, and workspace_members. Page content lives in workspace_pages and workspace_blocks. page_chunks stores the searchable text and optional vectors. outbox_events is the queue.",
                ),
                _block(
                    "paragraph",
                    "usage_events records AI latency, token estimates, and cache hits. rate_buckets counts requests per minute. api_keys stores only a hash of each secret.",
                ),
            ],
        ),
        (
            "Caching Strategy",
            "◎",
            False,
            2,
            {"status": "Active", "tags": ["cache", "quota"], "section": "Architecture"},
            [
                _block(
                    "paragraph",
                    "An AI answer is cached by a hash of the prompt and the retrieved chunk text. A later identical question in the same workspace returns that answer and counts a cache hit. The cache entry is not reused after the underlying chunks change.",
                ),
                _block(
                    "paragraph",
                    "Each workspace has a token quota. AI calls stop with HTTP 429 when the recorded usage reaches that quota. Login, register, and AI routes also use a one-minute rate bucket.",
                ),
            ],
        ),
        (
            "API Specifications",
            "📘",
            True,
            3,
            {"status": "Active", "tags": ["http", "sse"], "section": "Favorites"},
            [
                _block(
                    "paragraph",
                    "POST /auth/register and /auth/login issue an access JWT and a refresh token. POST /auth/refresh rotates the refresh token and revokes the previous one. Reuse of a revoked refresh token revokes the user's remaining refresh tokens.",
                ),
                _block(
                    "paragraph",
                    "Workspace routes require the access token. PUT /workspaces/{slug}/pages/{id}/blocks saves with a version check. POST /workspaces/{slug}/ai/stream streams a grounded answer and does not write blocks. The client inserts a proposal only after Accept.",
                ),
            ],
        ),
        (
            "Operator Checklist",
            "☑",
            True,
            4,
            {"status": "Active", "tags": ["ops"], "section": "Favorites"},
            [
                _block(
                    "paragraph",
                    "Use System Monitor to see the worker heartbeat, queue depth, dead letters, cache-hit ratio, and AI latency percentiles computed from usage_events.",
                ),
            ],
        ),
    ]
    for title, icon, favorite, position, properties, blocks in pages:
        page_id = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO workspace_pages (
                id, workspace_id, created_by, title, icon, position, favorite, properties
            )
            VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s)
            """,
            (page_id, workspace_id, user_id, title, icon, position, favorite, Json(properties)),
        )
        for index, block in enumerate(blocks):
            cur.execute(
                """
                INSERT INTO workspace_blocks (id, page_id, block_type, content, position)
                VALUES (%s::uuid, %s::uuid, %s, %s, %s)
                """,
                (str(uuid.uuid4()), page_id, block["type"], Json(block["content"]), index),
            )
        enqueue_index(
            cur,
            page_id=page_id,
            workspace_id=workspace_id,
            version=1,
            plain=text_from_blocks(title, blocks),
            trace_id=trace_id,
            actor_id=user_id,
        )
    tasks = [
        ("Review a dead-letter replay", "Todo", "High", "Worker"),
        ("Confirm search stays inside this workspace", "Todo", "High", "Retrieval"),
        ("Watch the worker heartbeat", "Todo", "Medium", "Telemetry"),
        ("Check token usage after an AI answer", "Todo", "Medium", "Quota"),
    ]
    for title, status, priority, area in tasks:
        cur.execute(
            """
            INSERT INTO workspace_tasks (workspace_id, title, status, priority, area, assignee_id)
            VALUES (%s::uuid, %s, %s, %s, %s, %s::uuid)
            """,
            (workspace_id, title, status, priority, area, user_id),
        )


def login(email: str, password: str, client_key: str) -> dict[str, Any]:
    if not consume_rate(f"login:{client_key}", 20):
        raise PlatformError(429, "Quá nhiều lượt đăng nhập. Hãy thử lại sau một phút.")
    email = email.strip().lower()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id::text, email, display_name, password_hash FROM users WHERE email = %s",
                (email,),
            )
            row = cur.fetchone()
            if not row or not verify_password(password, row[3]):
                raise PlatformError(401, "Email hoặc mật khẩu không đúng.")
            workspace = _workspace_for_user(cur, row[0])
    return _issue(_user(row[:3]), workspace)


def refresh(token: str) -> dict[str, Any]:
    if not token:
        raise PlatformError(401, "Thiếu refresh token.")
    digest = hash_token(token)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, user_id::text, revoked_at
                FROM refresh_tokens
                WHERE token_hash = %s AND expires_at > CURRENT_TIMESTAMP
                FOR UPDATE
                """,
                (digest,),
            )
            row = cur.fetchone()
            if not row:
                raise PlatformError(401, "Refresh token không còn dùng được.")
            if row[2] is not None:
                cur.execute(
                    "UPDATE refresh_tokens SET revoked_at = CURRENT_TIMESTAMP WHERE user_id = %s::uuid AND revoked_at IS NULL",
                    (row[1],),
                )
                conn.commit()
                raise PlatformError(401, "Refresh token đã bị thu hồi. Hãy đăng nhập lại.")
            cur.execute("SELECT id::text, email, display_name FROM users WHERE id = %s::uuid", (row[1],))
            user_row = cur.fetchone()
            if not user_row:
                raise PlatformError(401, "Tài khoản không còn tồn tại.")
            workspace = _workspace_for_user(cur, row[1])
            replacement = new_refresh_token()
            cur.execute(
                """
                INSERT INTO refresh_tokens (user_id, token_hash, expires_at)
                VALUES (%s::uuid, %s, CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'))
                RETURNING id::text
                """,
                (row[1], hash_token(replacement), REFRESH_TTL),
            )
            new_id = cur.fetchone()[0]
            cur.execute(
                "UPDATE refresh_tokens SET revoked_at = CURRENT_TIMESTAMP, replaced_by = %s::uuid WHERE id = %s::uuid",
                (new_id, row[0]),
            )
        conn.commit()
    user = _user(user_row)
    return {
        "access_token": issue_access_token(user["id"], user["email"]),
        "refresh_token": replacement,
        "expires_in": ACCESS_TTL,
        "user": user,
        "workspace": workspace,
    }


def logout(token: str) -> None:
    if not token:
        return
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE refresh_tokens SET revoked_at = CURRENT_TIMESTAMP WHERE token_hash = %s AND revoked_at IS NULL",
                (hash_token(token),),
            )
        conn.commit()


def overview(user_id: str, slug: str) -> dict[str, Any]:
    workspace = require_workspace(user_id, slug)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.display_name, u.email, m.role
                FROM workspace_members m
                JOIN users u ON u.id = m.user_id
                WHERE m.workspace_id = %s::uuid
                ORDER BY m.created_at
                """,
                (workspace["id"],),
            )
            members = [
                {"display_name": row[0], "email": row[1], "role": row[2], "initials": _initials(row[0])}
                for row in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT COUNT(*),
                       COUNT(*) FILTER (WHERE s.status = 'indexed' AND s.vector_ready)
                FROM workspace_pages p
                LEFT JOIN page_index_state s ON s.page_id = p.id
                WHERE p.workspace_id = %s::uuid AND p.archived = FALSE
                """,
                (workspace["id"],),
            )
            total, indexed = cur.fetchone()
    return {
        "workspace": workspace,
        "members": members,
        "pages": int(total),
        "indexed": int(indexed),
    }


def _initials(name: str) -> str:
    parts = [part for part in name.split() if part]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def list_workspace_pages(user_id: str, slug: str) -> dict[str, Any]:
    workspace = require_workspace(user_id, slug)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id::text, p.title, p.icon, p.favorite, p.version, p.updated_at, p.properties,
                       COALESCE(s.status, 'pending'), COALESCE(s.vector_ready, FALSE), COALESCE(s.chunk_count, 0)
                FROM workspace_pages p
                LEFT JOIN page_index_state s ON s.page_id = p.id
                WHERE p.workspace_id = %s::uuid AND p.archived = FALSE
                ORDER BY p.favorite DESC, p.position, p.created_at
                """,
                (workspace["id"],),
            )
            pages = [
                {
                    "id": row[0],
                    "title": row[1],
                    "icon": row[2],
                    "favorite": bool(row[3]),
                    "version": int(row[4]),
                    "updated_at": row[5].isoformat() if row[5] else None,
                    "properties": row[6] if isinstance(row[6], dict) else {},
                    "index_status": row[7],
                    "vector_ready": bool(row[8]),
                    "chunk_count": int(row[9]),
                }
                for row in cur.fetchall()
            ]
    return {"pages": pages, "total": len(pages)}


def _assert_page(cur, workspace_id: str, page_id: str) -> None:
    cur.execute(
        "SELECT 1 FROM workspace_pages WHERE id = %s::uuid AND workspace_id = %s::uuid",
        (page_id, workspace_id),
    )
    if not cur.fetchone():
        raise PlatformError(404, "Không tìm thấy trang trong workspace này.")


def read_workspace_page(user_id: str, slug: str, page_id: str) -> dict[str, Any]:
    workspace = require_workspace(user_id, slug)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id::text, p.title, p.icon, p.favorite, p.version, p.updated_at, p.properties,
                       u.display_name, COALESCE(s.status, 'pending'), COALESCE(s.vector_ready, FALSE),
                       s.error, COALESCE(s.chunk_count, 0), s.updated_at
                FROM workspace_pages p
                LEFT JOIN users u ON u.id = p.created_by
                LEFT JOIN page_index_state s ON s.page_id = p.id
                WHERE p.id = %s::uuid AND p.workspace_id = %s::uuid AND p.archived = FALSE
                """,
                (page_id, workspace["id"]),
            )
            page = cur.fetchone()
            if not page:
                raise PlatformError(404, "Không tìm thấy trang trong workspace này.")
            cur.execute(
                """
                SELECT id::text, block_type, content, position
                FROM workspace_blocks
                WHERE page_id = %s::uuid
                ORDER BY position, created_at
                """,
                (page_id,),
            )
            blocks = [
                {
                    "id": row[0],
                    "type": row[1],
                    "content": row[2] if isinstance(row[2], dict) else {},
                    "position": float(row[3]),
                }
                for row in cur.fetchall()
            ]
            from app.files import overlay_files

            blocks = overlay_files(cur, blocks)
    return {
        "id": page[0],
        "title": page[1],
        "icon": page[2],
        "favorite": bool(page[3]),
        "version": int(page[4]),
        "updated_at": page[5].isoformat() if page[5] else None,
        "properties": page[6] if isinstance(page[6], dict) else {},
        "owner_name": page[7],
        "index_status": page[8],
        "vector_ready": bool(page[9]),
        "index_error": page[10],
        "chunk_count": int(page[11]),
        "indexed_at": page[12].isoformat() if page[12] else None,
        "blocks": blocks,
        "role": workspace["role"],
    }


def create_workspace_page(user_id: str, slug: str, title: str, trace_id: str) -> dict[str, Any]:
    workspace = require_workspace(user_id, slug, write=True)
    page_id = str(uuid.uuid4())
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(MAX(position), -1) + 1
                FROM workspace_pages
                WHERE workspace_id = %s::uuid AND archived = FALSE
                """,
                (workspace["id"],),
            )
            position = float(cur.fetchone()[0])
            cur.execute(
                """
                INSERT INTO workspace_pages (id, workspace_id, created_by, title, icon, position, properties)
                VALUES (%s::uuid, %s::uuid, %s::uuid, %s, '📄', %s, %s)
                """,
                (page_id, workspace["id"], user_id, (title or "Untitled").strip()[:200], position, Json({"status": "Draft", "tags": []})),
            )
            cur.execute(
                """
                INSERT INTO workspace_blocks (id, page_id, block_type, content, position)
                VALUES (%s::uuid, %s::uuid, 'paragraph', %s, 0)
                """,
                (str(uuid.uuid4()), page_id, Json({"text": "", "checked": False, "view": "table", "rows": []})),
            )
            enqueue_index(
                cur,
                page_id=page_id,
                workspace_id=workspace["id"],
                version=1,
                plain=title or "Untitled",
                trace_id=trace_id,
                actor_id=user_id,
            )
        conn.commit()
    return read_workspace_page(user_id, slug, page_id)


def rename_page(user_id: str, slug: str, page_id: str, title: str, trace_id: str) -> dict[str, Any]:
    workspace = require_workspace(user_id, slug, write=True)
    with connect() as conn:
        with conn.cursor() as cur:
            _assert_page(cur, workspace["id"], page_id)
            cur.execute(
                """
                UPDATE workspace_pages
                SET title = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s::uuid
                RETURNING version
                """,
                (title.strip()[:200] or "Untitled", page_id),
            )
            version = int(cur.fetchone()[0])
            cur.execute(
                "SELECT block_type, content FROM workspace_blocks WHERE page_id = %s::uuid ORDER BY position",
                (page_id,),
            )
            blocks = [{"type": row[0], "content": row[1] if isinstance(row[1], dict) else {}} for row in cur.fetchall()]
            enqueue_index(
                cur,
                page_id=page_id,
                workspace_id=workspace["id"],
                version=version,
                plain=text_from_blocks(title, blocks),
                trace_id=trace_id,
                actor_id=user_id,
            )
        conn.commit()
    return read_workspace_page(user_id, slug, page_id)


def list_members(user_id: str, slug: str) -> dict[str, Any]:
    workspace = require_workspace(user_id, slug)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.id::text, u.display_name, u.email, m.role, m.created_at
                FROM workspace_members m
                JOIN users u ON u.id = m.user_id
                WHERE m.workspace_id = %s::uuid
                ORDER BY m.created_at
                """,
                (workspace["id"],),
            )
            members = [
                {
                    "id": row[0],
                    "display_name": row[1],
                    "email": row[2],
                    "role": row[3],
                    "joined_at": row[4].isoformat() if row[4] else None,
                }
                for row in cur.fetchall()
            ]
    return {"members": members, "role": workspace["role"]}


def invite_member(user_id: str, slug: str, email: str, role: str) -> dict[str, Any]:
    workspace = require_workspace(user_id, slug, owner=True)
    if role not in {"editor", "viewer"}:
        raise PlatformError(400, "Chỉ mời được editor hoặc viewer.")
    email = email.strip().lower()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id::text FROM users WHERE email = %s", (email,))
            found = cur.fetchone()
            if not found:
                raise PlatformError(404, "Chưa có tài khoản với email này. Người đó cần đăng ký trước.")
            cur.execute(
                "SELECT role FROM workspace_members WHERE workspace_id = %s::uuid AND user_id = %s::uuid",
                (workspace["id"], found[0]),
            )
            existing = cur.fetchone()
            if existing and existing[0] == "owner":
                raise PlatformError(400, "Không đổi vai trò của owner bằng lời mời.")
            cur.execute(
                """
                INSERT INTO workspace_members (workspace_id, user_id, role)
                VALUES (%s::uuid, %s::uuid, %s)
                ON CONFLICT (workspace_id, user_id) DO UPDATE SET role = EXCLUDED.role
                """,
                (workspace["id"], found[0], role),
            )
            cur.execute(
                """
                INSERT INTO activity_events (workspace_id, actor_id, action, target, detail)
                VALUES (%s::uuid, %s::uuid, 'invited', %s, %s)
                """,
                (workspace["id"], user_id, found[0], f"{email} → {role}"),
            )
        conn.commit()
    return list_members(user_id, slug)


def list_keys(user_id: str, slug: str) -> dict[str, Any]:
    workspace = require_workspace(user_id, slug)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT k.id::text, k.name, k.key_prefix, k.created_at, k.revoked_at, u.display_name
                FROM api_keys k
                LEFT JOIN users u ON u.id = k.created_by
                WHERE k.workspace_id = %s::uuid
                ORDER BY k.created_at DESC
                """,
                (workspace["id"],),
            )
            keys = [
                {
                    "id": row[0],
                    "name": row[1],
                    "prefix": row[2],
                    "created_at": row[3].isoformat() if row[3] else None,
                    "revoked_at": row[4].isoformat() if row[4] else None,
                    "created_by": row[5],
                }
                for row in cur.fetchall()
            ]
    return {"keys": keys}


def create_key(user_id: str, slug: str, name: str) -> dict[str, Any]:
    workspace = require_workspace(user_id, slug, owner=True)
    secret = "dp_live_" + uuid.uuid4().hex
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO api_keys (workspace_id, name, key_prefix, key_hash, created_by)
                VALUES (%s::uuid, %s, %s, %s, %s::uuid)
                RETURNING id::text
                """,
                (workspace["id"], (name or "Pipeline key").strip()[:80], secret[:16], hash_token(secret), user_id),
            )
            key_id = cur.fetchone()[0]
        conn.commit()
    return {"id": key_id, "secret": secret, "prefix": secret[:16]}


def revoke_key(user_id: str, slug: str, key_id: str) -> dict[str, Any]:
    workspace = require_workspace(user_id, slug, owner=True)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE api_keys
                SET revoked_at = CURRENT_TIMESTAMP
                WHERE id = %s::uuid AND workspace_id = %s::uuid AND revoked_at IS NULL
                RETURNING id::text
                """,
                (key_id, workspace["id"]),
            )
            if not cur.fetchone():
                raise PlatformError(404, "Không tìm thấy API key đang hoạt động.")
        conn.commit()
    return {"revoked": True, "id": key_id}


def list_tasks(user_id: str, slug: str) -> dict[str, Any]:
    workspace = require_workspace(user_id, slug)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT t.id::text, t.title, t.status, t.priority, t.area, u.display_name, t.updated_at
                FROM workspace_tasks t
                LEFT JOIN users u ON u.id = t.assignee_id
                WHERE t.workspace_id = %s::uuid
                ORDER BY t.created_at
                """,
                (workspace["id"],),
            )
            tasks = [
                {
                    "id": row[0],
                    "title": row[1],
                    "status": row[2],
                    "priority": row[3],
                    "area": row[4],
                    "assignee": row[5],
                    "updated_at": row[6].isoformat() if row[6] else None,
                }
                for row in cur.fetchall()
            ]
    return {"tasks": tasks}


def update_task(user_id: str, slug: str, task_id: str, status: str) -> dict[str, Any]:
    workspace = require_workspace(user_id, slug, write=True)
    allowed = {"Todo", "In Progress", "In Review", "Completed"}
    if status not in allowed:
        raise PlatformError(400, "Trạng thái nhiệm vụ không hợp lệ.")
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE workspace_tasks
                SET status = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s::uuid AND workspace_id = %s::uuid
                RETURNING id::text
                """,
                (status, task_id, workspace["id"]),
            )
            if not cur.fetchone():
                raise PlatformError(404, "Không tìm thấy nhiệm vụ.")
            cur.execute(
                """
                INSERT INTO activity_events (workspace_id, actor_id, action, target, detail)
                VALUES (%s::uuid, %s::uuid, 'task', %s, %s)
                """,
                (workspace["id"], user_id, task_id, status),
            )
        conn.commit()
    return list_tasks(user_id, slug)


def home(user_id: str, slug: str) -> dict[str, Any]:
    workspace = require_workspace(user_id, slug)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT display_name FROM users WHERE id = %s::uuid", (user_id,))
            display = cur.fetchone()[0]
            cur.execute(
                """
                SELECT p.id::text, p.title, p.icon, p.updated_at,
                       COALESCE(s.status, 'pending'), COALESCE(s.chunk_count, 0), COALESCE(s.vector_ready, FALSE)
                FROM workspace_pages p
                LEFT JOIN page_index_state s ON s.page_id = p.id
                WHERE p.workspace_id = %s::uuid AND p.archived = FALSE
                ORDER BY p.updated_at DESC
                LIMIT 4
                """,
                (workspace["id"],),
            )
            recent = [
                {
                    "id": row[0],
                    "title": row[1],
                    "icon": row[2],
                    "updated_at": row[3].isoformat() if row[3] else None,
                    "index_status": row[4],
                    "chunk_count": int(row[5]),
                    "vector_ready": bool(row[6]),
                }
                for row in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT id::text, title, status, priority, area
                FROM workspace_tasks
                WHERE workspace_id = %s::uuid
                ORDER BY created_at
                """,
                (workspace["id"],),
            )
            tasks = [
                {"id": row[0], "title": row[1], "status": row[2], "priority": row[3], "area": row[4]}
                for row in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT a.action, a.detail, a.created_at, COALESCE(u.display_name, 'Hệ thống'), COALESCE(p.title, a.target)
                FROM activity_events a
                LEFT JOIN users u ON u.id = a.actor_id
                LEFT JOIN workspace_pages p ON p.id::text = a.target
                WHERE a.workspace_id = %s::uuid
                ORDER BY a.created_at DESC
                LIMIT 12
                """,
                (workspace["id"],),
            )
            activity = [
                {
                    "action": row[0],
                    "detail": row[1],
                    "created_at": row[2].isoformat() if row[2] else None,
                    "actor": row[3],
                    "target": row[4],
                }
                for row in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT COUNT(*),
                       COUNT(*) FILTER (WHERE s.status = 'indexed')
                FROM workspace_pages p
                LEFT JOIN page_index_state s ON s.page_id = p.id
                WHERE p.workspace_id = %s::uuid AND p.archived = FALSE
                """,
                (workspace["id"],),
            )
            total, indexed = cur.fetchone()
    return {
        "display_name": display,
        "workspace": workspace,
        "recent": recent,
        "tasks": tasks,
        "activity": activity,
        "page_count": int(total),
        "indexed_count": int(indexed),
    }
