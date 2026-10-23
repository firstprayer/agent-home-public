import dataclasses
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from agent_home.models import AgentIdentity, AgentSleeve, CommunicationPolicy, ExternalIdentity, TelegramIdentity
from agent_home.service import Bridge
from agent_home.storage import Store
from agent_home.telegram import TelegramClient
from agent_home.voice import VoiceError, save_voice


TOKEN = "123456789:" + "t" * 35
THREAD = "11111111-1111-4111-8111-111111111111"


class VoiceRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "state.sqlite3")
        self.store.add_identity(AgentIdentity("Worker", TelegramIdentity(TOKEN, "TestBot", 11, chats={"42": 42})))
        self.store.data["sleeves"]["Worker"] = dataclasses.asdict(AgentSleeve(THREAD, "/tmp/codex.sock", cwd=self.temp.name))
        self.external = ExternalIdentity("telegram", "42", communication_policy=CommunicationPolicy("always_allowed"))
        self.store.save_external_identity(self.external)
        self.bridge = Bridge(self.store)
        self.bridge.telegram = AsyncMock()
        self.bridge.telegram.download_voice.return_value = b"audio"
        self.client = AsyncMock()
        self.active = False

        async def request(method, params):
            if method == "thread/read":
                return {"thread": {"status": {"type": "active" if self.active else "idle"}}}
            if method == "thread/turns/list":
                return {"data": [{"id": "running", "status": "inProgress"}] if self.active else []}
            return {"turnId": "running"} if method == "turn/steer" else {"turn": {"id": "new"}}

        self.client.request.side_effect = request
        self.bridge.ensure_sleeve = AsyncMock(return_value=self.client)

    def job(self, user_id=42):
        update = {"update_id": 1, "message": {"from": {"id": user_id}, "chat": {"id": user_id, "type": "private"},
            "voice": {"file_id": "voice-id", "duration": 3, "file_size": 64}}}
        self.bridge.ingest("Worker", update)
        return self.store.data["inbox"][-1] if self.store.data["inbox"] else None

    def submitted_methods(self):
        return [call.args[0] for call in self.client.request.await_args_list if call.args[0].startswith("turn/")]

    async def test_voice_saves_private_audio_and_starts_exact_thread_without_api_key(self):
        job = self.job()
        with patch.dict(os.environ, {}, clear=True):
            await self.bridge.handle(job)
        method, params = self.client.request.call_args.args
        self.assertEqual((method, params["threadId"], len(params["input"])), ("turn/start", THREAD, 1))
        path = Path(job["voice_path"])
        self.assertEqual(path.read_bytes(), b"audio")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(path.is_relative_to(Path(self.temp.name).resolve()))
        self.assertIn(str(path), params["input"][0]["text"])
        self.assertIn("Transcribe this audio", params["input"][0]["text"])
        self.assertIn("Begin your first reply with exactly: You asked: <transcribed text>.", params["input"][0]["text"])
        self.assertEqual(Store(self.store.path).data["inbox"][0]["voice_path"], str(path))
        self.assertEqual(self.store.data["turn_recipients"]["Worker"]["new"], [42])
        self.assertEqual(self.store.data["outbox"], [])

    async def test_voice_steers_active_turn_with_audio_path(self):
        self.active = True
        job = self.job()
        await self.bridge.handle(job)
        method, params = self.client.request.call_args.args
        self.assertEqual(method, "turn/steer")
        self.assertEqual(params["expectedTurnId"], "running")
        self.assertIn(job["voice_path"], params["input"][0]["text"])
        self.assertIn("You asked: <transcribed text>.", params["input"][0]["text"])
        self.assertNotIn("turn/interrupt", self.submitted_methods())

    async def test_unknown_sender_is_rejected_before_downloading_or_saving(self):
        self.assertIsNone(self.job(user_id=99))
        self.bridge.telegram.download_voice.assert_not_called()
        self.assertFalse((Path(self.temp.name) / ".local").exists())

    async def test_voice_without_sleeve_warns_without_downloading(self):
        self.store.data["sleeves"].clear()
        await self.bridge.handle(self.job())
        self.assertIn("no sleeve", self.store.data["outbox"][-1]["text"])
        self.bridge.telegram.download_voice.assert_not_called()

    async def test_download_failure_returns_actionable_warning_without_submitting_work(self):
        self.bridge.telegram.download_voice.side_effect = VoiceError("Voice recording could not be downloaded")
        await self.bridge.handle(self.job())
        self.assertIn("Warning:", self.store.data["outbox"][-1]["text"])
        self.assertIn("could not be downloaded", self.store.data["outbox"][-1]["text"])
        self.assertIn("not submitted", self.store.data["outbox"][-1]["text"])
        self.assertEqual(self.submitted_methods(), [])

    async def test_revocation_or_cancellation_after_download_prevents_saving_and_submission(self):
        for revoke in (True, False):
            with self.subTest(revoke=revoke):
                self.bridge.update_external_policy(self.external.id, {"communication_policy": CommunicationPolicy("always_allowed").to_dict()})
                self.store.data["inbox"].clear()
                self.store.data["identities"]["Worker"]["telegram"]["offset"] = 0
                job = self.job()
                async def download(*args):
                    if revoke:
                        self.bridge.update_external_policy(self.external.id, {"communication_policy": CommunicationPolicy("always_blocked").to_dict()})
                    else:
                        job["cancel_requested"] = True
                    return b"audio"
                self.bridge.telegram.download_voice.side_effect = download
                await self.bridge.handle(job)
                self.assertEqual(self.submitted_methods(), [])
                self.assertFalse((Path(self.temp.name) / ".local").exists())

    async def test_admin_voice_requires_typed_commands_without_downloading(self):
        self.store.data["sleeves"]["Worker"] = {"kind": "admin_app"}
        await self.bridge.handle(self.job())
        self.assertIn("typed commands only", self.store.data["outbox"][-1]["text"])
        self.bridge.telegram.download_voice.assert_not_called()
        self.bridge.ensure_sleeve.assert_not_called()

    async def test_turn_started_notification_remembers_voice_recipient_before_rpc_returns(self):
        job = self.job()
        job.update(status="submitting", voice_path="/workspace/voice.ogg")
        client = SimpleNamespace(socket_path="/tmp/codex.sock", ws=object())
        self.bridge.connected["Worker"] = client.ws
        await self.bridge.on_codex(client, {"method": "turn/started", "params": {"threadId": THREAD, "turn": {"id": "early"}}})
        self.assertEqual(self.store.data["turn_recipients"]["Worker"]["early"], [42])

    async def test_cached_voice_file_is_not_submitted_to_replacement_sleeve(self):
        job = self.job()
        job.update(voice_path="/previous/voice.ogg", voice_sleeve={"socket_path": "/tmp/codex.sock", "thread_id": "previous"})
        await self.bridge.handle(job)
        self.assertIn("previous sleeve", self.store.data["outbox"][-1]["text"])
        self.assertEqual(self.submitted_methods(), [])
        self.bridge.telegram.download_voice.assert_not_called()


class VoiceDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.identity = AgentIdentity("Worker", TelegramIdentity(TOKEN, "TestBot", 11))
        self.voice = {"file_id": "voice-id", "duration": 3, "file_size": 4}
        self.metadata = {"file_path": "voice/note.oga", "file_size": 4}
        self.audio = b"OggS"
        self.requests = []
        async def get_file(request):
            self.requests.append(request.path)
            self.assertEqual(await request.json(), {"file_id": "voice-id"})
            return web.json_response({"ok": True, "result": self.metadata})
        async def download(request):
            self.requests.append(request.path)
            return web.Response(body=self.audio)
        app = web.Application()
        app.router.add_post("/getFile", get_file)
        app.router.add_get("/files/{path:.*}", download)
        self.server = TestServer(app)
        await self.server.start_server()
        self.addAsyncCleanup(self.server.close)
        self.session = aiohttp.ClientSession()
        self.addAsyncCleanup(self.session.close)
        self.telegram = TelegramClient()
        self.telegram.http = SimpleNamespace(
            post=lambda url, **kwargs: self.session.post(self.server.make_url("/getFile"), **kwargs),
            get=lambda url, **kwargs: self.session.get(self.server.make_url("/files/" + url.split(TOKEN + "/", 1)[1]), **kwargs))

    async def test_getfile_then_download_uses_provider_file_location(self):
        self.assertEqual(await self.telegram.download_voice(self.identity, self.voice), b"OggS")
        self.assertEqual(self.requests, ["/getFile", "/files/voice/note.oga"])

    async def test_oversized_or_invalid_voice_is_rejected_before_network(self):
        for changes in ({"duration": -1}, {"file_size": 20_000_001}, {"file_id": ""}):
            with self.subTest(changes=changes), self.assertRaises(VoiceError):
                await self.telegram.download_voice(self.identity, self.voice | changes)
        self.assertEqual(self.requests, [])

    async def test_provider_path_cannot_redirect_download_to_arbitrary_file_or_host(self):
        for path in ("../secret", "/tmp/private", "https://example.com/voice", "voice/%2e%2e/private"):
            self.metadata["file_path"] = path
            with self.subTest(path=path), self.assertRaises(VoiceError):
                await self.telegram.download_voice(self.identity, self.voice)
        self.assertTrue(all(path == "/getFile" for path in self.requests))

    async def test_download_limit_is_enforced_on_actual_bytes_not_just_metadata(self):
        self.audio = b"OggS" * 3
        with patch("agent_home.telegram.MAX_VOICE_BYTES", 8), self.assertRaises(VoiceError):
            await self.telegram.download_voice(self.identity, self.voice)


class VoiceStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_audio_is_private_and_ignored_by_git(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        path = Path(save_voice(self.root, "receipt", b"OggS", "audio/ogg"))
        self.assertEqual(path.read_bytes(), b"OggS")
        self.assertEqual(path.suffix, ".ogg")
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(subprocess.run(["git", "-C", str(self.root), "check-ignore", "-q", str(path)]).returncode, 0)

    def test_symlinked_storage_is_rejected_and_does_not_write_outside_workspace(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / ".local").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            save_voice(self.root, "receipt", b"OggS")
        self.assertEqual(list(outside.iterdir()), [])

    def test_existing_audio_is_never_overwritten_and_mime_only_selects_known_extensions(self):
        path = Path(save_voice(self.root, "receipt", b"original", "audio/mpeg"))
        with self.assertRaises(FileExistsError):
            save_voice(self.root, "receipt", b"replacement", "audio/mpeg")
        self.assertEqual(path.read_bytes(), b"original")
        self.assertEqual(path.suffix, ".mp3")
        self.assertEqual(Path(save_voice(self.root, "another", b"audio", "../../escape")).suffix, ".ogg")


if __name__ == "__main__":
    unittest.main()
