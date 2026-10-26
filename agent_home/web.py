"""Loopback-only HTTP API and dashboard assets."""

import asyncio
import dataclasses
from pathlib import Path

from aiohttp import web

from .codex import DEFAULT_SOCKET
from .service import Bridge
from .utils import safe_error

BRIDGE_KEY = web.AppKey("bridge", Bridge)
FRONTEND_BUILD = Path(__file__).parent / "static" / "build"


def create_app(store, config=None, config_path=None, database_locks=None):
    bridge = Bridge(store, config, config_path, database_locks)

    @web.middleware
    async def security(request, handler):
        if request.host.split(":")[0] not in {"127.0.0.1", "localhost"}:
            raise web.HTTPForbidden(text="Invalid Host")
        if request.headers.get("Origin") not in {None, f"http://{request.host}"}:
            raise web.HTTPForbidden(text="Cross-origin requests are disabled")
        if request.path.startswith("/api/"):
            if request.headers.get("Sec-Fetch-Site") == "cross-site":
                raise web.HTTPForbidden(text="Cross-site requests are disabled")
            if request.method == "POST" and request.content_type != "application/json":
                raise web.HTTPUnsupportedMediaType(text="Use application/json")
        try:
            if request.path.startswith("/api/") and request.method == "POST":
                if not isinstance(await request.json(), dict):
                    raise ValueError("Request body must be a JSON object")
            response = await handler(request)
        except (ValueError, KeyError, TypeError) as error:
            response = web.json_response({"error": safe_error(error)}, status=400)
        except (RuntimeError, ConnectionError, OSError, asyncio.TimeoutError) as error:
            response = web.json_response({"error": safe_error(error)}, status=502)
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "Content-Security-Policy":
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    app = web.Application(middlewares=[security], client_max_size=16384)
    app[BRIDGE_KEY] = bridge
    app.on_startup.append(bridge.start)
    app.on_cleanup.append(bridge.stop)

    async def static(request):
        return web.FileResponse(FRONTEND_BUILD / "index.html")

    async def state(request):
        identities = []
        for name, identity in store.identities().items():
            telegram = dataclasses.asdict(identity.telegram) if identity.telegram else None
            if telegram:
                telegram.pop("token")
            sleeve = dataclasses.asdict(identity.sleeve) if identity.sleeve else None
            identities.append({"id": identity.id, "name": name, "telegram": telegram,
                "allow_external_media": identity.allow_external_media,
                "sleeve": sleeve, "binding": sleeve,
                "sleeve_process": bridge.sleeve_process(name),
                "runtime": bridge.sleeve_status.get(name, {"status": "ready" if bridge.is_admin(name) else "checking" if sleeve else "unassigned"}),
                "polling": name in bridge.pollers and not bridge.pollers[name].done()})
        return web.json_response({"identities": identities, "default_socket": str(DEFAULT_SOCKET),
            "sleeve_allowed_folders": [str(p) for p in bridge.config.sleeve_allowed_folders],
            "external_identities": [identity.to_dict() for identity in store.external_identities()],
            "unregistered_senders": bridge.unregistered_senders(), "events": store.data["events"],
            "pending_deliveries": len(store.data["outbox"]),
            "uncertain_instructions": [{"id": job["id"], "identity": job["identity"]}
                for job in store.data["inbox"] if job["status"] == "uncertain"]})

    async def threads(request):
        return web.json_response(await bridge.loaded_root_threads(request.query.get("socket_path", str(DEFAULT_SOCKET))))

    async def add_identity(request):
        body = await request.json()
        if set(body) - {"name", "telegram", "allow_external_media"}:
            raise ValueError("Unknown identity settings")
        identity = await bridge.create_identity(body["name"], body.get("telegram"),
                                               allow_external_media=body.get("allow_external_media", True))
        return web.json_response({"id": identity.id, "name": identity.name,
            "username": identity.telegram.username if identity.telegram else None,
            "bot_id": identity.telegram.bot_id if identity.telegram else None}, status=201)

    async def binding(request):
        body = await request.json()
        kind = body.get("kind", "codex")
        if kind == "admin_app":
            if set(body) != {"kind"}:
                raise ValueError("AdminAppSleeve accepts only its kind; it has no Codex address")
            await bridge.assign_sleeve(request.match_info["name"], kind="admin_app")
            return web.json_response({"sleeve": {"kind": "admin_app"}})
        if kind != "codex":
            raise ValueError("Unknown sleeve kind")
        if set(body) - {"kind", "thread_id", "socket_path"}:
            raise ValueError("Unknown sleeve settings")
        if body["thread_id"] is not None and not isinstance(body["thread_id"], str):
            raise ValueError("thread_id must be a full UUID or null to detach")
        thread = await bridge.assign_sleeve(request.match_info["name"], body["thread_id"], body.get("socket_path", str(DEFAULT_SOCKET)))
        return web.json_response({"thread_id": thread["id"], "name": thread.get("name"), "cwd": thread["cwd"]} if thread else {"sleeve": None})

    async def communication(request):
        name = request.match_info["name"]
        bridge.identity(name)
        body = await request.json()
        if set(body) - {"telegram", "allow_external_media"}:
            raise ValueError("Unknown communication settings")
        await bridge.configure_communication(name, body.get("telegram", ...),
                                            allow_external_media=body.get("allow_external_media", ...))
        return web.json_response({"updated": True})

    async def settings(request):
        return web.json_response(bridge.settings())

    async def save_settings(request):
        return web.json_response(await bridge.configure_settings(await request.json()))

    async def create_sleeve(request):
        body = await request.json()
        if set(body) != {"folder", "request_id"}:
            raise ValueError("Sleeve creation accepts folder and request_id only")
        return web.json_response(await bridge.create_sleeve(body["folder"], body["request_id"]), status=201)

    async def recover_sleeve(request):
        body = await request.json()
        if (set(body) != {"thread_id", "socket_path"} or not isinstance(body["thread_id"], str)
                or not isinstance(body["socket_path"], str)):
            raise ValueError("Recovery requires the current thread_id and socket_path")
        return web.json_response(await bridge.recover_sleeve(request.match_info["name"], **body))

    async def external_identities(request):
        return web.json_response([identity.to_dict() for identity in store.external_identities()])

    async def add_external_identity(request):
        identity = bridge.create_external_identity(await request.json())
        return web.json_response(identity.to_dict(), status=201)

    async def update_external_identity(request):
        identity = bridge.update_external_policy(request.match_info["identity_id"], await request.json())
        return web.json_response(identity.to_dict())

    app.router.add_get("/", static)
    app.router.add_static("/assets/", FRONTEND_BUILD / "assets")
    app.router.add_get("/api/state", state)
    app.router.add_get("/api/settings", settings)
    app.router.add_post("/api/settings", save_settings)
    app.router.add_post("/api/sleeves", create_sleeve)
    app.router.add_get("/api/threads", threads)
    app.router.add_post("/api/identities", add_identity)
    app.router.add_post("/api/identities/{name}/binding", binding)
    app.router.add_post("/api/identities/{name}/sleeve", binding)
    app.router.add_post("/api/identities/{name}/sleeve/recover", recover_sleeve)
    app.router.add_post("/api/identities/{name}/communication", communication)
    app.router.add_get("/api/external-identities", external_identities)
    app.router.add_post("/api/external-identities", add_external_identity)
    app.router.add_post("/api/external-identities/{identity_id}", update_external_identity)
    return app
