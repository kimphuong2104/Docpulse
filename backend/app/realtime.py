"""Phát hiện thay đổi trang giữa các process qua PostgreSQL LISTEN/NOTIFY."""

import asyncio
import threading
import time
from typing import Any

from fastapi import WebSocket

from app.db import connect

_rooms: dict[str, set[WebSocket]] = {}
_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_stop = threading.Event()


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def connection_count() -> int:
    with _lock:
        return sum(len(sockets) for sockets in _rooms.values())


async def join(page_id: str, socket: WebSocket) -> None:
    with _lock:
        _rooms.setdefault(page_id, set()).add(socket)


async def leave(page_id: str, socket: WebSocket) -> None:
    with _lock:
        sockets = _rooms.get(page_id)
        if not sockets:
            return
        sockets.discard(socket)
        if not sockets:
            _rooms.pop(page_id, None)


def publish_reload(page_id: str) -> None:
    if _loop is None or not page_id:
        return
    asyncio.run_coroutine_threadsafe(_fanout(page_id), _loop)


async def _fanout(page_id: str) -> None:
    with _lock:
        sockets = list(_rooms.get(page_id, ()))
    for socket in sockets:
        try:
            await socket.send_json({"type": "changed", "page_id": page_id})
        except Exception:
            await leave(page_id, socket)


def start_listener() -> None:
    return


def stop_listener() -> None:
    _stop.set()


def _listen() -> None:
    while not _stop.is_set():
        try:
            with connect() as conn:
                conn.autocommit = True
                conn.execute("LISTEN docpulse_page")
                while not _stop.is_set():
                    for notify in conn.notifies(timeout=0.05):
                        publish_reload(notify.payload)
                    time.sleep(0.4)
        except Exception as exc:
            print({"msg": "listen_restart", "detail": str(exc)[:200]}, flush=True)
            time.sleep(2)


def room_snapshot() -> dict[str, Any]:
    with _lock:
        connections = sum(len(sockets) for sockets in _rooms.values())
        return {"connections": connections, "rooms": len(_rooms)}
