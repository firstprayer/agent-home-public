"""Local media references and checked, optional workspace-bounded file access."""

import contextlib
import os
from pathlib import Path
import re
import stat
import uuid
from urllib.parse import unquote, urlsplit


FORMATS = {
    ".jpg": ("photo", "image/jpeg"), ".jpeg": ("photo", "image/jpeg"),
    ".png": ("photo", "image/png"), ".webp": ("photo", "image/webp"),
    ".gif": ("animation", "image/gif"),
    ".mp4": ("video", "video/mp4"), ".m4v": ("video", "video/mp4"),
    ".mov": ("document", "video/quicktime"), ".webm": ("document", "video/webm"),
    ".mkv": ("document", "video/x-matroska"), ".avi": ("document", "video/x-msvideo"),
}
MARKDOWN_MEDIA = re.compile(
    r"(?<![\\!])!?\[(?:\\.|[^\]\\])*\]\(\s*"
    r'''(?:<(?P<angle>[^>\n]+)>(?:\s+(?:"[^"]*"|'[^']*'))?'''
    r"|(?P<path>(?:\\.|[^()\n]|\([^()\n]*\))+?))\s*\)"
)
ATTACHMENT_MARKER = re.compile(r"(?<!\\)\[\[attach:(?P<path>[^\]\n]+)\]\]")


class MediaError(ValueError):
    """A local attachment cannot be uploaded; deliver a text explanation instead."""


def media_references(text, cwd):
    if not cwd:
        return []
    root = Path(cwd).expanduser().resolve()
    result, seen, fence = [], set(), None
    for line in text.splitlines():
        delimiter = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        if delimiter:
            marker, rest = delimiter.groups()
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not rest.strip():
                fence = None
            continue
        if fence is not None or line.startswith(("    ", "\t")):
            continue
        line = re.sub(r"(`+).*?\1", "", line)
        matches = [(match.start(), match["angle"] or match["path"], match["path"] is not None)
                   for match in MARKDOWN_MEDIA.finditer(line)]
        matches.extend((match.start(), match["path"], False)
                       for match in ATTACHMENT_MARKER.finditer(line))
        for _, value, markdown_path in sorted(matches):
            value = value.strip()
            if markdown_path:
                value = re.sub(r'''\s+(?:"[^"]*"|'[^']*')$''', "", value)
            value = re.sub(r"\\([\\()\[\]<> ])", r"\1", value)
            try:
                parsed = urlsplit(value)
            except ValueError:
                continue
            if parsed.scheme:
                if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
                    continue
                value = parsed.path
            value = unquote(value)
            path = Path(value).expanduser() if value.startswith("~/") else Path(value)
            if path.suffix.lower() not in FORMATS:
                continue
            path = Path(os.path.abspath(root / path))
            if path not in seen:
                result.append({"path": str(path), "root": str(root)})
                seen.add(path)
    return result


@contextlib.contextmanager
def open_media(media, *, allow_external=False):
    """Open regular files without following symlinks, applying the agent's boundary."""
    try:
        path = Path(media["path"])
        if not path.is_absolute():
            raise MediaError("Attachment path must be absolute")
        root = Path(path.anchor) if allow_external else Path(media["root"]).resolve(strict=True)
        try:
            relative = path.relative_to(root)
        except ValueError:
            raise MediaError("This agent does not allow attachments outside its working folder") from None
        if ".." in relative.parts or not relative.parts:
            raise MediaError("File is outside the originating sleeve's workspace")
        with contextlib.ExitStack() as stack:
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, directory)
            for part in relative.parts[:-1]:
                directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                stack.callback(os.close, directory)
            descriptor = os.open(relative.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
            stream = stack.enter_context(os.fdopen(descriptor, "rb"))
            info = os.fstat(stream.fileno())
            kind, mime = FORMATS[path.suffix.lower()]
            if not stat.S_ISREG(info.st_mode):
                raise MediaError("Attachment is not a regular file")
            limit_mb = 10 if kind == "photo" else 50
            if info.st_size == 0:
                raise MediaError("Attachment is empty")
            if info.st_size > limit_mb * 1_000_000:
                raise MediaError(f"Attachment is {info.st_size / 1_000_000:.1f} MB and exceeds Telegram's {limit_mb} MB upload limit; send a smaller copy")
            header = stream.read(32)
            valid = matches_media_header(header, mime)
            if not valid:
                raise MediaError("File contents do not match a supported image or video")
            stream.seek(0)
            yield stream, kind, mime
    except MediaError:
        raise
    except (OSError, ValueError, KeyError) as error:
        raise MediaError("File is missing, unreadable, or uses a symlink") from error


def matches_media_header(header, mime):
    return {
        "image/jpeg": header.startswith(b"\xff\xd8\xff"),
        "image/png": header.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/webp": header.startswith(b"RIFF") and header[8:12] == b"WEBP",
        "image/gif": header.startswith((b"GIF87a", b"GIF89a")),
        "video/mp4": header[4:8] == b"ftyp",
        "video/quicktime": header[4:8] in {b"ftyp", b"moov", b"mdat", b"wide"},
        "video/webm": header.startswith(b"\x1aE\xdf\xa3"),
        "video/x-matroska": header.startswith(b"\x1aE\xdf\xa3"),
        "video/x-msvideo": header.startswith(b"RIFF") and header[8:12] == b"AVI ",
    }.get(mime, False)


def save_attachment(cwd, job_id, data, folder, extension):
    """Keep received files private and inside the exact workspace, without symlinks."""
    root = Path(cwd).expanduser().resolve(strict=True)
    filename = folder + "-" + uuid.uuid5(uuid.NAMESPACE_URL, str(job_id)).hex + extension
    with contextlib.ExitStack() as stack:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, directory)
        for part in (".local", "agent-home", folder):
            try:
                os.mkdir(part, mode=0o700, dir_fd=directory)
            except FileExistsError:
                pass
            directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            stack.callback(os.close, directory)
        try:
            descriptor = os.open(".gitignore", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "w") as stream:
                stream.write("*\n")
        descriptor = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            os.unlink(filename, dir_fd=directory)
            raise
    return str(root / ".local" / "agent-home" / folder / filename)
