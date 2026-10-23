"""Typed service configuration, validated once at the boundary."""

import dataclasses
import os
import json
from pathlib import Path
import tempfile
import tomllib


@dataclasses.dataclass(frozen=True)
class ServiceConfig:
    db_path: Path = dataclasses.field(default_factory=lambda: Path.home() / ".config/agent-communication/agent-home.sqlite3")
    port: int = 8765
    sleeve_allowed_folders: tuple[Path, ...] = ()

    @staticmethod
    def desktop_path():
        """Keep desktop upgrades separate from private, user-owned settings."""
        path = Path.home() / ".config/agent-communication/service.toml"
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
                stream.write('db_path = "~/.config/agent-communication/agent-home.sqlite3"\nport = 8765\nsleeve_allowed_folders = []\n')
        except FileExistsError:
            pass
        return path

    @classmethod
    def load(cls, path=None, *, db_path=None, port=None, sleeve_allowed_folders=None):
        data = {}
        if path:
            path = Path(path).expanduser().resolve()
            with path.open("rb") as stream:
                data = tomllib.load(stream)
            unknown = set(data) - {field.name for field in dataclasses.fields(cls)}
            if unknown:
                raise ValueError("Unknown service configuration fields: " + ", ".join(sorted(unknown)))
        if db_path is not None:
            data["db_path"] = str(db_path)
        if port is not None:
            data["port"] = port
        if sleeve_allowed_folders is not None:
            data["sleeve_allowed_folders"] = sleeve_allowed_folders
        config = cls(**data)
        if type(config.port) is not int or not 1 <= config.port <= 65535:
            raise ValueError("port must be an integer between 1 and 65535")
        if not isinstance(config.db_path, (str, Path)) or not str(config.db_path).strip():
            raise ValueError("db_path must be a non-empty filesystem path")
        database = Path(config.db_path).expanduser()
        if not database.is_absolute() and path:
            database = path.parent / database
        if database.exists() and not database.is_file():
            raise ValueError("db_path must name a database file, not a directory")
        if not isinstance(config.sleeve_allowed_folders, (list, tuple)):
            raise ValueError("sleeve_allowed_folders must be a list of directory paths")
        folders = []
        for value in config.sleeve_allowed_folders:
            if not isinstance(value, (str, Path)) or not str(value).strip():
                raise ValueError("Each allowed folder must be a non-empty directory path")
            folder = Path(value).expanduser()
            if not folder.is_absolute() and path:
                folder = path.parent / folder
            folder = folder.resolve()
            if folder.exists() and not folder.is_dir():
                raise ValueError("Allowed folders must be directories")
            if folder not in folders:
                folders.append(folder)
        return dataclasses.replace(config, db_path=database.resolve(), sleeve_allowed_folders=tuple(folders))

    def save(self, path):
        """Atomically commit the complete typed config outside the database."""
        path = Path(path)
        payload = (f"db_path = {json.dumps(str(self.db_path), ensure_ascii=False)}\n"
            f"port = {self.port}\n"
            f"sleeve_allowed_folders = {json.dumps([str(p) for p in self.sleeve_allowed_folders], ensure_ascii=False)}\n")
        descriptor, temporary = tempfile.mkstemp(prefix=".service-", suffix=".toml", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
