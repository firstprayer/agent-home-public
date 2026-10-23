"""Private persistence and safe message formatting."""

import os
from pathlib import Path
import re

TOKEN_PATTERN = re.compile(r"\d{5,}:[A-Za-z0-9_-]{25,}")


def safe_error(error):
    return TOKEN_PATTERN.sub("[redacted bot token]", str(error))[:500]


def write_private(path, data):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def text_chunks(text, limit=3900):
    """Telegram measures message length in UTF-16 code units."""
    chunk, length = [], 0
    for char in text:
        size = 2 if ord(char) > 0xFFFF else 1
        if length + size > limit:
            yield "".join(chunk)
            chunk, length = [], 0
        chunk.append(char)
        length += size
    if chunk:
        yield "".join(chunk)
