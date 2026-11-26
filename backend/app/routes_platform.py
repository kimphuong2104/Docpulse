"""HTTP và WebSocket cho workspace đã đăng nhập."""

import json
import uuid
from typing import Any

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, Request, UploadFile, WebSocket
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.accounts import (
    PlatformError,
    create_key,
    create_workspace_page,
    home,
    invite_member,
    list_keys,
    list_members,
    list_tasks,
    list_workspace_pages,
    login,
    logout,
    overview,
    read_workspace_page,
    refresh,
    register,
    rename_page,
    require_workspace,
    revoke_key,
    update_task,
    user_from_api_key,
    user_from_token,
)
from app.files import accept_file, read_file
from app.indexing import replay_event
from app.pages import VersionConflict, save_blocks
from app.realtime import join, leave
from app.runtime import iter_ai, search, telemetry

router = APIRouter()


class AuthBody(BaseModel):
    email: str
    password: str
    workspace_name: str = "Workspace"


class RefreshBody(BaseModel):
    refresh_token: str = ""


class PageBody(BaseModel):
    title: str = ""


class InviteBody(BaseModel):
    email: str
    role: str = "editor"


class KeyBody(BaseModel):
    name: str = "Pipeline key"


class TaskBody(BaseModel):
    status: str


class BlockBody(BaseModel):
    id: str
    type: str
    content: dict[str, Any] = Field(default_factory=dict)


class BlocksBody(BaseModel):
    version: int
    blocks: list[BlockBody] = Field(default_factory=list, max_length=400)


class AiBody(BaseModel):
    prompt: str = Field(min_length=2)
    page_id: str | None = None
    scope: str = "workspace"
    attachment_page_ids: list[str] = Field(default_factory=list, max_length=8)


def _trace(request: Request) -> str:
    return getattr(request.state, "trace_id", str(uuid.uuid4()))


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _raise(exc: PlatformError) -> None:
    raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


def _actor(authorization: str | None, api_key: str | None) -> dict[str, Any]:
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    secret = api_key or (token if token.startswith("dp_live_") else "")
    try:
        if secret:
            user, _workspace = user_from_api_key(secret)
            return user
        if token:
            return user_from_token(token)
    except PlatformError as exc:
        _raise(exc)
    raise HTTPException(status_code=401, detail="Thiếu access token.")


@router.post("/auth/register", status_code=201)
def register_route(payload: AuthBody, request: Request) -> dict[str, Any]:
    try:
        return register(payload.email, payload.password, payload.workspace_name, _client(request))
    except PlatformError as exc:
        _raise(exc)


@router.post("/auth/login")
def login_route(payload: AuthBody, request: Request) -> dict[str, Any]:
    try:
        return login(payload.email, payload.password, _client(request))
    except PlatformError as exc:
        _raise(exc)


@router.post("/auth/refresh")
def refresh_route(payload: RefreshBody) -> dict[str, Any]:
    try:
        return refresh(payload.refresh_token)
    except PlatformError as exc:
        _raise(exc)


@router.post("/auth/logout")
def logout_route(payload: RefreshBody) -> dict[str, bool]:
    logout(payload.refresh_token)
    return {"logged_out": True}


