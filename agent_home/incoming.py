"""Telegram image/video selection and workspace-local Codex inputs."""

from pathlib import Path

from .media import FORMATS, MediaError, matches_media_header, save_attachment


MAX_MEDIA_BYTES = 20_000_000


def incoming_media(message):
    if "photo" in message:
        photos = message["photo"]
        if not isinstance(photos, list) or not photos or any(not isinstance(photo, dict) for photo in photos):
            raise MediaError("Telegram returned invalid photo metadata")
        def area(photo):
            width, height = photo.get("width", 0), photo.get("height", 0)
            return width * height if type(width) is int and type(height) is int else 0
        return max(photos, key=area), "photo"
    for kind in ("video", "video_note", "animation", "document"):
        if kind not in message:
            continue
        file = message[kind]
        if not isinstance(file, dict):
            raise MediaError("Telegram returned invalid media metadata")
        if kind == "document":
            mime, filename = file.get("mime_type", ""), file.get("file_name", "")
            if not (isinstance(mime, str) and mime.startswith(("image/", "video/"))
                    or isinstance(filename, str) and Path(filename).suffix.lower() in FORMATS):
                return None
        return file, kind
    return None


def save_incoming_media(cwd, job_id, data, file, kind):
    if not cwd:
        raise MediaError("The sleeve has no working folder in which to save media")
    if not 0 < len(data) <= MAX_MEDIA_BYTES:
        raise MediaError("Media is empty or exceeds Telegram's 20 MB download limit")
    filename = file.get("file_name", "")
    preferred = Path(filename).suffix.lower() if isinstance(filename, str) else ""
    extensions = dict.fromkeys([preferred, *FORMATS])
    for extension in extensions:
        if extension not in FORMATS:
            continue
        _, mime = FORMATS[extension]
        if kind == "photo" and mime not in {"image/jpeg", "image/png", "image/webp"}:
            continue
        if kind in {"video", "video_note"} and not mime.startswith("video/"):
            continue
        if matches_media_header(data[:32], mime):
            return save_attachment(cwd, job_id, data, "incoming", extension)
    raise MediaError("File contents do not match a supported image or video")


def media_input(path):
    if Path(path).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
        return [{"type": "text", "text": f"The user sent a Telegram image saved at: {path}"},
                {"type": "localImage", "path": path}]
    return [{"type": "text", "text": f"The user sent a Telegram video or animation saved at: {path}\n\n"
             "Inspect it using available local tools and respond to the user's request. "
             "If you cannot decode it, explain which tool is missing."}]
