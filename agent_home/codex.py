"""Codex JSON-RPC over an existing local Unix socket."""

import asyncio
import contextlib
import json
import logging
from pathlib import Path

import aiohttp

from .utils import safe_error

LOG = logging.getLogger(__name__)
DEFAULT_SOCKET = Path.home() / ".codex/app-server-control/app-server-control.sock"


def is_root_thread(thread):
    source = thread.get("source")
    if isinstance(source, str):
        return source in {"cli", "vscode", "exec", "appServer"}
    return isinstance(source, dict) and set(source) == {"custom"} and isinstance(source["custom"], str)


class CodexClient:
    def __init__(self, socket_path, on_event):
        self.socket_path = socket_path
        self.on_event = on_event
        self.http = None
        self.ws = None
        self.reader = None
        self.pending = {}
        self.sequence = 0
        self.lock = asyncio.Lock()

    async def connect(self):
        async with self.lock:
            if self.ws and not self.ws.closed:
                return
            await self.close()
            self.http = aiohttp.ClientSession(
                connector=aiohttp.UnixConnector(path=self.socket_path),
                timeout=aiohttp.ClientTimeout(total=30),
            )
            try:
                self.ws = await self.http.ws_connect("http://localhost/", heartbeat=20, max_msg_size=64 * 1024 * 1024)
                self.reader = asyncio.create_task(self.read())
                await self.request("initialize", {"clientInfo": {
                    "name": "agent_communication", "title": "Agent Communication", "version": "0.1.0",
                }})
                await self.ws.send_json({"method": "initialized"})
            except BaseException:
                await self.close()
                raise

    async def request(self, method, params):
        if not self.ws or self.ws.closed:
            raise ConnectionError("Codex is disconnected")
        self.sequence += 1
        request_id = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.ws.send_json({"id": request_id, "method": method, "params": params})
            try:
                return await asyncio.wait_for(future, 60)
            except RuntimeError as error:
                empty_history = (f"thread {params.get('threadId')} is not materialized yet; "
                    "thread/turns/list is unavailable before first user message")
                if method == "thread/turns/list" and str(error) == empty_history:
                    return {"data": [], "nextCursor": None}
                raise
        finally:
            self.pending.pop(request_id, None)

    async def read(self):
        try:
            async for frame in self.ws:
                if frame.type != aiohttp.WSMsgType.TEXT:
                    continue
                message = json.loads(frame.data)
                if "method" not in message:
                    future = self.pending.get(message.get("id"))
                    if future and not future.done():
                        if "error" in message:
                            future.set_exception(RuntimeError(safe_error(message["error"].get("message", "Codex RPC error"))))
                        else:
                            future.set_result(message.get("result", {}))
                else:
                    # The reader must remain free to receive replies to RPCs started by handlers.
                    await self.on_event(self, message)
        except (aiohttp.ClientError, ValueError, OSError) as error:
            LOG.warning("Codex connection: %s", safe_error(error))
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("Codex connection closed"))
            await self.ws.close()

    async def close(self):
        if self.reader and self.reader is not asyncio.current_task():
            self.reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.reader
        if self.ws:
            await self.ws.close()
        if self.http:
            await self.http.close()
