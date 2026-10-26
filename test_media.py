import asyncio
import dataclasses
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from agent_home.media import FORMATS, MediaError, media_references, open_media
from agent_home.models import AgentIdentity, AgentSleeve, CommunicationPolicy, ExternalIdentity, TelegramIdentity
from agent_home.service import Bridge
from agent_home.storage import Store
from agent_home.telegram import TelegramAPIError, TelegramClient


PNG = b"\x89PNG\r\n\x1a\n" + b"test image bytes"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"test video bytes"
TOKEN = "123456789:" + "t" * 35


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_markdown_embeds_support_inline_multiple_and_local_path_variants_and_deduplicate(self):
        text = (f"Here is ![Preview]({self.root}/preview.png) and ![Again](./preview.png).\n"
                '![Final](<renders/final cut.mp4> "Watch") ![Other](renders/other%20clip.MP4 "Watch")\n'
                "![Animation](renders/animation.gif) ![WebM](renders/plain.webm)\n"
                f"![Absolute]({self.root}/absolute.jpg)\n"
                f"![File]({self.root.as_uri()}/encoded%20clip.mp4)\n"
                "![Parentheses](renders/clip(1).mp4) ![Escaped](renders/clip\\(1\\).mp4)")
        media = media_references(text, self.root)
        self.assertEqual([Path(item["path"]).relative_to(self.root).as_posix() for item in media],
            ["preview.png", "renders/final cut.mp4", "renders/other clip.MP4",
             "renders/animation.gif", "renders/plain.webm", "absolute.jpg", "encoded clip.mp4",
             "renders/clip(1).mp4"])
        self.assertTrue(all(item["root"] == str(self.root) for item in media))

    def test_urls_nonmedia_and_missing_workspace_do_not_become_uploads(self):
        text = ('![Remote](https://example.com/photo.png) ![Host](file://remote-host/file.png)\n'
                '![Invalid](http://[invalid.png) ![Text](notes.txt)')
        self.assertEqual(media_references(text, self.root), [])
        self.assertEqual(media_references("![Output](output.png)", None), [])

    def test_ordinary_paths_nonmedia_links_legacy_markers_and_code_examples_stay_text(self):
        text = (f"Old file: {self.root}/preview.png\n[Notes](notes.txt) [Website](https://example.com)\n"
                '`preview.png` "clip.mp4" preview.png\n'
                '`[[attach:preview.png]]`\n`![Preview](preview.png)`\n'
                '``![Video](clip.mp4)``\n\\![Escaped](preview.png)\n\\[Escaped](clip.mp4)\n'
                '`[Download](clip.mp4)`\n'
                '    ![Indented](clip.mp4)\n'
                '```text\n![Preview](preview.png)\n```\n'
                '~~~~\n![Video](clip.mp4)\n~~~~\n'
                '```unfinished\n![Preview](preview.png)')
        self.assertEqual(media_references(text, self.root), [])

    def test_attach_markers_support_multiple_local_images_and_videos_outside_code(self):
        text = (f"Working: [[attach:{self.root}/support-desktop.png]]\n"
                "[[attach:review/support-phone.png]]\n"
                "`[[attach:ignored.png]]`\n"
                "```text\n[[attach:also-ignored.mp4]]\n```\n"
                f"![Duplicate]({self.root}/support-desktop.png)")
        self.assertEqual([Path(media["path"]).relative_to(self.root).as_posix()
                          for media in media_references(text, self.root)],
                         ["support-desktop.png", "review/support-phone.png"])

    def test_playback_url_and_local_download_link_queue_one_webm(self):
        path = self.root / "projects/hai-gintama-ensemble/output/shorts.webm"
        text = ("[▶ 直接播放银魂视频（带日英字幕）]"
                "(http://127.0.0.1:8787/projects/hai-gintama-ensemble/output/shorts.webm) · "
                f"[下载视频]({path}) ![重复](projects/hai-gintama-ensemble/output/shorts.webm)")
        self.assertEqual(media_references(text, self.root), [{"path": str(path), "root": str(self.root)}])

    def test_ordinary_media_links_support_images_titles_and_spaced_paths(self):
        text = '[Preview](preview.png) [Video](<final cut.mp4> "Download") [WebM](other%20clip.webm)'
        self.assertEqual([Path(media["path"]).name for media in media_references(text, self.root)],
                         ["preview.png", "final cut.mp4", "other clip.webm"])

    def test_valid_local_media_is_opened_and_closed(self):
        path = self.root / "image.png"
        path.write_bytes(PNG)
        with open_media({"path": str(path), "root": str(self.root)}) as (stream, kind, mime):
            self.assertEqual((stream.read(), kind, mime), (PNG, "photo", "image/png"))
        self.assertTrue(stream.closed)

    def test_external_files_can_be_allowed_without_following_symlinks_or_skipping_content_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            outside = Path(directory).resolve()
            path = outside / 'image.png'
            path.write_bytes(PNG)
            media = {'path':str(path), 'root':str(self.root)}
            with self.assertRaisesRegex(MediaError, 'does not allow attachments outside'):
                with open_media(media):
                    self.fail('External attachment opened without permission')
            with open_media(media, allow_external=True) as (stream, _, _):
                self.assertEqual(stream.read(), PNG)
            (outside / 'link.png').symlink_to(path)
            (outside / 'linked-folder').symlink_to(outside, target_is_directory=True)
            os.mkfifo(outside / 'pipe.png')
            for unsafe in (outside/'link.png', outside/'linked-folder/image.png', outside/'pipe.png'):
                with self.subTest(path=unsafe), self.assertRaises(MediaError):
                    with open_media({'path':str(unsafe), 'root':str(self.root)}, allow_external=True):
                        self.fail('Unsafe external attachment opened')
            path.write_text('Not an image')
            with self.assertRaisesRegex(MediaError, 'contents'):
                with open_media(media, allow_external=True):
                    self.fail('Invalid external content opened')

    def test_paths_missing_files_symlinks_and_special_files_are_rejected(self):
        outside = self.root.parent / "outside.png"
        (self.root / "real.png").write_bytes(PNG)
        (self.root / "link.png").symlink_to(self.root / "real.png")
        (self.root / "linked-dir").symlink_to(self.root, target_is_directory=True)
        (self.root / "directory.png").mkdir()
        os.mkfifo(self.root / "pipe.png")
        for path in (outside, self.root / "../outside.png", self.root / "missing.png",
                     self.root / "link.png", self.root / "linked-dir/real.png",
                     self.root / "directory.png", self.root / "pipe.png"):
            with self.subTest(path=path), self.assertRaises(MediaError):
                with open_media({"path": str(path), "root": str(self.root)}):
                    self.fail("Unsafe media opened")

    def test_invalid_content_empty_and_oversized_files_are_rejected(self):
        path = self.root / "image.png"
        for content in (b"", b"secret text renamed to an image"):
            path.write_bytes(content)
            with self.assertRaises(MediaError):
                with open_media({"path": str(path), "root": str(self.root)}):
                    self.fail("Invalid content opened")
        for suffix, size in ((".png", 10), (".mp4", 50)):
            path = self.root / ("large" + suffix)
            with path.open("wb") as stream:
                stream.truncate(size * 1024 * 1024 + 1)
            with self.assertRaisesRegex(MediaError, "upload limit"):
                with open_media({"path": str(path), "root": str(self.root)}):
                    self.fail("Oversized media opened")


class MediaRelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.database = self.root / "state.sqlite3"
        self.store = Store(self.database)
        self.identity = AgentIdentity("Creator", TelegramIdentity(TOKEN, "TestBot", 11, chats={"42": 42}),
            sleeve=AgentSleeve("11111111-1111-4111-8111-111111111111", "/tmp/codex.sock", cwd=str(self.root)))
        self.store.add_identity(self.identity)
        self.store.data["sleeves"][self.identity.name] = dataclasses.asdict(self.identity.sleeve)
        self.external = ExternalIdentity("telegram", "42", communication_policy=CommunicationPolicy("always_allowed"))
        self.store.save_external_identity(self.external)
        self.bridge = Bridge(self.store)
        self.bridge.telegram = AsyncMock()

    def queue(self):
        (self.root / "preview.png").write_bytes(PNG)
        (self.root / "clip.mp4").write_bytes(MP4)
        self.bridge.add_turn_recipient("Creator", "turn", 42)
        self.bridge.relay_item("Creator", "turn", {"id": "answer", "text": "Ready: ![Preview](preview.png) ![Video](clip.mp4)"})
        return self.store.data["outbox"]

    async def test_media_jobs_preserve_text_routes_and_survive_restart_without_replay(self):
        entries = self.queue()
        self.assertEqual(len(entries), 3)
        self.assertTrue(all(entry["chat_id"] == 42 for entry in entries))
        self.assertIn("Ready:", entries[0]["text"])
        restarted = Bridge(Store(self.database))
        self.assertEqual(restarted.store.data["outbox"], entries)
        restarted.relay_item("Creator", "turn", {"id": "answer", "text": "preview.png clip.mp4"})
        self.assertEqual(len(restarted.store.data["outbox"]), 3)
        await self.bridge.deliver_entry(self.identity, entries[1])
        self.bridge.telegram.send_media.assert_awaited_once_with(self.identity, entries[1]["media"], chat_id=42)

    async def test_revoking_access_removes_media_and_text_deliveries(self):
        self.queue()
        self.bridge.update_external_policy(self.external.id, {"communication_policy": CommunicationPolicy("always_blocked").to_dict()})
        self.assertEqual(self.store.data["outbox"], [])
        task = asyncio.create_task(self.bridge.deliver())
        await asyncio.sleep(.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.bridge.telegram.send_media.assert_not_called()
        self.bridge.telegram.assert_not_called()

    async def test_reassignment_does_not_change_queued_media_workspace(self):
        entry = self.queue()[1]
        self.store.data["sleeves"]["Creator"]["cwd"] = "/another/workspace"
        self.store.save()
        restarted = Bridge(Store(self.database))
        self.assertEqual(restarted.store.data["outbox"][1]["media"]["root"], str(self.root))
        with open_media(entry["media"]) as (stream, _, _):
            self.assertEqual(stream.read(), PNG)

    async def test_missing_or_permanently_rejected_media_becomes_durable_text_fallback(self):
        for error in (MediaError("File is missing"), TelegramAPIError("sendDocument", {"error_code": 400, "description": "Invalid media"})):
            with self.subTest(error=error):
                self.store.data["outbox"].clear()
                self.store.data["seen_items"].clear()
                entry = self.queue()[1]
                self.bridge.telegram.send_media.side_effect = error
                await self.bridge.deliver_entry(self.identity, entry)
                self.assertNotIn("media", entry)
                self.assertIn("Warning: media could not be attached", entry["text"])
                self.assertEqual(Store(self.database).data["outbox"][1], entry)
                self.assertEqual(self.bridge.telegram.call_args.args[1], "sendMessage")

    async def test_oversized_download_link_sends_telegram_warning_with_actual_size_and_limit(self):
        path = self.root / "shorts.webm"
        with path.open("wb") as stream:
            stream.write(b"\x1aE\xdf\xa3")
            stream.truncate(65_289_921)
        self.bridge.relay_item("Creator", "turn", {"id": "oversized", "text": f"[下载视频]({path})"})
        entry = self.store.data["outbox"][-1]
        self.bridge.telegram.send_media.side_effect = TelegramClient().send_media
        await self.bridge.deliver_entry(self.identity, entry)
        self.bridge.telegram.assert_awaited_once()
        self.assertEqual(self.bridge.telegram.call_args.args[1], "sendMessage")
        warning = self.bridge.telegram.call_args.kwargs["text"]
        for detail in ("Warning:", "65.3 MB", "50 MB", "smaller copy", "shorts.webm"):
            self.assertIn(detail, warning)
        self.assertNotIn("media", Store(self.database).data["outbox"][-1])

    async def test_temporary_media_failure_leaves_upload_queued_for_retry(self):
        entry = self.queue()[1]
        error = TelegramAPIError("sendPhoto", {"error_code": 429, "description": "Retry later"})
        self.bridge.telegram.send_media.side_effect = error
        with self.assertRaises(TelegramAPIError):
            await self.bridge.deliver_entry(self.identity, entry)
        self.assertIn("media", Store(self.database).data["outbox"][1])
        self.bridge.telegram.assert_not_called()

    async def test_queued_external_media_uses_current_agent_preference_after_restart_and_reassignment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'download.png'
            path.write_bytes(PNG)
            self.bridge.add_turn_recipient('Creator', 'external-turn', 42)
            self.bridge.relay_item('Creator', 'external-turn', {'id':'external-answer','text':f'![Download]({path})'})
            entry = self.store.data['outbox'][-1]
            async def send(identity, media, **kwargs):
                with open_media(media, allow_external=identity.allow_external_media) as (stream, _, _):
                    self.assertEqual(stream.read(), PNG)
            self.bridge.telegram.send_media.side_effect = send
            await self.bridge.deliver_entry(self.identity, entry)
            self.bridge.telegram.send_media.assert_awaited_once()
            self.store.data['identities']['Creator']['allow_external_media'] = False
            self.store.data['sleeves']['Creator']['cwd'] = '/replacement/workspace'
            self.store.save()
            restarted = Bridge(Store(self.database))
            self.addAsyncCleanup(restarted.telegram.close)
            restarted.telegram = self.bridge.telegram
            self.bridge.telegram.reset_mock()
            queued = restarted.store.data['outbox'][-1]
            await restarted.deliver_entry(self.identity, queued)
            self.assertNotIn('media', queued)
            self.assertIn('does not allow attachments outside', queued['text'])
            self.assertEqual(self.bridge.telegram.call_args.kwargs['chat_id'], 42)
            self.assertEqual(Store(self.database).data['outbox'][-1], queued)

    async def test_enabling_external_media_applies_to_already_queued_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'download.png'
            path.write_bytes(PNG)
            self.store.data['identities']['Creator']['allow_external_media'] = False
            self.store.save()
            old_identity = self.store.identity('Creator')
            self.bridge.relay_item('Creator', 'external-turn', {'id':'external-answer','text':f'[[attach:{path}]]'})
            self.store.data['identities']['Creator']['allow_external_media'] = True
            self.store.save()
            entry = self.store.data['outbox'][-1]
            await self.bridge.deliver_entry(old_identity, entry)
            self.assertTrue(self.bridge.telegram.send_media.await_args.args[0].allow_external_media)



class TelegramUploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.identity = AgentIdentity("Creator", TelegramIdentity(TOKEN, "TestBot", 11))
        self.received = []
        self.results = []

        async def receive(request):
            data = {}
            if request.content_type == "multipart/form-data":
                reader = await request.multipart()
                async for part in reader:
                    data[part.name] = (part.filename, part.headers.get("Content-Type"), bytes(await part.read()))
            else:
                data = await request.json()
            self.received.append((request.path, data))
            return web.json_response(self.results.pop(0) if self.results else {"ok": True, "result": {"message_id": 123}})

        app = web.Application(client_max_size=60 * 1024 * 1024)
        app.router.add_post("/{method}", receive)
        self.server = TestServer(app)
        await self.server.start_server()
        self.addAsyncCleanup(self.server.close)
        self.session = aiohttp.ClientSession()
        self.addAsyncCleanup(self.session.close)
        self.telegram = TelegramClient()
        # Keep the real multipart transport, but route every request to a local fake Telegram server.
        self.telegram.http = SimpleNamespace(post=lambda url, **kwargs:
            self.session.post(self.server.make_url("/" + url.rsplit("/", 1)[-1]), **kwargs))

    async def send(self, filename, content):
        path = self.root / filename
        path.write_bytes(content)
        return await self.telegram.send_media(self.identity, {"path": str(path), "root": str(self.root)}, chat_id=42)

    async def test_real_multipart_uploads_select_photo_video_animation_and_document(self):
        examples = [("image.png", PNG, "photo"), ("clip.mp4", MP4, "video"),
                    ("loop.gif", b"GIF89a" + b"fake gif bytes", "animation"),
                    ("clip.webm", b"\x1aE\xdf\xa3" + b"fake webm bytes", "document")]
        for filename, content, field in examples:
            await self.send(filename, content)
            method, data = self.received[-1]
            self.assertEqual(method, "/send" + field.title())
            self.assertEqual(data["chat_id"][2], b"42")
            self.assertEqual(data[field], (filename, FORMATS[Path(filename).suffix][1], content))

    async def test_external_upload_defaults_to_allowed_and_explicit_restriction_prevents_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'download.png'
            path.write_bytes(PNG)
            media = {'path':str(path), 'root':str(self.root)}
            await self.telegram.send_media(self.identity, media, chat_id=42)
            self.assertEqual(self.received[-1][1]['photo'][2], PNG)
            with self.assertRaisesRegex(MediaError, 'does not allow attachments outside'):
                await self.telegram.send_media(dataclasses.replace(self.identity, allow_external_media=False), media, chat_id=42)
            self.assertEqual(len(self.received), 1)

    async def test_inline_rejection_reopens_file_and_falls_back_to_document(self):
        self.results = [{"ok": False, "error_code": 400, "description": "Invalid dimensions"}]
        await self.send("image.png", PNG)
        self.assertEqual([method for method, _ in self.received], ["/sendPhoto", "/sendDocument"])
        self.assertEqual(self.received[-1][1]["document"][2], PNG)

    async def test_rate_limits_do_not_trigger_document_fallback(self):
        self.results = [{"ok": False, "error_code": 429, "description": "Retry later"}]
        with self.assertRaises(TelegramAPIError):
            await self.send("clip.mp4", MP4)
        self.assertEqual(len(self.received), 1)

    async def test_json_calls_and_provider_error_redaction_are_preserved(self):
        await self.telegram(self.identity, "sendMessage", chat_id=42, text="hello")
        self.assertEqual(self.received[-1], ("/sendMessage", {"chat_id": 42, "text": "hello"}))
        self.results = [{"ok": False, "error_code": 400, "description": "Invalid token " + TOKEN}]
        with self.assertRaises(TelegramAPIError) as error:
            await self.telegram(self.identity, "getMe")
        self.assertNotIn(TOKEN, str(error.exception))


if __name__ == "__main__":
    unittest.main()