@router.get("/workspaces/{slug}")
def workspace_overview(
    slug: str,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return overview(_actor(authorization, x_api_key)["id"], slug)
    except PlatformError as exc:
        _raise(exc)


@router.get("/workspaces/{slug}/home")
def workspace_home(
    slug: str,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return home(_actor(authorization, x_api_key)["id"], slug)
    except PlatformError as exc:
        _raise(exc)


@router.get("/workspaces/{slug}/pages")
def workspace_pages(
    slug: str,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return list_workspace_pages(_actor(authorization, x_api_key)["id"], slug)
    except PlatformError as exc:
        _raise(exc)


@router.post("/workspaces/{slug}/pages", status_code=201)
def workspace_add_page(
    slug: str,
    payload: PageBody,
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return create_workspace_page(_actor(authorization, x_api_key)["id"], slug, payload.title, _trace(request))
    except PlatformError as exc:
        _raise(exc)


@router.get("/workspaces/{slug}/pages/{page_id}")
def workspace_page(
    slug: str,
    page_id: str,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return read_workspace_page(_actor(authorization, x_api_key)["id"], slug, page_id)
    except PlatformError as exc:
        _raise(exc)


@router.patch("/workspaces/{slug}/pages/{page_id}")
def workspace_rename(
    slug: str,
    page_id: str,
    payload: PageBody,
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return rename_page(_actor(authorization, x_api_key)["id"], slug, page_id, payload.title, _trace(request))
    except PlatformError as exc:
        _raise(exc)


@router.put("/workspaces/{slug}/pages/{page_id}/blocks")
def workspace_blocks(
    slug: str,
    page_id: str,
    payload: BlocksBody,
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        user = _actor(authorization, x_api_key)
        require_workspace(user["id"], slug, write=True)
        read_workspace_page(user["id"], slug, page_id)
        saved = save_blocks(
            page_id,
            payload.version,
            [block.model_dump() for block in payload.blocks],
            actor_id=user["id"],
            trace_id=_trace(request),
        )
        return read_workspace_page(user["id"], slug, saved["id"])
    except PlatformError as exc:
        _raise(exc)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Không tìm thấy trang.") from exc
    except VersionConflict as exc:
        raise HTTPException(status_code=409, detail="Trang đã được lưu ở một phiên khác.") from exc


@router.post("/workspaces/{slug}/files", status_code=201)
async def workspace_upload(
    slug: str,
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form("import"),
    page_id: str | None = Form(default=None),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    payload = await file.read()
    try:
        return accept_file(
            _actor(authorization, x_api_key)["id"],
            slug,
            filename=file.filename or "",
            payload=payload,
            mode=mode,
            page_id=page_id or None,
            trace_id=_trace(request),
        )
    except PlatformError as exc:
        _raise(exc)
    finally:
        await file.close()


@router.get("/workspaces/{slug}/files/{file_id}")
def workspace_file(
    slug: str,
    file_id: str,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return read_file(_actor(authorization, x_api_key)["id"], slug, file_id)
    except PlatformError as exc:
        _raise(exc)


@router.get("/workspaces/{slug}/search")
def workspace_search(
    slug: str,
    q: str = Query(min_length=2),
    scope: str = "workspace",
    page_id: str | None = None,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return search(_actor(authorization, x_api_key)["id"], slug, q, page_id=page_id, scope=scope)
    except PlatformError as exc:
        _raise(exc)


@router.post("/workspaces/{slug}/ai/stream")
def workspace_ai(
    slug: str,
    payload: AiBody,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> StreamingResponse:
    user = _actor(authorization, x_api_key)

    def generate():
        try:
            for item in iter_ai(
                user["id"],
                slug,
                payload.prompt,
                page_id=payload.page_id,
                scope=payload.scope,
                extra_page_ids=payload.attachment_page_ids,
            ):
                yield f"event: {item['event']}\ndata: {json.dumps(item['data'], ensure_ascii=False)}\n\n"
        except PlatformError as exc:
            yield f"event: error\ndata: {json.dumps({'detail': exc.detail})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/workspaces/{slug}/system")
def workspace_system(
    slug: str,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return telemetry(_actor(authorization, x_api_key)["id"], slug)
    except PlatformError as exc:
        _raise(exc)


@router.post("/workspaces/{slug}/outbox/{event_id}/replay")
def workspace_replay(
    slug: str,
    event_id: str,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        user = _actor(authorization, x_api_key)
        workspace = require_workspace(user["id"], slug, write=True)
        replayed = replay_event(workspace["id"], event_id)
    except PlatformError as exc:
        _raise(exc)
    if not replayed:
        raise HTTPException(status_code=404, detail="Chỉ chạy lại được event đang failed hoặc dead của workspace này.")
    return replayed


@router.get("/workspaces/{slug}/members")
def members_route(
    slug: str,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return list_members(_actor(authorization, x_api_key)["id"], slug)
    except PlatformError as exc:
        _raise(exc)


@router.post("/workspaces/{slug}/members")
def invite_route(
    slug: str,
    payload: InviteBody,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return invite_member(_actor(authorization, x_api_key)["id"], slug, payload.email, payload.role)
    except PlatformError as exc:
        _raise(exc)


@router.get("/workspaces/{slug}/keys")
def keys_route(
    slug: str,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return list_keys(_actor(authorization, x_api_key)["id"], slug)
    except PlatformError as exc:
        _raise(exc)


@router.post("/workspaces/{slug}/keys", status_code=201)
def create_key_route(
    slug: str,
    payload: KeyBody,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return create_key(_actor(authorization, x_api_key)["id"], slug, payload.name)
    except PlatformError as exc:
        _raise(exc)


@router.post("/workspaces/{slug}/keys/{key_id}/revoke")
def revoke_key_route(
    slug: str,
    key_id: str,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return revoke_key(_actor(authorization, x_api_key)["id"], slug, key_id)
    except PlatformError as exc:
        _raise(exc)


@router.get("/workspaces/{slug}/tasks")
def tasks_route(
    slug: str,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return list_tasks(_actor(authorization, x_api_key)["id"], slug)
    except PlatformError as exc:
        _raise(exc)


@router.patch("/workspaces/{slug}/tasks/{task_id}")
def task_route(
    slug: str,
    task_id: str,
    payload: TaskBody,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return update_task(_actor(authorization, x_api_key)["id"], slug, task_id, payload.status)
    except PlatformError as exc:
        _raise(exc)


@router.get("/workspaces/{slug}/quota")
def quota_route(
    slug: str,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        user = _actor(authorization, x_api_key)
        snapshot = telemetry(user["id"], slug)
    except PlatformError as exc:
        _raise(exc)
    return {"tokens": snapshot["tokens"], "cache": snapshot["cache"], "latency": snapshot["latency"]}


@router.websocket("/ws/{slug}/pages/{page_id}")
async def page_socket(socket: WebSocket, slug: str, page_id: str) -> None:
    await socket.accept()
    try:
        hello = await socket.receive_json()
        user = user_from_token(str(hello.get("token") or ""))
        require_workspace(user["id"], slug)
        read_workspace_page(user["id"], slug, page_id)
    except Exception:
        await socket.send_json({"type": "error", "detail": "Không mở được kênh trang."})
        await socket.close(code=4401)
        return
    await join(page_id, socket)
    await socket.send_json({"type": "ready", "page_id": page_id})
    try:
        while True:
            message = await socket.receive_json()
            if message.get("type") == "ping":
                await socket.send_json({"type": "pong"})
    except Exception:
        await leave(page_id, socket)
