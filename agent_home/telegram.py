"""Telegram API transport. Bot credentials never leave this boundary."""

import asyncio
from pathlib import PurePosixPath
import re

import aiohttp

from .incoming import MAX_MEDIA_BYTES
from .media import MediaError, open_media
from .utils import TOKEN_PATTERN, safe_error
from .voice import MAX_VOICE_BYTES, VoiceError


class TelegramAPIError(RuntimeError):
    def __init__(self, method, result):
        super().__init__(f"Telegram {method}: {safe_error(result.get('description', 'request failed'))}")
        self.code = result.get("error_code")


class TelegramClient:
    def __init__(self):
        self.http = None

    async def start(self):
        self.http = aiohttp.ClientSession()

    async def close(self):
        if self.http:
            await self.http.close()

    async def __call__(self, identity, method, **params):
        return await self.request(identity, method, json=params)

    async def download_voice(self, identity, voice):
        if (not isinstance(voice, dict) or not isinstance(voice.get("file_id"), str)
                or not voice["file_id"]):
            raise VoiceError("The voice note has no valid Telegram file ID")
        if type(voice.get("duration")) is not int or voice["duration"] < 0:
            raise VoiceError("The voice note is empty or has an invalid duration")
        return await self.download_file(identity, voice, limit=MAX_VOICE_BYTES, error_type=VoiceError)

    async def download_file(self, identity, file, *, limit=MAX_MEDIA_BYTES, error_type=MediaError):
        if not isinstance(file, dict) or not isinstance(file.get("file_id"), str) or not file["file_id"]:
            raise error_type("The attachment has no valid Telegram file ID")
        def check_size(metadata):
            size = metadata.get("file_size", 1)
            if type(size) is not int or not 0 < size <= limit:
                raise error_type("Attachment is empty or exceeds Telegram's 20 MB download limit")
        check_size(file)
        metadata = await self(identity, "getFile", file_id=file["file_id"])
        if not isinstance(metadata, dict):
            raise error_type("Telegram returned invalid file metadata")
        path = metadata.get("file_path", "")
        if (not isinstance(path, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", path)
                or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts):
            raise error_type("Telegram returned an invalid file location")
        check_size(metadata)
        try:
            async with self.http.get(f"https://api.telegram.org/file/bot{identity.telegram.token}/{path}",
                timeout=aiohttp.ClientTimeout(total=60), allow_redirects=False) as response:
                if response.status != 200:
                    raise error_type("The attachment could not be downloaded; try again")
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > limit:
                        raise error_type("Attachment exceeds Telegram's 20 MB download limit")
            if not data:
                raise error_type("The attachment is empty; try another file")
            return bytes(data)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            # Download exception URLs contain bot tokens.
            raise error_type("The attachment could not be downloaded; try again or send text") from None

    async def send_media(self, identity, media, *, chat_id):
        async def upload(stream, field, mime):
            form = aiohttp.FormData()
            form.add_field("chat_id", str(chat_id))
            filename = media["path"].rsplit("/", 1)[-1]
            form.add_field(field, stream, filename=filename, content_type=mime)
            return await self.request(identity, "send" + field.title(), data=form)
        try:
            with open_media(media, allow_external=identity.allow_external_media) as (stream, kind, mime):
                return await upload(stream, kind, mime)
        except TelegramAPIError as error:
            if error.code != 400 or kind == "document":
                raise
            # Telegram may reject a valid file's dimensions or codec for inline display.
            with open_media(media, allow_external=identity.allow_external_media) as (stream, _, mime):
                return await upload(stream, "document", mime)

    async def request(self, identity, method, **body):
        token = identity.telegram.token
        if not TOKEN_PATTERN.fullmatch(token):
            raise ValueError("Invalid Telegram bot token")
        try:
            async with self.http.post(
                f"https://api.telegram.org/bot{token}/{method}", **body,
                timeout=aiohttp.ClientTimeout(total=120 if "data" in body else 40), allow_redirects=False,
            ) as response:
                result = await response.json()
            if not result.get("ok"):
                raise TelegramAPIError(method, result)
            return result["result"]
        except aiohttp.ClientError:
            # aiohttp exceptions can contain credential-bearing request URLs.
            raise ConnectionError(f"Telegram {method} connection failed") from None
