"""SQLite persistence and one-time migration of legacy JSON state."""

import contextlib
import dataclasses
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import time
import tempfile

from .models import AgentIdentity, AgentSleeve, AdminAppSleeve, TelegramIdentity, ExternalIdentity, CommunicationPolicy, telegram_user_selector
from .utils import TOKEN_PATTERN, write_private


SCHEMA = """
CREATE TABLE IF NOT EXISTS identities (
    name TEXT PRIMARY KEY, id TEXT NOT NULL UNIQUE, telegram TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sleeves (
    identity_name TEXT PRIMARY KEY REFERENCES identities(name),
    kind TEXT NOT NULL CHECK (kind IN ('codex', 'admin_app')), thread_id TEXT, socket_path TEXT,
    thread_name TEXT, cwd TEXT, last_turn_id TEXT, UNIQUE (socket_path, thread_id),
    CHECK ((kind = 'codex' AND thread_id IS NOT NULL AND socket_path IS NOT NULL)
        OR (kind = 'admin_app' AND thread_id IS NULL AND socket_path IS NULL
            AND thread_name IS NULL AND cwd IS NULL AND last_turn_id IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS singleton_admin_app_sleeve ON sleeves(kind) WHERE kind = 'admin_app';
CREATE TABLE IF NOT EXISTS inbox (
    position INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, identity_name TEXT NOT NULL REFERENCES identities(name),
    status TEXT NOT NULL CHECK (status IN ('received', 'submitting', 'uncertain', 'done')), payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
    position INTEGER PRIMARY KEY, identity_name TEXT NOT NULL REFERENCES identities(name), payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS external_identities (
    id TEXT PRIMARY KEY, provider TEXT NOT NULL CHECK (provider = 'telegram'),
    user_id TEXT, username TEXT,
    communication_policy TEXT NOT NULL CHECK (communication_policy IN ('always_allowed', 'always_blocked', 'specific_agents')),
    CHECK (user_id IS NOT NULL OR username IS NOT NULL),
    UNIQUE (provider, user_id), UNIQUE (provider, username)
);
CREATE TABLE IF NOT EXISTS external_identity_agents (
    external_identity_id TEXT NOT NULL REFERENCES external_identities(id),
    agent_id TEXT NOT NULL REFERENCES identities(id), PRIMARY KEY (external_identity_id, agent_id)
);
CREATE TABLE IF NOT EXISTS runtime_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class DatabaseLocks:
    """Hold all database locations used by this process until it exits."""
    def __init__(self):
        self.files = {}

    def acquire(self, database):
        database = Path(database).resolve()
        database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        added = {}
        try:
            for path in (database.parent / "server.lock", database.with_name(database.name + ".lock")):
                if path in self.files:
                    continue
                stream = path.open("a")
                added[path] = stream
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            for stream in added.values():
                stream.close()
            raise
        self.files.update(added)
        return tuple(added)

    def release(self, paths):
        for path in paths:
            self.files.pop(path).close()

    def close(self):
        for stream in self.files.values():
            stream.close()
        self.files.clear()


class Store:
    def __init__(self, database_path):
        self.path = Path(database_path).expanduser().resolve()
        self.directory = self.path.parent
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.path.exists():
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        os.chmod(self.path, 0o600)
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1, 2, 3, 4, 5}:
                raise ValueError("Unsupported database version; refusing to overwrite it")
            if version:
                self.data = self.load(connection, version)
                if version < 5:
                    if version < 4:
                        self.migrate_token_files()
                        self.migrate_external_identities()
                    suffix = ".before-admin-sleeve.sqlite3" if version == 4 else ".before-external-identities.sqlite3"
                    backup = self.path.with_name(self.path.stem + suffix)
                    if not backup.exists():
                        temp = backup.with_suffix(".sqlite3.tmp")
                        write_private(temp, "")
                        with contextlib.closing(sqlite3.connect(temp)) as target:
                            connection.backup(target)
                        os.replace(temp, backup)
                    self.save(initialize=True)
                return
        legacy = self.directory / "state.json"
        self.data = json.loads(legacy.read_text()) if legacy.exists() else {
            "version": 2, "identities": {}, "sleeves": {}, "pending_users": {},
            "inbox": [], "outbox": [], "seen_items": [], "events": [],
        }
        if legacy.exists():
            backup = self.directory / "state.before-sqlite.json"
            if not backup.exists():
                write_private(backup, legacy.read_text())
        if self.data.get("version", 1) not in {1, 2}:
            raise ValueError("Unsupported state version; refusing to overwrite it")
        if self.data.get("version", 1) == 1:
            backup = self.directory / "state.before-sleeves.json"
            if not backup.exists():
                write_private(backup, json.dumps(self.data, indent=2) + "\n")
        backup = self.directory / "state.before-allowlists.json"
        if any("allowed_user_id" in data for data in self.data["identities"].values()) and not backup.exists():
            write_private(backup, json.dumps(self.data, indent=2) + "\n")
        self.data.setdefault("communication_rules", self.data.pop("inbound_message_allow_rules", {}))
        self.data.setdefault("turn_recipients", {})
        for name, record in self.data["identities"].items():
            telegram = record if self.data.get("version", 1) == 1 else record.get("telegram")
            if telegram is None:
                continue
            previous_user = telegram.pop("allowed_user_id", None)
            previous_chat = telegram.pop("chat_id", None)
            telegram.setdefault("chats", {})
            telegram.setdefault("resolved_user_ids", {})
            self.data["communication_rules"].setdefault(name, {"rules": []})
            if previous_user is not None:
                self.data["communication_rules"][name] = {"rules": [
                    {"telegram_user_filter": {"allowed_user_ids": [str(previous_user)]}}]}
                if previous_chat is not None:
                    telegram["chats"][str(previous_user)] = previous_chat
                    for entry in self.data["outbox"]:
                        if entry["identity"] == name:
                            entry.setdefault("chat_id", previous_chat)
        if self.data.get("version", 1) == 1:
            self.data["identities"] = {name: {"id": AgentIdentity(name).id, "telegram": telegram}
                                       for name, telegram in self.data["identities"].items()}
            self.data["sleeves"] = self.data.pop("bindings", {})
            for sleeve in self.data["sleeves"].values():
                sleeve.setdefault("kind", "codex")
                sleeve.setdefault("socket_path", str(Path.home() / ".codex/app-server-control/app-server-control.sock"))
            self.data["version"] = 2
        self.migrate_token_files()
        self.migrate_external_identities()
        self.save(initialize=True)

    def migrate_external_identities(self):
        # Conflicting historical username pins require review rather than widening access.
        pins = {}
        for record in self.data["identities"].values():
            for username, user_id in (record.get("telegram") or {}).get("resolved_user_ids", {}).items():
                username = telegram_user_selector(username)
                user_id = telegram_user_selector(user_id)
                if not username.startswith("@") or user_id.startswith("@"):
                    raise ValueError("Invalid legacy Telegram user pin")
                if username in pins and pins[username] != user_id:
                    raise ValueError("Conflicting Telegram username pins; review the legacy permissions")
                pins[username] = user_id
        accounts = {}
        for name, data in self.data.get("communication_rules", {}).items():
            if name not in self.data["identities"] or not isinstance(data, dict) or set(data) != {"rules"} or not isinstance(data["rules"], list):
                raise ValueError("Invalid legacy communication rules")
            for rule in data["rules"]:
                if isinstance(rule, dict) and set(rule) == {"telegram_user_filter"}:
                    user_filter = rule["telegram_user_filter"]
                    if not isinstance(user_filter, dict) or set(user_filter) != {"allowed_user_ids"}:
                        raise ValueError("Invalid legacy Telegram user rule")
                    rule = {"type": "AllowTelegramUser", "users": user_filter["allowed_user_ids"]}
                if (not isinstance(rule, dict) or set(rule) != {"type", "users"}
                        or rule["type"] != "AllowTelegramUser" or not isinstance(rule["users"], list)):
                    raise ValueError("Invalid legacy communication rule")
                for value in rule["users"]:
                    selector = telegram_user_selector(value)
                    user_id = pins.get(selector) if selector.startswith("@") else selector
                    key = user_id or selector
                    account = accounts.setdefault(key, {"user_id": user_id, "usernames": set(), "agent_ids": set()})
                    if selector.startswith("@"):
                        account["usernames"].add(selector)
                    account["agent_ids"].add(self.data["identities"][name]["id"])
        external = {}
        for account in accounts.values():
            account["usernames"].update(username for username, user_id in pins.items() if user_id == account["user_id"])
            identity = ExternalIdentity("telegram", account["user_id"],
                min(account["usernames"]) if account["usernames"] else None,
                CommunicationPolicy("specific_agents", frozenset(account["agent_ids"])))
            external[identity.id] = identity.to_dict()
        self.data["external_identities"] = external
        self.data.pop("communication_rules", None)
        for record in self.data["identities"].values():
            if record.get("telegram"):
                record["telegram"].pop("resolved_user_ids", None)

    def migrate_token_files(self):
        changes = {}
        for name, record in self.data["identities"].items():
            telegram = record.get("telegram")
            if telegram and "token_file" in telegram:
                try:
                    token = Path(telegram["token_file"]).expanduser().read_text().strip()
                except OSError:
                    raise ValueError(f"Cannot migrate the Telegram token for {name}; its token file must be readable") from None
                if not TOKEN_PATTERN.fullmatch(token):
                    raise ValueError(f"Cannot migrate the Telegram token for {name}; its token file is invalid")
                changes[name] = {key: value for key, value in telegram.items() if key != "token_file"}
                changes[name]["token"] = token
        for name, telegram in changes.items():
            self.data["identities"][name]["telegram"] = telegram
        return bool(changes)

    @contextlib.contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def relocate(self, destination, commit_settings):
        """Move the active working set without yielding or replacing another DB.

        The caller must hold destination process locks. The old file remains a
        private backup; a crash before config commit keeps the original active.
        """
        destination = Path(destination).resolve()
        if destination == self.path:
            commit_settings()
            return
        if destination.exists():
            raise ValueError("Choose a new database file; existing files are never overwritten")
        self.save()
        descriptor, temporary = tempfile.mkstemp(prefix=".database-", suffix=".sqlite3", dir=destination.parent)
        os.close(descriptor)
        published = False
        try:
            with self.connect() as source, contextlib.closing(sqlite3.connect(temporary)) as target:
                source.backup(target)
            with open(temporary, "rb") as stream:
                os.fsync(stream.fileno())
            os.link(temporary, destination)
            published = True
            commit_settings()
        except BaseException:
            if published:
                destination.unlink()
            raise
        finally:
            Path(temporary).unlink(missing_ok=True)
        self.path, self.directory = destination, destination.parent

    def load(self, connection, version):
        data = {"version": 2, "identities": {}, "sleeves": {}, "communication_rules": {},
                "pending_users": {}, "turn_recipients": {}, "seen_items": [], "events": [],
                "inbox": [], "outbox": []}
        for row in connection.execute("SELECT * FROM identities ORDER BY rowid"):
            data["identities"][row["name"]] = {"id": row["id"], "telegram": json.loads(row["telegram"])}
            if version < 4:
                data["communication_rules"][row["name"]] = json.loads(row["allow_rules"])
        for row in connection.execute("SELECT * FROM sleeves"):
            record = dict(row)
            name = record.pop("identity_name")
            data["sleeves"][name] = {"kind": "admin_app"} if record["kind"] == "admin_app" else record
        for table in ("inbox", "outbox"):
            data[table] = [json.loads(row["payload"]) for row in connection.execute(
                f"SELECT payload FROM {table} ORDER BY position")]
        for row in connection.execute("SELECT * FROM runtime_state"):
            data[row["key"]] = json.loads(row["value"])
        data["external_identities"] = {}
        if version >= 4:
            data.pop("communication_rules")
            for row in connection.execute("SELECT * FROM external_identities ORDER BY rowid"):
                agents = [item[0] for item in connection.execute(
                    "SELECT agent_id FROM external_identity_agents WHERE external_identity_id = ? ORDER BY agent_id", (row["id"],))]
                identity = ExternalIdentity(row["provider"], row["user_id"], row["username"],
                    CommunicationPolicy.from_dict({"mode": row["communication_policy"], "agent_ids": agents}), row["id"])
                data["external_identities"][identity.id] = identity.to_dict()
        return data

    def save(self, initialize=False):
        # shortcut: transactional snapshots suit this local bridge; use incremental writes if queues grow.
        with self.connect() as connection:
            connection.execute("BEGIN")
            if initialize:
                old_sleeves = connection.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'sleeves'").fetchone()
                rebuild_sleeves = old_sleeves is not None and "admin_app" not in old_sleeves[0]
                if rebuild_sleeves:
                    connection.execute("ALTER TABLE sleeves RENAME TO sleeves_before_admin")
                for statement in SCHEMA.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                if rebuild_sleeves:
                    connection.execute("DROP TABLE sleeves_before_admin")
                if any(row["name"] == "allow_rules" for row in connection.execute("PRAGMA table_info(identities)")):
                    connection.execute("ALTER TABLE identities DROP COLUMN allow_rules")
            for table in ("external_identity_agents", "external_identities", "sleeves", "inbox", "outbox", "identities", "runtime_state"):
                connection.execute(f"DELETE FROM {table}")
            connection.executemany("INSERT INTO identities VALUES (?, ?, ?)", [
                (name, record["id"], json.dumps(record.get("telegram")))
                for name, record in self.data["identities"].items()])
            connection.executemany("INSERT INTO external_identities VALUES (?, ?, ?, ?, ?)", [
                (record["id"], record["provider"], record["user_id"], record["username"], record["communication_policy"]["mode"])
                for record in self.data["external_identities"].values()])
            connection.executemany("INSERT INTO external_identity_agents VALUES (?, ?)", [
                (record["id"], agent_id) for record in self.data["external_identities"].values()
                for agent_id in record["communication_policy"]["agent_ids"]])
            connection.executemany("INSERT INTO sleeves VALUES (?, ?, ?, ?, ?, ?, ?)", [
                (name, sleeve.get("kind", "codex"), sleeve.get("thread_id"), sleeve.get("socket_path"),
                 sleeve.get("thread_name"), sleeve.get("cwd"), sleeve.get("last_turn_id"))
                for name, sleeve in self.data["sleeves"].items()])
            connection.executemany("INSERT INTO inbox VALUES (?, ?, ?, ?, ?)", [
                (position, job["id"], job["identity"], job["status"], json.dumps(job))
                for position, job in enumerate(self.data["inbox"])])
            connection.executemany("INSERT INTO outbox VALUES (?, ?, ?)", [
                (position, entry["identity"], json.dumps(entry))
                for position, entry in enumerate(self.data["outbox"])])
            connection.executemany("INSERT INTO runtime_state VALUES (?, ?)", [
                (key, json.dumps(self.data[key]))
                for key in ("pending_users", "turn_recipients", "seen_items", "events", "sleeve_launches", "sleeve_initializations") if key in self.data])
            connection.execute("PRAGMA user_version = 5")

    def event(self, name, message):
        self.data["events"].append({"time": time.time(), "identity": name, "message": message})
        self.data["events"] = self.data["events"][-100:]
        self.save()

    def identities(self):
        return {name: self.identity(name) for name in self.data["identities"]}

    def identity(self, name):
        if name not in self.data["identities"]:
            raise ValueError("Unknown identity")
        record = self.data["identities"][name]
        sleeve = self.data["sleeves"].get(name)
        return AgentIdentity(name=name, id=record["id"],
            telegram=TelegramIdentity(**record["telegram"]) if record.get("telegram") else None,
            sleeve=(AdminAppSleeve() if sleeve.get("kind") == "admin_app" else AgentSleeve(**sleeve)) if sleeve else None)

    def add_identity(self, identity):
        if not identity.name or len(identity.name) > 80 or identity.name in self.data["identities"]:
            raise ValueError("Use a new identity name of 1–80 characters")
        self.data["identities"][identity.name] = {
            "id": identity.id, "telegram": dataclasses.asdict(identity.telegram) if identity.telegram else None}
        self.save()

    def external_identities(self):
        return [ExternalIdentity(record["provider"], record["user_id"], record["username"],
                    CommunicationPolicy.from_dict(record["communication_policy"]), record["id"])
                for record in self.data["external_identities"].values()]

    def external_identity(self, identity_id):
        return next((identity for identity in self.external_identities() if identity.id == identity_id), None)

    def save_external_identity(self, identity):
        if identity.communication_policy.agent_ids - {record["id"] for record in self.data["identities"].values()}:
            raise ValueError("Policy references an unknown agent identity")
        for other in self.external_identities():
            if other.id != identity.id and other.provider == identity.provider and (
                    (identity.user_id is not None and other.user_id == identity.user_id)
                    or (identity.username is not None and other.username == identity.username)):
                raise ValueError("This external identity is already registered")
        self.data["external_identities"][identity.id] = identity.to_dict()

    def match_external_identity(self, user):
        matches = [identity for identity in self.external_identities() if identity.matches(user)]
        # A registered numeric identity takes precedence over an unbound username.
        return next((identity for identity in matches if identity.user_id is not None), matches[0] if matches else None)
