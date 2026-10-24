"""Sender-scoped Admin menus and explicit replies for configuration input."""

import asyncio
import hashlib
import json
import secrets
import time
import uuid

from .utils import TOKEN_PATTERN


class AdminMenu:
    def __init__(self, commands):
        self.commands = commands
        self.bridge = commands.bridge
        self.sessions = {}

    def session(self, identity, user, kind, **fields):
        record = {"nonce": secrets.token_urlsafe(12), "expires": time.monotonic() + 300,
            "bot_id": identity.telegram.bot_id, "kind": kind, **fields}
        self.sessions[(identity.id, user["id"])] = record
        return record

    def current(self, identity, user):
        record = self.sessions.get((identity.id, user["id"]))
        if (not record or record["expires"] < time.monotonic()
                or record["bot_id"] != identity.telegram.bot_id):
            self.sessions.pop((identity.id, user["id"]), None)
            raise ValueError("This menu has expired. Send /menu again")
        return record

    def target(self, user, record):
        target = next((target for target in self.commands.targets(user) if target.id == record["target_id"]), None)
        if target is None:
            raise ValueError("Agent not found or access denied. Send /menu again")
        return target

    def button(self, record, label, action):
        return {"text": label, "callback_data": f"admin-menu:{record['nonce']}:{action}"}

    def home(self, identity, user, page=0):
        targets = self.commands.targets(user)
        pages = max(1, (len(targets) + 9) // 10)
        if not 0 <= page < pages:
            raise ValueError("Page unavailable. Send /menu again")
        record = self.session(identity, user, "home", agents=[target.id for target in targets])
        buttons = [[self.button(record, target.name[:60], f"open{index}")]
            for index, target in enumerate(targets) if page * 10 <= index < (page + 1) * 10]
        external = self.bridge.store.match_external_identity(user)
        if external and external.communication_policy.mode == "always_allowed":
            buttons.append([self.button(record, "+ New agent identity", "new")])
        navigation = [self.button(record, label, f"page{destination}")
            for label, destination in (("← Previous", page - 1), ("Next →", page + 1)) if 0 <= destination < pages]
        if navigation:
            buttons.append(navigation)
        return (f"Agent Home · Admin ({page + 1}/{pages})\nChoose an agent to view its status and actions.\n"
                "Use /menu to refresh, /list for all cached statuses, or /cancel to leave an input step.",
                {"admin_menu": record["nonce"], "reply_markup": {"inline_keyboard": buttons}})

    def agent(self, identity, user, target):
        record = self.session(identity, user, "agent", target_id=target.id)
        lines = [self.commands.agent_line(target), "Telegram: " + ("@" + target.telegram.username if target.telegram else "Not connected")]
        if target.sleeve and target.sleeve.kind == "codex":
            lines.extend([target.sleeve.thread_name or "Codex root session", target.sleeve.cwd or ""])
        buttons = []
        external = self.bridge.store.match_external_identity(user)
        if external and external.communication_policy.mode == "always_allowed" and target.id != identity.id:
            buttons.extend([[self.button(record, "Set / change Telegram token", "token")],
                [self.button(record, "Change sleeve" if target.sleeve else "Assign sleeve", "bind")],
                [self.button(record, "Create new sleeve", "create")]])
        if target.sleeve and target.sleeve.kind == "codex":
            buttons.append([self.button(record, "Stop work", "kill")])
        if target.id == identity.id:
            lines.append("Manage this Admin bot's own connection and sleeve in the desktop app.")
        buttons.append([self.button(record, "← All agents", "home")])
        return "\n".join(lines), {"admin_menu": record["nonce"], "reply_markup": {"inline_keyboard": buttons}}

    @staticmethod
    def communication_stamp(target):
        return (target.telegram.bot_id, hashlib.sha256(target.telegram.token.encode()).hexdigest()) if target.telegram else None

    @staticmethod
    def sleeve_stamp(target):
        sleeve = target.sleeve
        return (sleeve.kind, getattr(sleeve, "thread_id", None), getattr(sleeve, "socket_path", None)) if sleeve else None

    def folders(self, identity, user, target, page=0, record=None):
        if record is None:
            options = [str(folder) for folder in self.bridge.config.sleeve_allowed_folders]
            if not options:
                raise ValueError("No sleeve folders configured. Add folders in desktop Settings → Sleeve allowed folders")
            record = self.session(identity, user, "folders", target_id=target.id,
                binding=self.sleeve_stamp(target), folders=options)
        pages = (len(record["folders"]) + 9) // 10
        if not 0 <= page < pages:
            raise ValueError("Folder page unavailable. Send /menu again")
        lines = [f"Choose a folder for {target.name}'s new Codex sleeve ({page + 1}/{pages}):"]
        buttons = []
        for index in range(page * 10, min((page + 1) * 10, len(record["folders"]))):
            folder = record["folders"][index]
            lines.append(f"{index + 1}. {folder}")
            buttons.append([self.button(record, f"{index + 1}. {folder}"[:60], f"folder{index}")])
        navigation = [self.button(record, label, f"folders{destination}")
            for label, destination in (("← Previous", page - 1), ("Next →", page + 1)) if 0 <= destination < pages]
        if navigation:
            buttons.append(navigation)
        buttons.append([self.button(record, "← All agents", "home")])
        return "\n".join(lines), {"admin_menu": record["nonce"], "reply_markup": {"inline_keyboard": buttons}}

    def check_creation(self, identity, user, chat, record):
        self.commands.require_manager(identity, user, chat)
        current = self.bridge.identity(identity.name)
        if record["expires"] < time.monotonic() or record["bot_id"] != current.telegram.bot_id:
            raise ValueError("This menu has expired. Send /menu again")
        target = self.target(user, record)
        if target.id == current.id or self.sleeve_stamp(target) != record["binding"]:
            raise ValueError("The agent's sleeve changed. Send /menu again")
        if record["folders"] != [str(folder) for folder in self.bridge.config.sleeve_allowed_folders]:
            raise ValueError("Allowed folders changed. Send /menu again")
        return target

    def prompt(self, identity, user, kind, target=None):
        fields = {"target_id": target.id, "communication": self.communication_stamp(target)} if target else {}
        record = self.session(identity, user, kind, **fields)
        text = (f"Set Telegram for {target.name}.\nReply to this message with its BotFather token."
                if kind == "token" else "New agent identity.\nReply to this message with a unique name (1–80 characters).")
        return text + "\nThis prompt expires in 5 minutes. Send /cancel to return to the menu.", {
            "admin_prompt": record["nonce"], "reply_markup": {"force_reply": True,
                "input_field_placeholder": "BotFather token" if kind == "token" else "Agent name"}}

    def prompt_delivered(self, identity, entry, result):
        record = self.sessions.get((identity.id, entry["chat_id"]))
        if (record and record["nonce"] == entry.get("admin_prompt") and record["bot_id"] == identity.telegram.bot_id
                and isinstance(result, dict) and type(result.get("message_id")) is int):
            record["prompt_id"] = result["message_id"]

    def deliverable(self, identity, entry):
        nonce = entry.get("admin_prompt") or entry.get("admin_menu")
        if not nonce:
            return True
        user = {"id": entry["chat_id"]}
        try:
            record = self.current(identity, user)
            if record["nonce"] != nonce:
                return False
            if record["kind"] in {"token", "new", "folders", "launch"}:
                self.commands.require_manager(identity, user, {"id": user["id"], "type": "private"})
                if record["kind"] in {"folders", "launch"}:
                    self.check_creation(identity, user, {"id": user["id"], "type": "private"}, record)
            elif record["kind"] == "agent":
                self.target(user, record)
            elif record["kind"] == "home":
                allowed = {target.id for target in self.commands.targets(user)}
                return set(record["agents"]) <= allowed
            return True
        except ValueError:
            return False

    def recheck_access(self):
        identities = {identity.id: identity for identity in self.bridge.store.identities().values()}
        for (identity_id, user_id), record in list(self.sessions.items()):
            identity = identities.get(identity_id)
            user, chat = {"id": user_id}, {"id": user_id, "type": "private"}
            if not identity or not self.bridge.is_admin(identity.name) or not self.bridge.authorized(identity, user, chat):
                self.sessions.pop((identity_id, user_id), None)
            elif record["kind"] in {"token", "new", "folders", "launch"}:
                external = self.bridge.store.match_external_identity(user)
                if external.communication_policy.mode != "always_allowed":
                    self.sessions.pop((identity_id, user_id), None)

    def capture_input(self, identity, job):
        message = job["message"]
        if not message:
            job["callback"] = json.loads(TOKEN_PATTERN.sub("[redacted bot token]", json.dumps(job["callback"])))
            return
        text = message.get("text", "")
        user = message.get("from", {})
        record = self.sessions.get((identity.id, user.get("id")))
        entering = (record and record["kind"] == "token" and record.get("prompt_id") is not None
            and record["prompt_id"] == message.get("reply_to_message", {}).get("message_id")
            and not text.startswith("/"))
        if entering or TOKEN_PATTERN.search(text):
            job["admin_token"] = True
            if (entering and record["expires"] >= time.monotonic() and record["bot_id"] == identity.telegram.bot_id
                    and "token_job" not in record):
                record.update(token=text.strip(), token_job=job["id"])
            message = {**message, "text": "[Telegram token received]"}
        # Quoted messages can contain credentials too; durable receipts contain no bot tokens.
        job["message"] = json.loads(TOKEN_PATTERN.sub("[redacted bot token]", json.dumps(message)))

    def waiting(self, identity, user):
        record = self.sessions.get((identity.id, user.get("id")))
        return record is not None and record["kind"] in {"token", "new"}

    async def input(self, identity, job):
        user, message = job["message"].get("from", {}), job["message"]
        chat = message.get("chat", {})
        self.commands.require_manager(identity, user, chat)
        record = self.current(identity, user)
        if (record["kind"] not in {"token", "new"} or record.get("prompt_id") is None
                or record["prompt_id"] != message.get("reply_to_message", {}).get("message_id")):
            raise ValueError("Reply to the current Admin prompt, or send /menu to start again")
        self.sessions.pop((identity.id, user["id"]))
        def check_authorization():
            self.commands.require_manager(identity, user, chat)
            if record["kind"] == "token":
                target = self.target(user, record)
                if target.id == identity.id or self.communication_stamp(target) != record["communication"]:
                    raise ValueError("The Telegram connection changed. Open /menu again")
        if record["kind"] == "new":
            if job.get("admin_token"):
                raise ValueError("Expected an agent name. Open /menu again")
            target = await self.bridge.create_identity(message.get("text", ""), check_authorization=check_authorization)
            text, extra = self.agent(identity, user, target)
            return "Created " + target.name + ".\n\n" + text, extra
        if record.get("token_job") != job["id"]:
            raise ValueError("Token entry expired. Open /menu and send the token again")
        target = self.target(user, record)
        await self.bridge.configure_communication(target.name, {"token": record.pop("token")},
                                                  check_authorization=check_authorization)
        target = self.bridge.identity(target.name)
        text, extra = self.agent(identity, user, target)
        return f"Connected @{target.telegram.username} to {target.name}.\nOpen https://t.me/{target.telegram.username} and press Start.\n\n" + text, extra

    async def action(self, identity, callback, nonce, action):
        user, chat = callback.get("from", {}), callback.get("message", {}).get("chat", {})
        record = self.current(identity, user)
        if nonce != record["nonce"]:
            raise ValueError("This menu has expired. Send /menu again")
        if action == "home":
            return self.home(identity, user)
        if record["kind"] in {"folders", "launch"}:
            target = self.check_creation(identity, user, chat, record)
            if record["kind"] == "folders":
                if action.startswith("folders"):
                    return self.folders(identity, user, target, int(action[7:]), record)
                if action.startswith("folder") and 0 <= int(action[6:]) < len(record["folders"]):
                    record.update(kind="launch", folder=record["folders"][int(action[6:])])
                    return (f"Create and assign a new Codex sleeve to {target.name}?\nFolder: {record['folder']}\n"
                        "This starts an independent Codex process with --yolo (full access, no approval prompts).\n"
                        "An existing active turn must be stopped or finished before replacement.",
                        {"admin_menu": record["nonce"], "reply_markup": {"inline_keyboard": [
                            [self.button(record, "Create and assign", "launch")],
                            [self.button(record, "← All agents", "home")]]}})
            elif action == "launch":
                self.sessions.pop((identity.id, user["id"]))
                check = lambda: self.check_creation(identity, user, chat, record)
                launch = await self.bridge.create_sleeve(record["folder"], str(uuid.uuid4()), check_authorization=check)
                try:
                    await self.bridge.assign_sleeve(target.name, launch["thread_id"], launch["socket_path"],
                                                   check_authorization=check)
                except (ValueError, RuntimeError, ConnectionError, OSError, asyncio.TimeoutError) as error:
                    raise ValueError(f"Sleeve created ({launch['thread_id']}) but not assigned. "
                        "It remains available; check /list or use Assign sleeve. " + str(error)) from error
                text, extra = self.agent(identity, user, self.bridge.identity(target.name))
                return "New Codex sleeve created and assigned.\n\n" + text, extra
        if record["kind"] == "home":
            if action.startswith("page"):
                return self.home(identity, user, int(action[4:]))
            if action.startswith("open") and 0 <= int(action[4:]) < len(record["agents"]):
                target = self.target(user, {"target_id": record["agents"][int(action[4:])]})
                return self.agent(identity, user, target)
            if action == "new":
                self.commands.require_manager(identity, user, chat)
                return self.prompt(identity, user, "new")
        if record["kind"] == "agent":
            target = self.target(user, record)
            if action == "kill":
                self.sessions.pop((identity.id, user["id"]))
                response = await self.commands.kill(identity.name, user, chat, target.name)
                text, extra = self.agent(identity, user, self.bridge.identity(target.name))
                return response + "\n\n" + text, extra
            self.commands.require_manager(identity, user, chat)
            if target.id == identity.id:
                raise ValueError("Manage Admin's own connection in the desktop app")
            if action == "token":
                return self.prompt(identity, user, "token", target)
            if action == "bind":
                return await self.commands.choose_sleeve(identity, user, chat, target.name)
            if action == "create":
                return self.folders(identity, user, target)
        raise ValueError("Action unavailable. Send /menu again")
