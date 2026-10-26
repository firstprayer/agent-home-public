"""Domain records. Identity survives communication and sleeve changes."""

import dataclasses
import re
import uuid


@dataclasses.dataclass
class TelegramIdentity:
    token: str = dataclasses.field(repr=False)
    username: str
    bot_id: int
    offset: int = 0
    chats: dict[str, int] = dataclasses.field(default_factory=dict)


def telegram_user_selector(value):
    if not isinstance(value, str):
        raise ValueError("Telegram users must be strings")
    if re.fullmatch(r"[1-9][0-9]*", value):
        return value
    if re.fullmatch(r"@?[A-Za-z0-9_]{5,32}", value) and not value.lstrip("@").isdigit():
        return "@" + value.lstrip("@").lower()
    raise ValueError("Use a positive numeric user ID or a Telegram @username")


@dataclasses.dataclass(frozen=True)
class CommunicationPolicy:
    mode: str = "always_blocked"
    agent_ids: frozenset[str] = dataclasses.field(default_factory=frozenset)

    def __post_init__(self):
        if self.mode not in {"always_allowed", "always_blocked", "specific_agents"}:
            raise ValueError("Unknown communication policy")
        if self.mode != "specific_agents" and self.agent_ids:
            raise ValueError("Only specific_agents policies may list agents")
        for agent_id in self.agent_ids:
            if not isinstance(agent_id, str) or str(uuid.UUID(agent_id)) != agent_id:
                raise ValueError("Policy agents must be canonical UUIDs")

    @classmethod
    def from_dict(cls, data):
        if (not isinstance(data, dict) or set(data) != {"mode", "agent_ids"}
                or not isinstance(data["mode"], str) or not isinstance(data["agent_ids"], list)
                or any(not isinstance(value, str) for value in data["agent_ids"])):
            raise ValueError("Communication policy requires mode and an agent_ids list")
        return cls(data["mode"], frozenset(data["agent_ids"]))

    def allows(self, agent_id):
        return self.mode == "always_allowed" or (self.mode == "specific_agents" and agent_id in self.agent_ids)

    def to_dict(self):
        return {"mode": self.mode, "agent_ids": sorted(self.agent_ids)}


@dataclasses.dataclass(frozen=True)
class ExternalIdentity:
    provider: str
    user_id: str | None = None
    username: str | None = None
    communication_policy: CommunicationPolicy = dataclasses.field(default_factory=CommunicationPolicy)
    id: str = dataclasses.field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self):
        if self.provider != "telegram":
            raise ValueError("Only Telegram external identities are supported")
        if self.user_id is not None and (not isinstance(self.user_id, str) or not re.fullmatch(r"[1-9][0-9]*", self.user_id)):
            raise ValueError("Telegram user_id must be a positive numeric string")
        if self.username is not None:
            username = telegram_user_selector(self.username)
            if not username.startswith("@"):
                raise ValueError("Use a Telegram @username")
            object.__setattr__(self, "username", username)
        if self.user_id is None and self.username is None:
            raise ValueError("Provide a Telegram user ID or username")
        if not isinstance(self.id, str) or str(uuid.UUID(self.id)) != self.id:
            raise ValueError("External identity ID must be a canonical UUID")

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or set(data) != {"provider", "user_id", "username", "communication_policy"}:
            raise ValueError("External identity requires provider, user_id, username, and communication_policy")
        return cls(data["provider"], data["user_id"], data["username"], CommunicationPolicy.from_dict(data["communication_policy"]))

    def matches(self, user):
        if type(user.get("id")) is not int or user["id"] <= 0:
            return False
        if self.user_id is not None:
            return self.user_id == str(user["id"])
        username = user.get("username")
        return isinstance(username, str) and self.username == "@" + username.lower()

    def to_dict(self):
        return {"id": self.id, "provider": self.provider, "user_id": self.user_id,
                "username": self.username, "communication_policy": self.communication_policy.to_dict()}


@dataclasses.dataclass
class AgentSleeve:
    """An exact runtime address, never the identity itself."""

    thread_id: str
    socket_path: str
    kind: str = "codex"
    thread_name: str | None = None
    cwd: str | None = None
    last_turn_id: str | None = None


@dataclasses.dataclass(frozen=True)
class AdminAppSleeve:
    """The application's singleton fixed-command runtime, independent of Codex."""

    kind: str = dataclasses.field(default="admin_app", init=False)


@dataclasses.dataclass(frozen=True)
class AgentIdentity:
    name: str
    telegram: TelegramIdentity | None = None
    id: str = dataclasses.field(default_factory=lambda: str(uuid.uuid4()))
    sleeve: AgentSleeve | AdminAppSleeve | None = None
    allow_external_media: bool = True

    def __post_init__(self):
        if type(self.allow_external_media) is not bool:
            raise ValueError("allow_external_media must be a boolean")
