"""Message routing, authorization, and sleeve lifecycle orchestration."""

import asyncio
import dataclasses
from pathlib import Path
import secrets
import time
import uuid

from .admin import AdminAppCommands
from .codex import CodexClient, DEFAULT_SOCKET, is_root_thread
from .config import ServiceConfig
from .incoming import incoming_media, media_input, save_incoming_media
from .sleeves import allowed_folder, codex_executable, start_daemon, start_worker, worker_running
from .models import AgentIdentity, AgentSleeve, AdminAppSleeve, TelegramIdentity, ExternalIdentity, CommunicationPolicy
from .media import MediaError, media_references
from .telegram import TelegramAPIError, TelegramClient
from .utils import TOKEN_PATTERN, safe_error, text_chunks
from .voice import VoiceError, save_voice, voice_instruction


class Bridge:
    def __init__(self, store, config=None, config_path=None, database_locks=None):
        self.store = store
        self.config = config or ServiceConfig(db_path=store.path)
        self.config_path = Path(config_path).expanduser().resolve() if config_path else None
        self.database_locks = database_locks
        self.telegram = TelegramClient()
        self.clients = {}
        self.tasks = []
        self.pollers = {}
        self.connected = {}
        self.approvals = {}
        self.locks = {}
        self.configuration_lock = asyncio.Lock()
        self.sleeve_status = {}
        self.sleeve_process_status = {}
        self.admin = AdminAppCommands(self)

    def settings(self):
        return {"db_path": str(self.store.path), "port": self.config.port,
            "sleeve_allowed_folders": [str(p) for p in self.config.sleeve_allowed_folders],
            "config_path": str(self.config_path) if self.config_path else None,
            "editable": self.config_path is not None and self.database_locks is not None}

    async def configure_settings(self, body):
        if not isinstance(body, dict) or set(body) != {"db_path", "sleeve_allowed_folders"}:
            raise ValueError("Settings require db_path and sleeve_allowed_folders")
        if not self.config_path or not self.database_locks:
            raise ValueError("Start with --config to persist application settings")
        if not isinstance(body["sleeve_allowed_folders"], list):
            raise ValueError("sleeve_allowed_folders must be a list")
        if not isinstance(body["db_path"], str) or not body["db_path"].strip():
            raise ValueError("db_path must be a non-empty database file path")
        async with self.configuration_lock:
            config = ServiceConfig.load(self.config_path, db_path=body["db_path"],
                sleeve_allowed_folders=body["sleeve_allowed_folders"])
            if config.port != self.config.port:
                raise ValueError("The port was changed in the config file; restart the app before saving Settings")
            if config.db_path != self.store.path and config.db_path.exists():
                raise ValueError("Choose a new database file; existing files are never overwritten")
            try:
                acquired = self.database_locks.acquire(config.db_path)
            except BlockingIOError:
                raise ValueError("Another service is using the destination database directory") from None
            try:
                self.store.relocate(config.db_path, lambda: config.save(self.config_path))
            except BaseException:
                self.database_locks.release(acquired)
                raise
            self.config = config
            self.store.event("Agent Home", "Application settings updated")
            return self.settings()

    async def create_sleeve(self, folder, request_id, *, check_authorization=None):
        if not isinstance(request_id, str):
            raise ValueError("request_id must be a UUID")
        request_id = str(uuid.UUID(request_id))
        async with self.configuration_lock:
            if check_authorization:
                check_authorization()
            path = allowed_folder(folder, self.config.sleeve_allowed_folders)
            launches = self.store.data.setdefault("sleeve_launches", {})
            prior = launches.get(request_id)
            if prior:
                if prior["folder"] != str(path):
                    raise ValueError("This creation request was already used for another folder")
                if prior["status"] == "running":
                    return prior
                raise ValueError("The previous launch was not confirmed. Check available root agents before creating another sleeve")
            executable = codex_executable()
            try:
                client = await self.client(str(DEFAULT_SOCKET))
            except OSError:
                if check_authorization:
                    check_authorization()
                await asyncio.to_thread(start_daemon, executable)
                client = await self.client(str(DEFAULT_SOCKET))
            if check_authorization:
                check_authorization()
            launch = {"socket_path": str(DEFAULT_SOCKET), "folder": str(path), "status": "starting"}
            launches[request_id] = launch
            self.store.save()
            try:
                started = await client.request("thread/start", {"cwd": str(path), "approvalPolicy": "never",
                    "sandbox": "danger-full-access", "ephemeral": False})
                thread = started["thread"]
                launch["thread_id"] = str(uuid.UUID(thread["id"]))
                self.store.save()
                if started.get("approvalPolicy") != "never" or started.get("sandbox", {}).get("type") != "dangerFullAccess":
                    raise RuntimeError("Codex did not grant the requested --yolo permissions; no CLI process was started")
                if not is_root_thread(thread) or Path(thread["cwd"]).resolve() != path:
                    raise RuntimeError("Codex did not create a root agent in the requested folder")
                if check_authorization:
                    check_authorization()
                initialized = await client.request("turn/start", {"threadId": thread["id"], "input": [{
                    "type": "text", "text": "Initialize this new Agent Home sleeve. Reply only: Ready. Do not inspect files or run tools; wait for the user's next message."}]})
                launch["initial_turn_id"] = initialized["turn"]["id"]
                self.store.save()
                try:
                    async with asyncio.timeout(60):
                        while True:
                            if check_authorization:
                                check_authorization()
                            try:
                                await client.request("thread/resume", {"threadId": thread["id"], "excludeTurns": True})
                                break
                            except RuntimeError as error:
                                if str(error) not in {f"no rollout found for thread id {thread['id']}",
                                                      "list_turns is not supported yet"}:
                                    raise
                                await asyncio.sleep(0.2)
                except asyncio.TimeoutError:
                    raise RuntimeError("Sleeve initialization was not confirmed. Check available root agents before creating another sleeve") from None
                if check_authorization:
                    check_authorization()
                launch.update(await asyncio.to_thread(start_worker, path, thread["id"], str(DEFAULT_SOCKET),
                    executable, self.store.directory/"sleeve-processes"/(thread["id"]+".log")))
            except BaseException:
                launch["status"] = "unconfirmed"
                self.store.save()
                raise
            launch["status"] = "running"
            self.store.event("Agent Home", "Independent Codex sleeve created: " + thread["id"])
            return launch

    def sleeve_launch(self, name):
        binding = self.store.data["sleeves"].get(name, {})
        if binding.get("kind", "codex") != "codex":
            return None
        return next((launch for launch in self.store.data.get("sleeve_launches", {}).values()
            if launch.get("thread_id") == binding.get("thread_id")
            and launch.get("socket_path") == binding.get("socket_path")), None)

    def sleeve_process(self, name):
        launch = self.sleeve_launch(name)
        if not launch:
            return None
        observation = self.sleeve_process_status.get(name, {})
        if observation.get("thread_id") != launch["thread_id"]:
            return {"status": "checking"}
        return observation

    async def recover_sleeve(self, name, thread_id, socket_path):
        async with self.configuration_lock, self.locks.setdefault(name, asyncio.Lock()):
            self.identity(name)
            binding = self.store.data["sleeves"].get(name, {})
            if binding.get("thread_id") != thread_id or binding.get("socket_path") != socket_path:
                raise ValueError("The current sleeve changed; refresh before recovering it")
            launch = self.sleeve_launch(name)
            if not launch or socket_path != str(DEFAULT_SOCKET):
                raise ValueError("Only a current Codex sleeve created by Agent Home can be restored here")
            executable = codex_executable()
            try:
                client = await self.client(socket_path)
            except OSError:
                await asyncio.to_thread(start_daemon, executable)
                client = await self.client(socket_path)
            thread = (await client.request("thread/read", {"threadId": thread_id}))["thread"]
            if thread.get("id") != thread_id or not is_root_thread(thread):
                raise ValueError("Only root agents can be sleeves")
            if Path(thread["cwd"]).resolve() != Path(launch["folder"]).resolve():
                raise ValueError("The saved sleeve's working folder changed; restore it manually")
            resumed = await client.request("thread/resume", {"threadId": thread_id, "excludeTurns": True})
            if resumed["thread"].get("id") != thread_id or not is_root_thread(resumed["thread"]):
                raise ValueError("Codex did not resume the saved root session")
            alive = await asyncio.to_thread(worker_running, launch.copy())
            if not alive:
                folder = allowed_folder(launch["folder"], self.config.sleeve_allowed_folders)
                if resumed.get("approvalPolicy") != "never" or resumed.get("sandbox", {}).get("type") != "dangerFullAccess":
                    raise ValueError("The sleeve's permissions changed; resume it manually to preserve its current settings")
                log_path = Path(launch.get("log_path") or self.store.directory/"sleeve-processes"/(thread_id+".log"))
                launch["recovery_status"] = "starting"
                self.store.save()
                try:
                    launch.update(await asyncio.to_thread(start_worker, folder, thread_id, socket_path, executable, log_path))
                except BaseException:
                    launch["recovery_status"] = "unconfirmed"
                    self.store.save()
                    raise
                launch["recovery_status"] = "running"
                launch["recovered_at"] = time.time()
            self.connected[name] = client.ws
            self.sleeve_status[name] = {"status": resumed["thread"]["status"]["type"], "checked_at": time.time()}
            self.sleeve_process_status[name] = {"status": "running", "thread_id": thread_id, "checked_at": time.time()}
            self.store.event(name, ("Reconnected to" if alive else "Restored CLI for") + " saved Codex thread " + thread_id)
            await self.catch_up(name, client)
            return {"thread_id": thread_id, "socket_path": socket_path, "already_running": alive}

    def identity(self, name):
        return self.store.identity(name)

    async def verify_telegram(self, name, settings):
        if settings is None:
            return None
        if not isinstance(settings, dict) or set(settings) != {"token"} or not isinstance(settings["token"], str):
            raise ValueError("Telegram settings must contain a bot token")
        token = settings["token"].strip()
        if not TOKEN_PATTERN.fullmatch(token):
            raise ValueError("Invalid Telegram bot token")
        candidate = TelegramIdentity(token, "", 0)
        me = await self.telegram(AgentIdentity(name, candidate), "getMe")
        if any(other != name and record.get("telegram", {}).get("bot_id") == me["id"]
               for other, record in self.store.data["identities"].items() if record.get("telegram")):
            raise ValueError("This Telegram bot is already registered")
        candidate.bot_id, candidate.username = me["id"], me["username"]
        return candidate

    async def create_identity(self, name, settings=None, *, allow_external_media=True, check_authorization=None):
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            raise ValueError("Use an identity name of 1–80 characters")
        name = name.strip()
        if type(allow_external_media) is not bool:
            raise ValueError("allow_external_media must be a boolean")
        async with self.configuration_lock:
            if check_authorization:
                check_authorization()
            if name in self.store.data["identities"]:
                raise ValueError("Identity name already exists")
            telegram = await self.verify_telegram(name, settings)
            if check_authorization:
                check_authorization()
            identity = AgentIdentity(name, telegram, allow_external_media=allow_external_media)
            self.store.add_identity(identity)
            self.store.event(name, "Agent identity created")
            if telegram:
                self.start_poller(name)
            return identity

    async def configure_communication(self, name, settings, *, allow_external_media=..., check_authorization=None):
        if allow_external_media is not ... and type(allow_external_media) is not bool:
            raise ValueError("allow_external_media must be a boolean")
        async with self.configuration_lock, self.locks.setdefault(name, asyncio.Lock()):
            if check_authorization:
                check_authorization()
            identity = self.identity(name)
            old = identity.telegram
            telegram = old if settings is ... else await self.verify_telegram(name, settings)
            if check_authorization:
                check_authorization()
            changed_bot = (old.bot_id if old else None) != (telegram.bot_id if telegram else None)
            if old and telegram and not changed_bot:
                telegram.offset, telegram.chats = old.offset, old.chats
            poller = self.pollers.pop(name, None)
            if poller:
                poller.cancel()
                try:
                    await asyncio.gather(poller, return_exceptions=True)
                    if check_authorization:
                        check_authorization()
                except BaseException:
                    if old:
                        self.start_poller(name)
                    raise
            self.store.data["identities"][name]["telegram"] = dataclasses.asdict(telegram) if telegram else None
            if allow_external_media is not ...:
                self.store.data["identities"][name]["allow_external_media"] = allow_external_media
            if changed_bot:
                # A new bot must never deliver the previous bot's queued private messages.
                self.store.data["pending_users"].pop(name, None)
                self.store.data["turn_recipients"].pop(name, None)
                self.store.data["outbox"] = [entry for entry in self.store.data["outbox"] if entry["identity"] != name]
                for job in self.store.data["inbox"]:
                    if job["identity"] == name and job["status"] == "received":
                        job["status"] = "done"
                self.clear_approvals(name)
            self.store.event(name, "Communication settings updated")
            if telegram:
                self.start_poller(name)

    def clear_approvals(self, name):
        for key, approval in list(self.approvals.items()):
            if approval["name"] == name:
                self.approvals.pop(key)

    async def client(self, socket_path):
        socket_path = str(Path(socket_path).expanduser().absolute())
        client = self.clients.setdefault(socket_path, CodexClient(socket_path, self.on_codex))
        await client.connect()
        return client

    async def start(self, app):
        await self.telegram.start()
        for job in self.store.data["inbox"]:
            if job["status"] == "submitting":
                job["status"] = "uncertain"
                message = job.get("message") or (job.get("callback") or {}).get("message", {})
                self.enqueue(job["identity"], f"The bridge restarted while submitting an instruction. Check {'/list or the local dashboard' if self.is_admin(job['identity']) else 'the local dashboard'} before resending; it was not replayed.",
                             chat_ids=[job.get("chat_id", message.get("chat", {}).get("id"))])
        self.store.save()
        self.tasks = [asyncio.create_task(self.work()), asyncio.create_task(self.deliver()),
                      asyncio.create_task(self.work(admin=True)), asyncio.create_task(self.deliver(admin=True)),
                      asyncio.create_task(self.maintain_connections())]
        for identity in self.store.identities().values():
            if identity.telegram:
                self.start_poller(identity.name)

    async def stop(self, app):
        for task in self.tasks + list(self.pollers.values()):
            task.cancel()
        await asyncio.gather(*self.tasks, *self.pollers.values(), return_exceptions=True)
        for client in self.clients.values():
            await client.close()
        await self.telegram.close()

    def start_poller(self, name):
        if name in self.pollers and not self.pollers[name].done():
            return
        self.pollers[name] = asyncio.create_task(self.poll(name))

    def authorized(self, identity, user, chat):
        if (not identity.telegram or type(user.get("id")) is not int or user["id"] <= 0
                or chat.get("type") != "private" or chat.get("id") != user["id"]):
            return False
        external = self.store.match_external_identity(user)
        return external is not None and external.communication_policy.allows(identity.id)

    def resolve_sender(self, user):
        external = self.store.match_external_identity(user)
        if external and external.user_id is None:
            external = dataclasses.replace(external, user_id=str(user["id"]))
            self.store.save_external_identity(external)
        return external

    def remember_user(self, name, user, chat):
        self.store.data["identities"][name]["telegram"]["chats"][str(user["id"])] = chat["id"]

    def create_external_identity(self, body):
        external = ExternalIdentity.from_dict(body)
        self.store.save_external_identity(external)
        self.recheck_access()
        self.store.event("External identities", "External identity registered")
        return self.store.external_identity(external.id)

    def update_external_policy(self, identity_id, body):
        existing = self.store.external_identity(identity_id)
        if existing is None:
            raise ValueError("Unknown external identity")
        if not isinstance(body, dict) or set(body) != {"communication_policy"}:
            raise ValueError("Only communication_policy may be updated")
        external = dataclasses.replace(existing, communication_policy=CommunicationPolicy.from_dict(body["communication_policy"]))
        self.store.save_external_identity(external)
        self.recheck_access()
        self.store.event("External identities", "Communication policy updated")
        return self.store.external_identity(identity_id)

    def recipient_chat_ids(self, name):
        identity = self.identity(name)
        if not identity.telegram:
            return []
        return [chat_id for user_id, chat_id in identity.telegram.chats.items()
                if self.authorized(identity, {"id": int(user_id)}, {"id": chat_id, "type": "private"})]

    def turn_chat_ids(self, name, turn_id):
        allowed = self.recipient_chat_ids(name)
        recipients = self.store.data["turn_recipients"].get(name, {}).get(turn_id)
        if recipients is None:
            # Local-originated turns retain mirroring for a single connected user.
            return allowed if len(allowed) == 1 else []
        return [chat_id for chat_id in recipients if chat_id in allowed]

    def add_turn_recipient(self, name, turn_id, chat_id):
        turns = self.store.data["turn_recipients"].setdefault(name, {})
        recipients = turns.setdefault(turn_id, [])
        if chat_id not in recipients:
            recipients.append(chat_id)
        self.store.data["turn_recipients"][name] = dict(list(turns.items())[-1000:])
        self.store.save()

    def recheck_access(self):
        self.admin.menu.recheck_access()
        for name, identity in self.store.identities().items():
            if not identity.telegram:
                continue
            for user_id, pending in list(self.store.data["pending_users"].get(name, {}).items()):
                if time.time() - pending["seen_at"] > 1800:
                    continue
                user = {"id": pending["user_id"], "username": pending.get("username")}
                if self.resolve_sender(user):
                    self.remember_user(name, user, {"id": pending["chat_id"]})
                    self.store.data["pending_users"][name].pop(user_id)
        allowed = {name: self.recipient_chat_ids(name) for name in self.store.identities()}
        self.store.data["outbox"] = [entry for entry in self.store.data["outbox"]
                                    if entry.get("chat_id") in allowed[entry["identity"]]]
        for job in self.store.data["inbox"]:
            if job["status"] == "received":
                message = job.get("message") or (job.get("callback") or {}).get("message", {})
                user = (job.get("callback") or message).get("from", {})
                if not self.authorized(self.identity(job["identity"]), user, message.get("chat", {})):
                    job["status"] = "done"

    def unregistered_senders(self):
        senders = {}
        for name, users in self.store.data["pending_users"].items():
            for pending in users.values():
                if time.time() - pending["seen_at"] > 1800 or self.store.match_external_identity(
                        {"id": pending["user_id"], "username": pending.get("username")}):
                    continue
                user_id = str(pending["user_id"])
                sender = senders.setdefault(user_id, {"provider": "telegram", "user_id": user_id,
                    "username": pending.get("username"), "first_name": pending.get("first_name"), "agents": []})
                sender["agents"].append(name)
        return list(senders.values())

    def enqueue(self, name, text, chat_ids=None, media=(), **extra):
        allowed = self.recipient_chat_ids(name)
        for chat_id in allowed if chat_ids is None else chat_ids:
            if chat_id in allowed:
                for chunk in text_chunks(text):
                    self.store.data["outbox"].append({"identity": name, "chat_id": chat_id, "text": chunk, **extra})
                for attachment in media:
                    self.store.data["outbox"].append({"identity": name, "chat_id": chat_id, "media": attachment})
        self.store.save()

    async def poll(self, name):
        try:
            identity = self.identity(name)
            webhook = await self.telegram(identity, "getWebhookInfo")
            if webhook.get("url"):
                raise ValueError("Bot already has a webhook; remove it deliberately before using long polling")
            while True:
                try:
                    identity = self.identity(name)
                    updates = await self.telegram(identity, "getUpdates", offset=identity.telegram.offset,
                                                  timeout=25, allowed_updates=["message", "callback_query"])
                    for update in updates:
                        self.ingest(name, update)
                except (ConnectionError, RuntimeError, asyncio.TimeoutError) as error:
                    self.store.event(name, safe_error(error))
                    await asyncio.sleep(5)
        except (ValueError, OSError, RuntimeError, ConnectionError) as error:
            self.store.event(name, "Polling stopped: " + safe_error(error))

    def ingest(self, name, update):
        identity = self.identity(name)
        if not identity.telegram or update["update_id"] < identity.telegram.offset:
            return
        callback = update.get("callback_query")
        message = update.get("message") or (callback or {}).get("message", {})
        user = (callback or message).get("from", {})
        chat = message.get("chat", {})
        if (chat.get("type") == "private" and type(user.get("id")) is int
                and user["id"] > 0 and chat.get("id") == user["id"]):
            external = self.resolve_sender(user)
            if external:
                self.remember_user(name, user, chat)
            if self.authorized(identity, user, chat):
                self.store.data["pending_users"].get(name, {}).pop(str(user["id"]), None)
                job = {"id": f"{identity.telegram.bot_id}:{update['update_id']}",
                    "identity": name, "status": "received", "message": message if not callback else None,
                    "chat_id": chat["id"], "callback": callback}
                if self.is_admin(name):
                    self.admin.menu.capture_input(identity, job)
                self.store.data["inbox"].append(job)
            elif external is None:
                pending = self.store.data["pending_users"].setdefault(name, {})
                if len(pending) < 100 or str(user["id"]) in pending:
                    pending[str(user["id"])] = {"user_id": user["id"], "chat_id": chat["id"],
                        "username": user.get("username"), "first_name": user.get("first_name"), "seen_at": time.time()}
        self.store.data["identities"][name]["telegram"]["offset"] = update["update_id"] + 1
        self.store.save()

    def is_admin(self, name):
        return self.store.data["sleeves"].get(name, {}).get("kind") == "admin_app"

    async def work(self, admin=False):
        while True:
            job = next((job for job in self.store.data["inbox"] if job["status"] == "received" and self.is_admin(job["identity"]) == admin), None)
            if job is None:
                await asyncio.sleep(0.15)
                continue
            name = job["identity"]
            try:
                async with self.locks.setdefault(name, asyncio.Lock()):
                    if job["status"] != "received":
                        continue
                    job["status"] = "submitting"
                    self.store.save()
                    await self.handle(job)
                    job["status"] = "done"
                    self.store.event(name, "Telegram instruction handled")
            except Exception as error:
                job["status"] = "uncertain"
                self.store.event(name, "Instruction needs review: " + safe_error(error))
                self.enqueue(name, "Instruction could not be confirmed: " + safe_error(error)
                             + f"\nCheck {'/list' if self.is_admin(name) else 'the local dashboard'} before resending. It will not be replayed automatically.",
                             chat_ids=[job.get("chat_id")])
            # Keep every unfinished job; retain only the latest 100 finished receipts.
            finished = [x for x in self.store.data["inbox"] if x["status"] == "done"][-100:]
            self.store.data["inbox"] = [x for x in self.store.data["inbox"] if x["status"] != "done"] + finished
            self.store.save()

    async def handle(self, job):
        name = job["identity"]
        identity = self.identity(name)
        callback = job.get("callback")
        message = job.get("message") or (callback or {}).get("message", {})
        user = (callback or message).get("from", {})
        if not self.authorized(identity, user, message.get("chat", {})):
            return
        def reply(text):
            self.enqueue(name, text, chat_ids=[message["chat"]["id"]])
        if callback:
            if self.is_admin(name) and callback.get("data", "").startswith("admin-"):
                await self.admin.handle_callback(identity, callback)
            else:
                await self.handle_callback(identity, callback)
            return
        if self.is_admin(name):
            if any(key in message for key in ("voice", "photo", "video", "video_note", "animation", "document")):
                reply("Warning: AdminAppSleeve accepts typed commands only; send /help")
                return
            await self.admin.handle(identity, job)
            return
        if job.get("cancel_requested"):
            return
        text = message.get("text", "")
        voice = message.get("voice")
        try:
            media = incoming_media(message)
        except MediaError as error:
            reply("Warning: " + str(error) + ". This instruction was not submitted.")
            return
        if not text.strip() and not voice and not media:
            reply("Send text, a photo, a video, an image/video file, or a Telegram voice note.")
            return
        binding = self.store.data["sleeves"].get(name)
        if not binding:
            reply(f"Warning: {name} has no sleeve. Assign one in the local dashboard before sending more work.")
            return
        thread_id = binding["thread_id"]
        try:
            client = await self.ensure_sleeve(name)
            thread = (await client.request("thread/read", {"threadId": thread_id}))["thread"]
            turns = (await client.request("thread/turns/list", {"threadId": thread_id, "limit": 1,
                                                              "itemsView": "notLoaded"}))["data"]
        except (ConnectionError, OSError, RuntimeError, ValueError, asyncio.TimeoutError) as error:
            self.mark_unavailable(name, error)
            reply(f"Warning: {name} has no usable root-agent sleeve. Restore it or assign a root agent in the local dashboard. This instruction was not submitted.")
            return
        self.sleeve_status[name] = {"status": thread["status"]["type"], "checked_at": time.time()}
        if job.get("cancel_requested") or not self.authorized(self.identity(name), user, message.get("chat", {})):
            return
        if voice:
            try:
                address = {"socket_path": binding["socket_path"], "thread_id": binding["thread_id"]}
                if job.get("voice_path"):
                    if job.get("voice_sleeve") != address:
                        raise VoiceError("This recording belongs to the previous sleeve; resend it to use the new sleeve")
                else:
                    audio = await self.telegram.download_voice(identity, voice)
                    if job.get("cancel_requested") or not self.authorized(self.identity(name), user, message.get("chat", {})):
                        return
                    mime = voice.get("mime_type")
                    job["voice_path"] = save_voice(binding.get("cwd"), job["id"], audio, mime if isinstance(mime, str) else None)
                    job["voice_sleeve"] = address
                    self.store.save()
                text = voice_instruction(job["voice_path"])
            except (VoiceError, TelegramAPIError, ConnectionError, OSError, asyncio.TimeoutError) as error:
                # Only explicitly safe errors should be shown to the sender.
                detail = str(error) if isinstance(error, VoiceError) else "The voice recording could not be downloaded or saved; check workspace write access or send text"
                reply("Warning: " + detail + ". This instruction was not submitted.")
                return
        inputs = [{"type": "text", "text": text}] if text else []
        if media:
            try:
                address = {"socket_path": binding["socket_path"], "thread_id": binding["thread_id"]}
                if job.get("media_path"):
                    if job.get("media_sleeve") != address:
                        raise MediaError("This attachment belongs to the previous sleeve; resend it to use the new sleeve")
                else:
                    file, kind = media
                    data = await self.telegram.download_file(identity, file)
                    if job.get("cancel_requested") or not self.authorized(self.identity(name), user, message.get("chat", {})):
                        return
                    job["media_path"] = save_incoming_media(binding.get("cwd"), job["id"], data, file, kind)
                    job["media_sleeve"] = address
                    self.store.save()
                caption = message.get("caption", "")
                if caption:
                    inputs.append({"type": "text", "text": caption})
                inputs.extend(media_input(job["media_path"]))
            except (MediaError, TelegramAPIError, ConnectionError, OSError, asyncio.TimeoutError) as error:
                detail = str(error) if isinstance(error, MediaError) else "The attachment could not be downloaded or saved; check workspace write access or send text"
                reply("Warning: " + detail + ". This instruction was not submitted.")
                return
        active = next((turn for turn in turns if turn["status"] == "inProgress"), None)
        params = {"threadId": thread_id, "input": inputs}
        if active:
            self.add_turn_recipient(name, active["id"], message["chat"]["id"])
            params["expectedTurnId"] = active["id"]
            result = await client.request("turn/steer", params)
            job["turn_id"] = result["turnId"]
        else:
            params["clientUserMessageId"] = str(uuid.uuid5(uuid.NAMESPACE_URL, "telegram:" + job["id"]))
            result = await client.request("turn/start", params)
            job["turn_id"] = result["turn"]["id"]
        if job.get("cancel_requested"):
            await client.request("turn/interrupt", {"threadId": thread_id, "turnId": job["turn_id"]})
        self.add_turn_recipient(name, job["turn_id"], message["chat"]["id"])

    def mark_unavailable(self, name, error):
        if self.sleeve_status.get(name, {}).get("status") != "unavailable":
            self.store.event(name, "Sleeve unavailable: " + safe_error(error))
        self.sleeve_status[name] = {"status": "unavailable", "detail": safe_error(error), "checked_at": time.time()}
        self.connected.pop(name, None)

    async def ensure_sleeve(self, name):
        binding = self.store.data["sleeves"][name]
        if binding.get("kind") == "admin_app":
            raise ValueError("AdminAppSleeve does not use Codex")
        client = await self.client(binding["socket_path"])
        if self.connected.get(name) is not client.ws:
            thread = (await client.request("thread/read", {"threadId": binding["thread_id"]}))["thread"]
            if not is_root_thread(thread):
                raise ValueError("Only root agents can be sleeves; subagents and unknown origins are not supported")
            await self.resume_sleeve(client, binding["thread_id"])
            self.connected[name] = client.ws
            self.store.event(name, "Connected to exact Codex thread " + binding["thread_id"])
            await self.catch_up(name, client)
        return client

    async def resume_sleeve(self, client, thread_id):
        address = client.socket_path + ":" + thread_id
        receipts = self.store.data.setdefault("sleeve_initializations", {})
        receipt = receipts.get(address)
        if not receipt or receipt.get("status") == "ready":
            try:
                return await client.request("thread/resume", {"threadId": thread_id, "excludeTurns": True})
            except RuntimeError as error:
                if str(error) not in {f"no rollout found for thread id {thread_id}", "list_turns is not supported yet"}:
                    raise
            history = await client.request("thread/turns/list", {"threadId": thread_id, "limit": 1, "itemsView": "notLoaded"})
            if history["data"]:
                raise RuntimeError("The sleeve could not resume despite having history; no initialization was sent")
            if receipt:
                raise RuntimeError("The sleeve lost its initialized history; initialization will not be replayed")
            receipt = receipts[address] = {"socket_path": client.socket_path, "thread_id": thread_id, "status": "submitting"}
            self.store.save()
            try:
                result = await client.request("turn/start", {"threadId": thread_id,
                    "clientUserMessageId": str(uuid.uuid5(uuid.NAMESPACE_URL, "sleeve-initialization:" + address)),
                    "input": [{"type": "text", "text": "Initialize this empty Agent Home sleeve. Reply only: Ready. Do not inspect files or run tools; wait for the user's next message."}]})
                receipt["turn_id"] = result["turn"]["id"]
                self.store.save()
            except BaseException:
                receipt["status"] = "uncertain"
                self.store.save()
                raise
        if not receipt.get("turn_id"):
            raise RuntimeError("Sleeve initialization was not confirmed; inspect the root session before resending. It will not be replayed")
        async with asyncio.timeout(60):
            while True:
                try:
                    turns = (await client.request("thread/turns/list", {"threadId": thread_id, "limit": 10, "itemsView": "notLoaded"}))["data"]
                    initial = next((turn for turn in turns if turn["id"] == receipt["turn_id"]), None)
                    if initial and initial["status"] != "inProgress":
                        if initial["status"] != "completed":
                            raise RuntimeError("Sleeve initialization did not complete; inspect the root session")
                        resumed = await client.request("thread/resume", {"threadId": thread_id, "excludeTurns": True})
                        receipt["status"] = "ready"
                        self.store.save()
                        return resumed
                except RuntimeError as error:
                    # The first turn can briefly expose its empty rollout before the writer flushes it.
                    if not ("rollout at " in str(error) and str(error).endswith(f"-{thread_id}.jsonl is empty")):
                        raise
                await asyncio.sleep(0.2)

    async def catch_up(self, name, client):
        binding = self.store.data["sleeves"][name]
        watermark = binding["last_turn_id"]
        cursor, turns = None, []
        while True:
            page = await client.request("thread/turns/list", {"threadId": binding["thread_id"],
                "limit": 5, "itemsView": "full", "cursor": cursor})
            found = False
            for turn in page["data"]:
                turns.append(turn)
                if turn["id"] == watermark:
                    found = True
                    break
            if found or not page.get("nextCursor"):
                break
            cursor = page["nextCursor"]
        for turn in reversed(turns):
            if turn["id"] == watermark or turn["status"] == "inProgress":
                continue
            for item in turn.get("items", []):
                if item.get("type") == "agentMessage":
                    self.relay_item(name, turn["id"], item)
            binding["last_turn_id"] = turn["id"]
        self.store.save()

    async def maintain_connections(self):
        while True:
            for name in list(self.store.data["sleeves"]):
                if self.is_admin(name):
                    self.sleeve_status[name] = {"status": "ready", "checked_at": time.time()}
                    continue
                try:
                    async with self.locks.setdefault(name, asyncio.Lock()):
                        if name not in self.store.data["sleeves"] or self.is_admin(name):
                            continue
                        launch = self.sleeve_launch(name)
                        if launch:
                            alive = await asyncio.to_thread(worker_running, launch.copy())
                            self.sleeve_process_status[name] = {"status": "running" if alive else "stopped",
                                "thread_id": launch["thread_id"], "checked_at": time.time()}
                        client = await self.ensure_sleeve(name)
                        thread = (await client.request("thread/read", {
                            "threadId": self.store.data["sleeves"][name]["thread_id"]}))["thread"]
                        self.sleeve_status[name] = {"status": thread["status"]["type"], "checked_at": time.time()}
                        if self.recipient_chat_ids(name):
                            await self.catch_up(name, client)
                except Exception as error:
                    self.mark_unavailable(name, error)
            await asyncio.sleep(5)

    def relay_item(self, name, turn_id, item):
        binding = self.store.data["sleeves"][name]
        if any(receipt.get("turn_id") == turn_id and receipt["thread_id"] == binding.get("thread_id")
               and receipt["socket_path"] == binding.get("socket_path")
               for receipt in self.store.data.get("sleeve_initializations", {}).values()):
            return
        key = f"{name}:{binding['thread_id']}:{turn_id}:{item['id']}"
        if key in self.store.data["seen_items"]:
            return
        text = item.get("text", "")
        if text:
            prefix = "Working: " if item.get("phase") == "commentary" else ""
            chat_ids = self.turn_chat_ids(name, turn_id)
            if not chat_ids:
                return
            self.enqueue(name, prefix + text, chat_ids=chat_ids, media=media_references(text, binding.get("cwd")))
            self.store.data["seen_items"].append(key)
            self.store.data["seen_items"] = self.store.data["seen_items"][-5000:]
            self.store.event(name, "Codex reply queued for Telegram")

    async def on_codex(self, client, message):
        method, params = message["method"], message.get("params", {})
        names = [name for name, binding in self.store.data["sleeves"].items()
                 if binding.get("socket_path") == client.socket_path and binding.get("thread_id") == params.get("threadId")
                 and self.connected.get(name) is client.ws
                 and self.recipient_chat_ids(name)]
        if method == "serverRequest/resolved":
            for key, approval in list(self.approvals.items()):
                if approval["client"] is client and approval["request_id"] == params.get("requestId"):
                    self.approvals.pop(key, None)
            return
        for name in names:
            if method == "turn/started":
                for job in self.store.data["inbox"]:
                    if (job["identity"] == name and job["status"] == "submitting" and job.get("message")
                            and (job.get("voice_path") or job.get("media_path") or job["message"].get("text", "").strip())):
                        self.add_turn_recipient(name, params["turn"]["id"], job["message"]["chat"]["id"])
            elif method == "item/completed" and params["item"]["type"] == "agentMessage":
                self.relay_item(name, params["turnId"], params["item"])
            elif method == "turn/completed":
                turn = params["turn"]
                if turn["status"] != "completed":
                    error = turn.get("error") or {}
                    self.enqueue(name, f"Turn {turn['status']}. " + safe_error(error.get("message", "")),
                                 chat_ids=self.turn_chat_ids(name, turn["id"]))
                for key, approval in list(self.approvals.items()):
                    if approval["name"] == name and approval["turn_id"] == turn["id"]:
                        self.approvals.pop(key, None)
                self.store.save()
            elif "id" in message:
                if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
                    key = secrets.token_hex(10)
                    self.approvals[key] = {"name": name, "client": client, "request_id": message["id"],
                        "thread_id": params["threadId"], "turn_id": params["turnId"], "ws": client.ws}
                    detail = params.get("command") or params.get("reason") or "File changes requested"
                    self.enqueue(name, "Approval requested for this conversation:\n" + str(detail)[:2500],
                        chat_ids=self.turn_chat_ids(name, params["turnId"]),
                        reply_markup={"inline_keyboard": [[{"text": "Allow once", "callback_data": "allow:" + key},
                                                           {"text": "Deny", "callback_data": "deny:" + key}]]})
                else:
                    self.enqueue(name, f"Codex needs input ({method}). Answer in the existing local Codex interface.",
                                 chat_ids=self.turn_chat_ids(name, params.get("turnId")))

    async def handle_callback(self, identity, callback):
        action, _, key = callback.get("data", "").partition(":")
        approval = self.approvals.get(key)
        valid = (approval and approval["name"] == identity.name and action in {"allow", "deny"}
                 and self.authorized(identity, callback.get("from", {}), callback.get("message", {}).get("chat", {}))
                 and self.store.data["sleeves"].get(identity.name, {}).get("thread_id") == approval["thread_id"]
                 and self.store.data["sleeves"].get(identity.name, {}).get("socket_path") == approval["client"].socket_path
                 and approval["client"].ws is approval["ws"] and not approval["ws"].closed)
        if valid:
            turns = (await approval["client"].request("thread/turns/list", {
                "threadId": approval["thread_id"], "limit": 1, "itemsView": "notLoaded"}))["data"]
            valid = (any(turn["id"] == approval["turn_id"] and turn["status"] == "inProgress" for turn in turns)
                     and self.authorized(self.identity(identity.name), callback.get("from", {}), callback.get("message", {}).get("chat", {})))
        if valid and self.approvals.pop(key, None):
            await approval["client"].ws.send_json({"id": approval["request_id"], "result": {
                "decision": "accept" if action == "allow" else "decline"}})
        await self.telegram(identity, "answerCallbackQuery", callback_query_id=callback["id"],
                            text="Decision sent" if valid else "This approval has expired")

    async def deliver(self, admin=False):
        while True:
            for entry in list(self.store.data["outbox"]):
                if self.is_admin(entry["identity"]) != admin:
                    continue
                try:
                    async with self.locks.setdefault(entry["identity"], asyncio.Lock()):
                        if entry not in self.store.data["outbox"]:
                            continue
                        identity = self.identity(entry["identity"])
                        if entry.get("chat_id") not in self.recipient_chat_ids(identity.name):
                            self.store.data["outbox"].remove(entry)
                            self.store.save()
                            continue
                        await self.deliver_entry(identity, entry)
                        if entry in self.store.data["outbox"]:
                            self.store.data["outbox"].remove(entry)
                        self.store.event(identity.name, "Telegram reply delivered")
                    await asyncio.sleep(1.1)
                except Exception as error:
                    self.store.event(entry["identity"], "Delivery pending: " + safe_error(error))
                    await asyncio.sleep(5)
            await asyncio.sleep(0.2)

    async def deliver_entry(self, identity, entry):
        identity = self.identity(identity.name)
        if entry not in self.store.data["outbox"] or entry["chat_id"] not in self.recipient_chat_ids(identity.name):
            return
        if not self.admin.menu.deliverable(identity, entry):
            self.store.data["outbox"].remove(entry)
            self.store.save()
            return
        if "media" in entry:
            try:
                await self.telegram.send_media(identity, entry["media"], chat_id=entry["chat_id"])
                return
            except (MediaError, TelegramAPIError) as error:
                if isinstance(error, TelegramAPIError) and error.code not in {400, 413}:
                    raise
                media = entry.pop("media")
                entry["text"] = f"Warning: media could not be attached: {safe_error(error)}\nFile: {media['path']}"
                self.store.save()
        if entry in self.store.data["outbox"] and entry["chat_id"] in self.recipient_chat_ids(identity.name):
            result = await self.telegram(identity, "sendMessage", chat_id=entry["chat_id"], text=entry["text"],
                                **({"reply_markup": entry["reply_markup"]} if "reply_markup" in entry else {}))
            if "admin_prompt" in entry:
                self.admin.menu.prompt_delivered(identity, entry, result)

    async def loaded_root_threads(self, socket_path):
        client = await self.client(str(Path(socket_path).expanduser().absolute()))
        result, cursor = [], None
        while True:
            page = await client.request("thread/loaded/list", {"limit": 100, "cursor": cursor})
            for thread_id in page["data"]:
                thread = (await client.request("thread/read", {"threadId": thread_id}))["thread"]
                if is_root_thread(thread):
                    result.append({key: thread.get(key) for key in ["id", "name", "cwd", "source", "agentNickname", "status"]})
            cursor = page.get("nextCursor")
            if not cursor:
                return {"socket_path": client.socket_path, "threads": result}

    async def assign_sleeve(self, name, thread_id=None, socket_path=None, kind="codex", *, check_authorization=None):
        if kind not in {"codex", "admin_app"}:
            raise ValueError("Unknown sleeve kind")
        if kind == "admin_app" and thread_id is not None:
            raise ValueError("AdminAppSleeve has no conversation ID")
        self.identity(name)
        thread_id = str(uuid.UUID(thread_id)) if thread_id is not None else None
        socket_path = str(Path(socket_path).expanduser().absolute()) if socket_path is not None else None
        if kind == "codex" and thread_id is not None and socket_path is None:
            raise ValueError("A Codex sleeve requires a socket path")
        async with self.configuration_lock, self.locks.setdefault(name, asyncio.Lock()):
            if check_authorization:
                check_authorization()
            old = self.store.data["sleeves"].get(name)
            if old and old.get("kind", "codex") == "codex" and (old["thread_id"] != thread_id or old["socket_path"] != socket_path or kind != "codex"):
                try:
                    client = await self.client(old["socket_path"])
                    previous = (await client.request("thread/read", {"threadId": old["thread_id"]}))["thread"]
                except (ConnectionError, OSError, RuntimeError, asyncio.TimeoutError):
                    previous = None  # A lost sleeve must not prevent explicit replacement.
                if previous and previous["status"]["type"] == "active":
                    raise ValueError("Stop or finish the existing turn before rebinding")
            if check_authorization:
                check_authorization()
            if kind == "admin_app":
                if any(other != name and binding.get("kind") == "admin_app"
                       for other, binding in self.store.data["sleeves"].items()):
                    raise ValueError("AdminAppSleeve already belongs to another agent identity")
                self.store.data["sleeves"][name] = dataclasses.asdict(AdminAppSleeve())
                self.connected.pop(name, None)
                self.clear_approvals(name)
                self.sleeve_status[name] = {"status": "ready", "checked_at": time.time()}
                self.store.event(name, "AdminAppSleeve assigned")
                return {"kind": "admin_app"}
            if thread_id is None:
                self.store.data["sleeves"].pop(name, None)
                self.connected.pop(name, None)
                self.sleeve_status.pop(name, None)
                self.clear_approvals(name)
                self.store.event(name, "Sleeve detached")
                return None
            for other, binding in self.store.data["sleeves"].items():
                if other != name and binding.get("thread_id") == thread_id and binding.get("socket_path") == socket_path:
                    raise ValueError("This sleeve already belongs to another agent identity")
            client = await self.client(socket_path)
            thread = (await client.request("thread/read", {"threadId": thread_id}))["thread"]
            if not is_root_thread(thread):
                raise ValueError("Only root agents can be sleeves; subagents and unknown origins are not supported")
            await self.resume_sleeve(client, thread_id)
            if check_authorization:
                check_authorization()
            if old and old.get("thread_id") == thread_id and old.get("socket_path") == socket_path:
                return thread
            turns = (await client.request("thread/turns/list", {"threadId": thread_id, "limit": 2,
                                                              "itemsView": "notLoaded"}))["data"]
            sleeve = AgentSleeve(socket_path=socket_path, thread_id=thread_id,
                thread_name=thread.get("name"), cwd=thread["cwd"],
                last_turn_id=next((turn["id"] for turn in turns if turn["status"] != "inProgress"), None))
            # Remote access can change while the Codex RPCs are in flight.
            if check_authorization:
                check_authorization()
            self.store.data["sleeves"][name] = dataclasses.asdict(sleeve)
            self.connected.pop(name, None)
            self.clear_approvals(name)
            self.sleeve_status[name] = {"status": thread["status"]["type"], "checked_at": time.time()}
            self.store.event(name, "Sleeve assigned: " + thread_id)
            return thread
