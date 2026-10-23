import dataclasses
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from agent_home.incoming import incoming_media, save_incoming_media
from agent_home.media import MediaError
from agent_home.models import AgentIdentity, AgentSleeve, CommunicationPolicy, ExternalIdentity, TelegramIdentity
from agent_home.service import Bridge
from agent_home.storage import Store
from agent_home.telegram import TelegramClient


PNG = b"\x89PNG\r\n\x1a\n" + b"test image"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"test video"
TOKEN = "123456789:" + "t" * 35


class IncomingRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = Store(self.root / "home.sqlite3")
        self.bridge = Bridge(self.store)
        self.bridge.telegram = AsyncMock()
        self.bridge.telegram.download_file.return_value = PNG
        self.client = SimpleNamespace(socket_path="/tmp/test.sock", ws=object(), request=AsyncMock())
        self.active = False
        async def request(method, params):
            if method == "thread/read":
                return {"thread": {"status": {"type": "idle"}}}
            if method == "thread/turns/list":
                return {"data": [{"id": "active", "status": "inProgress"}] if self.active else []}
            return {"turnId": "active"} if method == "turn/steer" else {"turn": {"id": "new"}}
        self.client.request.side_effect = request
        self.bridge.ensure_sleeve = AsyncMock(return_value=self.client)
        for number in (1, 2):
            folder = self.root / str(number)
            folder.mkdir()
            name = "TestAgent" + str(number)
            identity = AgentIdentity(name, TelegramIdentity(TOKEN, "TestBot" + str(number), number))
            self.store.add_identity(identity)
            self.store.data["sleeves"][name] = dataclasses.asdict(AgentSleeve(
                f"00000000-0000-4000-8000-{number:012d}", self.client.socket_path, cwd=str(folder)))
            self.store.save_external_identity(ExternalIdentity("telegram", str(40 + number),
                communication_policy=CommunicationPolicy("specific_agents", frozenset({identity.id}))))

    def job(self, number=1, update=1, **content):
        sender = 40 + number
        self.bridge.ingest("TestAgent" + str(number), {"update_id": update, "message": {
            "from": {"id": sender}, "chat": {"id": sender, "type": "private"}, **content}})
        return self.store.data["inbox"][-1]

    def submissions(self):
        return [call for call in self.client.request.await_args_list if call.args[0] in {"turn/start", "turn/steer"}]

    async def test_photo_caption_native_image_private_file_and_receipt_survive_restart(self):
        job = self.job(photo=[{"file_id": "small", "width": 20, "height": 20},
                              {"file_id": "large", "width": 400, "height": 300}], caption="  解释图片\n")
        await self.bridge.handle(job)
        self.assertEqual(self.bridge.telegram.download_file.call_args.args[1]["file_id"], "large")
        method, params = self.submissions()[0].args
        self.assertEqual((method, params["threadId"]), ("turn/start", self.store.data["sleeves"]["TestAgent1"]["thread_id"]))
        self.assertEqual(params["input"][0], {"type": "text", "text": "  解释图片\n"})
        path = Path(job["media_path"])
        self.assertEqual(params["input"][-1], {"type": "localImage", "path": str(path)})
        self.assertEqual(path.read_bytes(), PNG)
        self.assertTrue(path.is_relative_to(self.root / "1"))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual((path.parent / ".gitignore").read_text(), "*\n")
        restarted = Store(self.store.path)
        self.assertEqual(restarted.data["inbox"][0]["media_path"], str(path))
        self.assertEqual(self.store.data["outbox"], [])

    async def test_video_and_image_document_use_correct_inputs_and_active_steering(self):
        cases = [("video", {"file_id": "video"}, MP4, False),
                 ("document", {"file_id": "webm", "file_name": "clip.webm"}, b"\x1aE\xdf\xa3video", False),
                 ("document", {"file_id": "png", "mime_type": "image/png"}, PNG, True)]
        self.active = True
        for index, (kind, file, data, image) in enumerate(cases, 1):
            with self.subTest(kind=kind, file=file):
                self.bridge.telegram.download_file.return_value = data
                job = self.job(update=index, **{kind: file})
                await self.bridge.handle(job)
                method, params = self.submissions()[-1].args
                self.assertEqual((method, params["expectedTurnId"]), ("turn/steer", "active"))
                self.assertEqual(any(item["type"] == "localImage" for item in params["input"]), image)
                self.assertIn(job["media_path"], str(params["input"]))
                if not image:
                    self.assertIn("available local tools", params["input"][-1]["text"])

    async def test_two_agents_keep_downloads_turn_replies_and_albums_isolated(self):
        for number in (1, 2):
            for update in (1, 2):
                job = self.job(number, update, photo=[{"file_id": f"{number}-{update}"}], media_group_id="album")
                job["status"] = "submitting"
                await self.bridge.handle(job)
                name = "TestAgent" + str(number)
                self.bridge.connected[name] = self.client.ws
                await self.bridge.on_codex(self.client, {"method": "turn/started", "params": {
                    "threadId": job["media_sleeve"]["thread_id"], "turn": {"id": "early"}}})
                self.assertEqual(self.store.data["turn_recipients"][name]["early"], [40 + number])
                self.bridge.relay_item(name, "new", {"id": str(update), "text": "Received"})
                job["status"] = "done"
        self.assertEqual(len(self.submissions()), 4)
        self.assertEqual([entry["chat_id"] for entry in self.store.data["outbox"]], [41, 41, 42, 42])
        self.assertEqual(len({job["media_path"] for job in self.store.data["inbox"]}), 4)

    async def test_revocation_and_cancellation_after_download_prevent_file_and_submission(self):
        for cancel in (True, False):
            job = self.job(update=1 if cancel else 2, photo=[{"file_id": "photo"}])
            async def download(*args):
                if cancel:
                    job["cancel_requested"] = True
                else:
                    external = self.store.match_external_identity({"id": 41})
                    self.store.save_external_identity(dataclasses.replace(external, communication_policy=CommunicationPolicy()))
                return PNG
            self.bridge.telegram.download_file.side_effect = download
            await self.bridge.handle(job)
        self.assertEqual(self.submissions(), [])
        self.assertFalse((self.root / "1/.local").exists())

    async def test_unusable_admin_or_missing_sleeve_never_downloads(self):
        for sleeve in (None, {"kind": "admin_app"}):
            if sleeve is None:
                self.store.data["sleeves"].pop("TestAgent1", None)
            else:
                self.store.data["sleeves"]["TestAgent1"] = sleeve
            await self.bridge.handle(self.job(update=1 if sleeve is None else 2, photo=[{"file_id": "photo"}]))
        self.bridge.telegram.download_file.assert_not_called()
        self.assertEqual(self.submissions(), [])
        self.assertIn("typed commands only", self.store.data["outbox"][-1]["text"])

    async def test_invalid_download_and_cached_attachment_from_old_sleeve_warn_without_submission(self):
        job = self.job(photo=[{"file_id": "photo"}])
        self.bridge.telegram.download_file.return_value = b"not an image"
        await self.bridge.handle(job)
        self.assertIn("File contents", self.store.data["outbox"][-1]["text"])
        job.update(media_path="/previous/private.png", media_sleeve={"thread_id": "old", "socket_path": "/tmp/old"})
        await self.bridge.handle(job)
        self.assertIn("previous sleeve", self.store.data["outbox"][-1]["text"])
        self.assertEqual(self.submissions(), [])


class IncomingDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_streamed_download_and_provider_location_validation(self):
        metadata = {"file_path": "photos/image.jpg", "file_size": len(PNG)}
        calls = []
        async def get_file(request):
            calls.append("getFile")
            return web.json_response({"ok": True, "result": metadata})
        async def download(request):
            calls.append("download")
            return web.Response(body=PNG)
        app = web.Application()
        app.router.add_post("/getFile", get_file)
        app.router.add_get("/download", download)
        server = TestServer(app)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        session = aiohttp.ClientSession()
        self.addAsyncCleanup(session.close)
        telegram = TelegramClient()
        telegram.http = SimpleNamespace(post=lambda url, **kw: session.post(server.make_url("/getFile"), **kw),
            get=lambda url, **kw: session.get(server.make_url("/download"), **kw))
        identity = AgentIdentity("Test", TelegramIdentity(TOKEN, "TestBot", 1))
        self.assertEqual(await telegram.download_file(identity, {"file_id": "photo"}), PNG)
        self.assertEqual(calls, ["getFile", "download"])
        for file in ({"file_id": "photo", "file_size": 20_000_001}, {"file_id": ""}):
            with self.assertRaises(MediaError):
                await telegram.download_file(identity, file)
        self.assertEqual(len(calls), 2)
        with self.assertRaises(MediaError):
            await telegram.download_file(identity, {"file_id": "photo"}, limit=8)
        metadata["file_size"] = 1
        with self.assertRaises(MediaError):
            await telegram.download_file(identity, {"file_id": "photo"}, limit=8)
        metadata["file_path"] = "../secret"
        before = calls.count("download")
        with self.assertRaises(MediaError):
            await telegram.download_file(identity, {"file_id": "photo"})
        self.assertEqual(calls.count("download"), before)

    def test_symlink_destination_and_invalid_media_never_write_outside_workspace(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            outside = root / "outside"
            outside.mkdir()
            (root / ".local").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(OSError):
                save_incoming_media(root, "id", PNG, {}, "photo")
            self.assertEqual(list(outside.iterdir()), [])
            with self.assertRaises(MediaError):
                save_incoming_media(root, "id", b"bad", {}, "photo")
        self.assertIsNone(incoming_media({"document": {"file_id": "pdf", "file_name": "file.pdf"}}))


if __name__ == "__main__":
    unittest.main()
