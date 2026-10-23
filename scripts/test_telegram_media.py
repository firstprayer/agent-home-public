"""Bounded live checks using two dedicated bots, root threads, and a private test DB.

Run: uv run python -m scripts.test_telegram_media --credentials /private/tokens.json
Credentials: {"FirstTestBot": "token", "SecondTestBot": "token"}.
Both bots must have Bot-to-Bot Communication Mode enabled in BotFather.
"""

import argparse
import asyncio
import contextlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import time
import uuid
import zlib

import aiohttp

from agent_home.codex import DEFAULT_SOCKET
from agent_home.models import CommunicationPolicy, ExternalIdentity
from agent_home.service import Bridge
from agent_home.storage import DatabaseLocks, Store
from agent_home.utils import safe_error, write_private


def fixtures(folder):
    folder.mkdir(parents=True, exist_ok=True)
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    image = folder / "red.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">2I5B", 128, 128, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + b"\xff\0\0" * 128) * 128)) + chunk(b"IEND", b""))
    executable = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("ffmpeg is required to generate the live video fixture")
    video = folder / "blue.mp4"
    subprocess.run([executable, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
        "-i", "color=c=blue:s=128x128:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)],
        check=True, timeout=20, capture_output=True)
    return image, video


async def run(args):
    credentials = json.loads(args.credentials.expanduser().read_text())
    if not isinstance(credentials, dict) or len(credentials) != 2:
        raise ValueError("Provide exactly two dedicated test bot credentials")
    root = args.workspace.expanduser().absolute()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_private(root / ".gitignore", "*\n")
    image, video = fixtures(root / "fixtures")
    locks = DatabaseLocks()
    locks.acquire(root / "state.sqlite3")
    bridge = Bridge(Store(root / "state.sqlite3"))
    delivery = None
    report = {"checks": [], "workspace": str(root)}
    await bridge.telegram.start()
    try:
        names = []
        for index, (username, token) in enumerate(credentials.items(), 1):
            name = "TelegramMediaTest" + str(index)
            names.append(name)
            existing = bridge.store.identities().get(name)
            if not existing:
                await bridge.create_identity(name, {"token": token})
                # Drive a finite number of updates below; never run an automatic echo loop.
                poller = bridge.pollers.pop(name)
                poller.cancel()
                await asyncio.gather(poller, return_exceptions=True)
            elif not existing.telegram or existing.telegram.token != token:
                raise ValueError("Test DB belongs to different credentials; choose another test workspace")
            identity = bridge.identity(name)
            webhook = await bridge.telegram(identity, "getWebhookInfo")
            if webhook.get("url"):
                raise ValueError("A test bot has a webhook; choose unused dedicated test bots")
            if identity.sleeve is None:
                folder = root / name
                folder.mkdir(exist_ok=True)
                client = await bridge.client(str(DEFAULT_SOCKET))
                saved = folder / "thread.json"
                if saved.exists():
                    thread_id = json.loads(saved.read_text())["thread_id"]
                else:
                    created = await client.request("thread/start", {"cwd": str(folder), "ephemeral": False,
                        "approvalPolicy": "never", "sandbox": "workspace-write"})
                    thread_id = created["thread"]["id"]
                    write_private(saved, json.dumps({"thread_id": thread_id}))
                # Assignment of a completely empty root exercises automatic initialization.
                await bridge.assign_sleeve(name, thread_id, str(DEFAULT_SOCKET))
            report.setdefault("threads", {})[name] = bridge.identity(name).sleeve.thread_id
        first, second = [bridge.identity(name) for name in names]
        if first.telegram.bot_id == second.telegram.bot_id:
            raise ValueError("The two test identities must use distinct bots")
        for sender, receiver in ((first, second), (second, first)):
            external = ExternalIdentity("telegram", str(sender.telegram.bot_id), sender.telegram.username,
                CommunicationPolicy("specific_agents", frozenset({receiver.id})))
            prior = bridge.store.match_external_identity({"id": sender.telegram.bot_id})
            if not prior:
                bridge.store.save_external_identity(external)
        delivery = asyncio.create_task(bridge.deliver())
        nonce = uuid.uuid4().hex[:12]

        async def wait_message(name, predicate, *, ingest=False):
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                identity = bridge.identity(name)
                updates = await bridge.telegram(identity, "getUpdates", offset=identity.telegram.offset,
                    timeout=5, allowed_updates=["message"])
                matched = None
                for update in updates:
                    message = update.get("message", {})
                    if matched is None and predicate(message):
                        matched = update
                        if ingest:
                            bridge.ingest(name, update)
                    bridge.store.data["identities"][name]["telegram"]["offset"] = update["update_id"] + 1
                bridge.store.save()
                if matched:
                    return matched
                await asyncio.sleep(0.1)
            raise TimeoutError("Timed out waiting for the expected test message or agent reply")

        cases = [(first, second, "text", None, None),
                 (second, first, "image", image, "red"),
                 (first, second, "video", video, "blue")]
        for sender, receiver, label, path, color in cases:
            expected = f"AGENT_HOME_TEST {nonce} {label}" + (f" {color}" if color else "")
            instruction = (f"Agent Home isolated integration test. Reply exactly: {expected}. "
                "Do not send commentary or a question. " +
                (f"First inspect the attached {label} and verify its dominant color is {color}. "
                 "For video, use local ffmpeg or available tools to read a frame. " if color else ""))
            if path:
                field = "photo" if label == "image" else "video"
                with path.open("rb") as stream:
                    form = aiohttp.FormData()
                    form.add_field("chat_id", "@" + receiver.telegram.username)
                    form.add_field("caption", instruction)
                    form.add_field(field, stream, filename=path.name,
                        content_type="image/png" if label == "image" else "video/mp4")
                    await bridge.telegram.request(sender, "send" + field.title(), data=form)
            else:
                await bridge.telegram(sender, "sendMessage", chat_id="@" + receiver.telegram.username, text=instruction)
            incoming = await wait_message(receiver.name, lambda message:
                message.get("from", {}).get("id") == sender.telegram.bot_id
                and nonce in (message.get("text") or message.get("caption", "")), ingest=True)
            job = next(job for job in bridge.store.data["inbox"]
                if job["id"] == f"{receiver.telegram.bot_id}:{incoming['update_id']}")
            job["status"] = "submitting"
            bridge.store.save()
            try:
                await bridge.handle(job)
            except BaseException:
                job["status"] = "uncertain"
                bridge.store.save()
                raise
            job["status"] = "done"
            bridge.store.save()
            await wait_message(sender.name, lambda message:
                message.get("from", {}).get("id") == receiver.telegram.bot_id
                and message.get("text", "").strip().removesuffix(".") == expected)
            report["checks"].append({"kind": label, "sender": sender.telegram.username,
                "receiver": receiver.telegram.username, "thread_id": receiver.sleeve.thread_id,
                "turn_id": job.get("turn_id"), "passed": True})
            print(label + ": passed", flush=True)
        report["passed"] = True
    except Exception as error:
        report["passed"] = False
        report["error"] = safe_error(error)
        raise
    finally:
        write_private(root / "report.json", json.dumps(report, indent=2) + "\n")
        if delivery:
            delivery.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await delivery
        await bridge.stop(None)
        locks.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, default=Path(".local/telegram-media-test"))
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except Exception as error:
        parser.exit(1, safe_error(error) + "\n")


if __name__ == "__main__":
    main()
