"""Fixed AdminAppSleeve commands. Never execute user text as code or shell commands."""

import asyncio
import secrets
import time

from .codex import DEFAULT_SOCKET, is_root_thread
from .admin_menu import AdminMenu
from .utils import safe_error


HELP = ("AdminAppSleeve commands:\n"
        "/menu, /start — choose an agent and an action\n"
        "/list — all accessible agents, current status, and assigned sleeve\n"
        "/create <agent name> — create an identity, then choose an existing sleeve\n"
        "/bind <agent name> — choose an existing Codex root sleeve\n"
        "/kill <agent name> — cancel queued instructions and interrupt active work\n"
        "/help — show commands\n"
        "/cancel — leave a menu input step\n"
        "Agent names must match exactly. /kill preserves the identity and sleeve.\n"
        "Managing identities, sleeves, and Telegram requires an Always allowed external identity.\n"
        "This sleeve runs inside Agent Home; it cannot respond when the server is down.")


def age(timestamp):
    return f"{max(0, int(time.time() - timestamp))}s ago" if timestamp else "not observed"


class AdminAppCommands:
    def __init__(self, bridge):
        self.bridge = bridge
        self.selections = {}
        self.menu = AdminMenu(self)

    def require_manager(self, identity, user, chat):
        current = self.bridge.identity(identity.name)
        external = self.bridge.store.match_external_identity(user)
        if (not self.bridge.is_admin(current.name) or not self.bridge.authorized(current, user, chat)
                or external is None or external.communication_policy.mode != "always_allowed"):
            raise ValueError("Management requires an Always allowed external identity with access to Admin")

    async def choose_sleeve(self, identity, user, chat, name):
        self.require_manager(identity, user, chat)
        target = self.target(user, name)
        if target.name == identity.name:
            raise ValueError("Change AdminAppSleeve from the desktop app, not its own chat")
        # shortcut: Telegram selects the default daemon; use the desktop app for custom sockets.
        result = await self.bridge.loaded_root_threads(str(DEFAULT_SOCKET))
        self.require_manager(identity, user, chat)
        target = self.target(user, name)
        options = [thread for thread in result["threads"] if not any(
            owner != name and binding.get("thread_id") == thread["id"]
            and binding.get("socket_path") == result["socket_path"]
            for owner, binding in self.bridge.store.data["sleeves"].items())]
        if not options:
            raise ValueError("No available loaded Codex root sleeves. Open a root session, then send /bind " + name)
        binding = self.bridge.store.data["sleeves"].get(name, {})
        selection = {"nonce": secrets.token_urlsafe(12), "expires": time.monotonic() + 300,
            "chat_id": chat["id"], "bot_id": identity.telegram.bot_id,
            "name": name, "target_id": target.id,
            "binding": {key: binding.get(key) for key in ("kind", "thread_id", "socket_path")},
            "socket_path": result["socket_path"], "options": options}
        self.selections[(identity.id, user["id"])] = selection
        return self.sleeve_page(selection, 0)

    def sleeve_page(self, selection, page):
        options = selection["options"]
        pages = (len(options) + 9) // 10
        if not 0 <= page < pages:
            raise ValueError("This sleeve page is unavailable; send /bind again")
        lines = [f"Choose an existing sleeve for {selection['name']} ({page + 1}/{pages}):"]
        buttons = []
        nonce = selection["nonce"]
        for index in range(page * 10, min((page + 1) * 10, len(options))):
            thread = options[index]
            title = " ".join((thread.get("name") or thread.get("agentNickname") or thread["id"][:8]).split())[:60]
            folder = " ".join((thread.get("cwd") or "Unknown working folder").split())[:140]
            lines.append(f"{index + 1}. {title}\n   {folder}")
            buttons.append([{"text": f"{index + 1}. {title}", "callback_data": f"admin-bind:{nonce}:{index}"}])
        navigation = []
        for label, destination in (("← Previous", page - 1), ("Next →", page + 1)):
            if 0 <= destination < pages:
                navigation.append({"text": label, "callback_data": f"admin-page:{nonce}:{destination}"})
        if navigation:
            buttons.append(navigation)
        lines.append("Choose a button within 5 minutes. Assigned sleeves and subagents are excluded.")
        return "\n".join(lines), {"reply_markup": {"inline_keyboard": buttons}}

    async def handle_callback(self, identity, callback):
        bridge = self.bridge
        user, chat = callback.get("from", {}), callback.get("message", {}).get("chat", {})
        try:
            async with asyncio.timeout(10):
                action, nonce, value = callback.get("data", "").split(":")
                if action == "admin-menu":
                    text, extra = await self.menu.action(identity, callback, nonce, value)
                    bridge.enqueue(identity.name, text, chat_ids=[chat["id"]], **extra)
                    return
                self.require_manager(identity, user, chat)
                selection = self.selections.get((identity.id, user["id"]))
                if (not selection or selection["nonce"] != nonce or selection["expires"] < time.monotonic()
                        or selection["chat_id"] != chat["id"] or selection["bot_id"] != identity.telegram.bot_id):
                    raise ValueError("This selection has expired. Send /bind <agent name> again")
                if action == "admin-page":
                    text, extra = self.sleeve_page(selection, int(value))
                    bridge.enqueue(identity.name, text, chat_ids=[chat["id"]], **extra)
                elif action == "admin-bind" and 0 <= int(value) < len(selection["options"]):
                    thread = selection["options"][int(value)]
                    def check_authorization():
                        self.require_manager(identity, user, chat)
                        target = self.target(user, selection["name"])
                        current = bridge.store.data["sleeves"].get(target.name, {})
                        binding = {key: current.get(key) for key in ("kind", "thread_id", "socket_path")}
                        if target.id != selection["target_id"] or binding != selection["binding"]:
                            raise ValueError("The agent's sleeve changed. Send /bind again to refresh")
                    self.selections.pop((identity.id, user["id"]))
                    await bridge.assign_sleeve(selection["name"], thread["id"], selection["socket_path"],
                                              check_authorization=check_authorization)
                    bridge.enqueue(identity.name, f"{selection['name']}: Codex sleeve assigned.\n"
                        f"{thread.get('name') or thread['id'][:8]}\n{thread.get('cwd') or ''}", chat_ids=[chat["id"]])
                else:
                    raise ValueError("Invalid sleeve choice; send /bind again")
        except (ValueError, RuntimeError, ConnectionError, OSError, asyncio.TimeoutError) as error:
            bridge.enqueue(identity.name, "Admin command failed: " + (safe_error(error) or
                "timed out; result not confirmed. Check /list before retrying"), chat_ids=[chat["id"]])
        finally:
            await self.answer_callback(identity, callback)

    async def answer_callback(self, identity, callback):
        try:
            async with asyncio.timeout(2):
                await self.bridge.telegram(identity, "answerCallbackQuery", callback_query_id=callback["id"])
        except (RuntimeError, OSError, asyncio.TimeoutError) as error:
            self.bridge.store.event(identity.name, "Admin callback notification failed: " + safe_error(error))

    def accessible(self, user, target):
        external = self.bridge.store.match_external_identity(user)
        return external is not None and external.communication_policy.allows(target.id)

    def targets(self, user):
        return [identity for identity in self.bridge.store.identities().values() if self.accessible(user, identity)]

    def target(self, user, name):
        target = next((identity for identity in self.targets(user) if identity.name == name), None)
        if target is None:
            raise ValueError("Agent not found or access denied. Use /list for accessible names.")
        return target

    def agent_line(self, target):
        labels = {"active": "Working", "idle": "Idle", "unavailable": "Unavailable",
                  "checking": "Checking", "systemError": "Runtime error", "notLoaded": "Not loaded"}
        runtime = self.bridge.sleeve_status.get(target.name, {})
        if target.sleeve is None:
            status, sleeve = "Needs sleeve", "None"
        elif target.sleeve.kind == "admin_app":
            status, sleeve = "Ready", "AdminAppSleeve"
        else:
            status = labels.get(runtime.get("status"), runtime.get("status", "Not checked"))
            sleeve = f"Codex (checked {age(runtime.get('checked_at'))})"
        return f"{target.name} — {status} | Sleeve: {sleeve}"

    def list_agents(self, user):
        return "\n".join(["Agents (cached status):"] + [self.agent_line(target) for target in self.targets(user)])

    async def handle(self, identity, job):
        message = job["message"]
        user, chat = message.get("from", {}), message.get("chat", {})
        if not self.bridge.authorized(identity, user, chat):
            return
        parts = message.get("text", "").strip().split(maxsplit=1)
        command = parts[0].split("@")[0].lower() if parts else ""
        argument = parts[1].strip() if len(parts) > 1 else ""
        extra, created = {}, ""
        try:
            if command in {"/menu", "/start", "/cancel"} and not argument:
                response, extra = self.menu.home(identity, user)
            elif job.get("admin_token") or self.menu.waiting(identity, user) and not command.startswith("/"):
                async with asyncio.timeout(10):
                    response, extra = await self.menu.input(identity, job)
            elif command == "/help" and not argument:
                response = HELP
            elif command == "/list" and not argument:
                response = self.list_agents(user)
            elif command == "/kill" and argument:
                async with asyncio.timeout(6):
                    response = await self.kill(identity.name, user, chat, argument)
            elif command in {"/create", "/bind"} and argument:
                async with asyncio.timeout(10):
                    self.require_manager(identity, user, chat)
                    if command == "/create":
                        target = await self.bridge.create_identity(argument, check_authorization=lambda:
                            self.require_manager(identity, user, chat))
                        created = f"Created {target.name}. Telegram is not connected yet; configure its bot from /menu.\n\n"
                    response, extra = await self.choose_sleeve(identity, user, chat, argument)
                    response = created + response
            else:
                response = "Fixed commands only.\n" + HELP
        except (ValueError, RuntimeError, ConnectionError, OSError, asyncio.TimeoutError) as error:
            if job.get("admin_token"):
                record = self.menu.sessions.get((identity.id, user["id"]))
                if record and record.get("token_job") == job["id"]:
                    self.menu.sessions.pop((identity.id, user["id"]))
            response = created + "Admin command failed: " + (safe_error(error) or "timed out; result not confirmed. Check /list before retrying")
        self.bridge.enqueue(identity.name, response, chat_ids=[chat["id"]], **extra)

    async def kill(self, admin_name, user, chat, name):
        bridge = self.bridge
        async with bridge.configuration_lock:
            if not bridge.authorized(bridge.identity(admin_name), user, chat):
                raise ValueError("Admin access revoked; no cancellation was requested")
            target = self.target(user, name)
            if not target.sleeve or target.sleeve.kind != "codex":
                raise ValueError("/kill requires an assigned Codex sleeve. AdminAppSleeve has no Codex process.")
            binding = target.sleeve
            cancelled, submitting = 0, 0
            for job in bridge.store.data["inbox"]:
                if job["identity"] == name and job["status"] in {"received", "submitting"}:
                    job["cancel_requested"] = True
                    if job["status"] == "received":
                        job["status"] = "done"
                        cancelled += 1
                    else:
                        submitting += 1
            bridge.clear_approvals(name)
            bridge.store.event(name, "Cancellation requested through AdminAppSleeve")
            try:
                async with asyncio.timeout(5):
                    client = await bridge.client(binding.socket_path)
                    thread = (await client.request("thread/read", {"threadId": binding.thread_id}))["thread"]
                    if not is_root_thread(thread):
                        raise ValueError("Only root-agent work may be interrupted")
                    turns = (await client.request("thread/turns/list", {
                        "threadId": binding.thread_id, "limit": 1, "itemsView": "notLoaded"}))["data"]
                    if (not self.accessible(user, bridge.identity(name))
                            or not bridge.authorized(bridge.identity(admin_name), user, chat)):
                        raise ValueError("Access revoked; no interrupt was sent")
                    active = next((turn for turn in turns if turn["status"] == "inProgress"), None)
                    if active:
                        await client.request("turn/interrupt", {"threadId": binding.thread_id, "turnId": active["id"]})
                    result = "Interrupt requested for the active turn." if active else "No active turn observed."
            except (RuntimeError, ConnectionError, OSError, asyncio.TimeoutError) as error:
                result = "Runtime stop not confirmed: " + (safe_error(error) or "Codex did not respond within 5 seconds")
            response = f"{name}: cancelled {cancelled} queued instruction(s). {result}"
            if submitting:
                response += f" {submitting} submission(s) were in flight; cancellation is requested. Check /list."
            return response
