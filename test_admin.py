import asyncio
import contextlib
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from agent_home.models import AdminAppSleeve, AgentIdentity, CommunicationPolicy, ExternalIdentity, TelegramIdentity
from agent_home.service import Bridge
from agent_home.storage import Store
from agent_home.web import create_app

TOKEN = "123456789:" + "t" * 35
THREAD = "11111111-1111-4111-8111-111111111111"


class AdminFixtures(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "home.sqlite3"
        self.store = Store(self.database)
        for name, bot_id in (("Admin", 11), ("Worker team", 22)):
            self.store.add_identity(AgentIdentity(name, TelegramIdentity(TOKEN, name + "Bot", bot_id, chats={"42": 42, "99": 99})))
        self.store.data["sleeves"]["Worker team"] = {"kind": "codex", "thread_id": THREAD, "socket_path": "/tmp/test.sock", "last_turn_id": None}
        self.owner = ExternalIdentity("telegram", "42", communication_policy=CommunicationPolicy("always_allowed"))
        self.store.save_external_identity(self.owner)
        self.store.save_external_identity(ExternalIdentity("telegram", "99", communication_policy=CommunicationPolicy(
            "specific_agents", frozenset({self.store.identity("Admin").id}))))
        self.bridge = Bridge(self.store)
        self.bridge.client = AsyncMock(side_effect=AssertionError("Unexpected Codex access"))
        await self.bridge.assign_sleeve("Admin", kind="admin_app")
        self.sequence = 0

    def ingest(self, name, text, user_id=42):
        self.sequence += 1
        self.bridge.ingest(name, {"update_id": self.sequence, "message": {"text": text,
            "from": {"id": user_id}, "chat": {"id": user_id, "type": "private"}}})
        return self.store.data["inbox"][-1]

    async def command(self, text, user_id=42):
        job = self.ingest("Admin", text, user_id)
        await self.bridge.handle(job)
        return self.store.data["outbox"][-1]["text"]

    def runtime(self, turns=None, source="vscode"):
        client = AsyncMock()
        client.request.side_effect = [{"thread": {"source": source}}, {"data": turns or []}, {}]
        self.bridge.client = AsyncMock(return_value=client)
        return client

    def loaded_roots(self, count=1):
        roots = [{"id": f"22222222-2222-4222-8222-{index:012d}", "name": f"Project {index}",
            "cwd": "/projects/example", "source": "cli", "status": {"type": "idle"}} for index in range(count)]
        client = AsyncMock()
        client.socket_path = "/tmp/test.sock"
        async def request(method, params):
            if method == "thread/loaded/list":
                return {"data": [thread["id"] for thread in roots], "nextCursor": None}
            thread = next((thread for thread in roots if thread["id"] == params.get("threadId")), None)
            if method in {"thread/read", "thread/resume"}:
                return {"thread": thread}
            if method == "thread/turns/list":
                return {"data": [{"id": "earlier-turn", "status": "completed"}]}
            raise AssertionError("Unexpected RPC: " + method)
        client.request.side_effect = request
        self.bridge.client = AsyncMock(return_value=client)
        self.bridge.telegram = AsyncMock(return_value={})
        return client, roots

    def choice(self):
        entry = next(entry for entry in reversed(self.store.data["outbox"]) if "reply_markup" in entry)
        return entry["reply_markup"]["inline_keyboard"][0][0]["callback_data"]

    def callback_job(self, data, user_id=42):
        self.sequence += 1
        before = len(self.store.data["inbox"])
        self.bridge.ingest("Admin", {"update_id": self.sequence, "callback_query": {
            "id": str(self.sequence), "data": data, "from": {"id": user_id},
            "message": {"chat": {"id": user_id, "type": "private"}}}})
        self.assertEqual(len(self.store.data["inbox"]), before + 1)
        return self.store.data["inbox"][-1]

class AdminSleeveTests(AdminFixtures):
    async def test_create_then_button_binds_exact_existing_root_and_survives_restart(self):
        client, roots = self.loaded_roots()
        response = await self.command("/create@AdminBot New research partner")
        self.assertIn("Created New research partner", response)
        self.assertIn("Telegram is not connected", response)
        self.assertIn("/projects/example", response)
        identity = Store(self.database).identity("New research partner")
        self.assertIsNone(identity.sleeve)
        self.assertIsNone(identity.telegram)
        data = self.choice()
        self.assertLessEqual(len(data.encode()), 64)
        await self.bridge.handle(self.callback_job(data))
        reopened = Store(self.database).identity(identity.name)
        self.assertEqual(reopened.id, identity.id)
        self.assertEqual(reopened.sleeve.thread_id, roots[0]["id"])
        self.assertEqual(reopened.sleeve.socket_path, client.socket_path)
        self.assertEqual(reopened.sleeve.last_turn_id, "earlier-turn")
        self.assertFalse(any(call.args[0] in {"thread/start", "turn/start"} for call in client.request.await_args_list))
        await self.bridge.handle(self.callback_job(data))
        self.assertIn("expired", self.store.data["outbox"][-1]["text"])

    async def test_create_and_bind_require_always_allowed_and_preserve_existing_identity(self):
        before = set(self.store.identities())
        self.assertIn("Always allowed", await self.command("/create Forbidden", user_id=99))
        self.assertIn("Always allowed", await self.command("/bind Worker team", user_id=99))
        self.assertEqual(set(self.store.identities()), before)
        self.bridge.client.assert_not_called()
        self.assertIn("already exists", await self.command("/create Worker team"))
        self.assertIn("own chat", await self.command("/bind Admin"))
        self.bridge.client.assert_not_called()

    async def test_new_identity_is_retained_and_reported_if_codex_is_unreachable(self):
        self.bridge.client = AsyncMock(side_effect=ConnectionError("Codex is offline"))
        response = await self.command("/create Offline project")
        self.assertIn("Created Offline project", response)
        self.assertIn("Codex is offline", response)
        self.assertIsNone(Store(self.database).identity("Offline project").sleeve)

    async def test_picker_filters_subagents_unknown_sources_and_owned_sleeves(self):
        client, roots = self.loaded_roots(4)
        roots[1]["source"] = {"subAgent": "review"}
        roots[2]["source"] = "unknown"
        roots[3]["id"] = THREAD
        response = await self.command("/create Filtered project")
        self.assertIn("Project 0", response)
        for title in ("Project 1", "Project 2", "Project 3"):
            self.assertNotIn(title, response)
        self.assertEqual(len(self.store.data["outbox"][-1]["reply_markup"]["inline_keyboard"]), 1)
        self.assertTrue(any(call.args[0] == "thread/loaded/list" for call in client.request.await_args_list))

    async def test_picker_pages_and_binds_the_selected_root_even_with_identical_folders(self):
        client, roots = self.loaded_roots(12)
        self.store.add_identity(AgentIdentity("Paged project"))
        await self.command("/bind Paged project")
        keyboard = self.store.data["outbox"][-1]["reply_markup"]["inline_keyboard"]
        await self.bridge.handle(self.callback_job(keyboard[-1][0]["callback_data"]))
        self.assertIn("(2/2)", self.store.data["outbox"][-1]["text"])
        self.assertIn("Project 10", self.store.data["outbox"][-1]["text"])
        await self.bridge.handle(self.callback_job(self.choice()))
        self.assertEqual(Store(self.database).identity("Paged project").sleeve.thread_id, roots[10]["id"])
        self.assertEqual(Store(self.database).identity("Paged project").sleeve.socket_path, client.socket_path)

    async def test_picker_is_scoped_to_the_sender_and_expires_on_restart(self):
        self.loaded_roots()
        await self.command("/create Private project")
        data = self.choice()
        self.store.save_external_identity(ExternalIdentity("telegram", "77", communication_policy=CommunicationPolicy("always_allowed")))
        await self.bridge.handle(self.callback_job(data, user_id=77))
        self.assertIn("expired", self.store.data["outbox"][-1]["text"])
        self.assertIsNone(self.store.identity("Private project").sleeve)
        self.bridge.admin.selections.clear()
        await self.bridge.handle(self.callback_job(data))
        self.assertIn("expired", self.store.data["outbox"][-1]["text"])
        self.assertIsNone(Store(self.database).identity("Private project").sleeve)

    async def test_stale_binding_and_new_sleeve_owner_are_not_overwritten(self):
        client, roots = self.loaded_roots(2)
        await self.command("/create Changing project")
        data = self.choice()
        await self.bridge.assign_sleeve("Changing project", roots[1]["id"], client.socket_path)
        await self.bridge.handle(self.callback_job(data))
        self.assertIn("sleeve changed", self.store.data["outbox"][-1]["text"])
        self.assertEqual(self.store.identity("Changing project").sleeve.thread_id, roots[1]["id"])
        await self.command("/create Other project")
        data = self.choice()
        self.store.add_identity(AgentIdentity("New owner"))
        await self.bridge.assign_sleeve("New owner", roots[0]["id"], client.socket_path)
        await self.bridge.handle(self.callback_job(data))
        self.assertIn("already belongs", self.store.data["outbox"][-1]["text"])
        self.assertIsNone(self.store.identity("Other project").sleeve)

    async def test_assignment_rechecks_management_policy_after_codex_rpc(self):
        client, _ = self.loaded_roots()
        await self.command("/create Revoked project")
        data = self.choice()
        original = client.request.side_effect
        async def revoked(method, params):
            result = await original(method, params)
            if method == "thread/turns/list":
                self.bridge.update_external_policy(self.owner.id, {"communication_policy": {
                    "mode": "specific_agents", "agent_ids": [self.store.identity("Admin").id]}})
            return result
        client.request.side_effect = revoked
        await self.bridge.handle(self.callback_job(data))
        self.assertIn("Always allowed", self.store.data["outbox"][-1]["text"])
        self.assertIsNone(Store(self.database).identity("Revoked project").sleeve)

    async def test_creation_rechecks_access_after_waiting_for_configuration_lock(self):
        await self.bridge.configuration_lock.acquire()
        task = asyncio.create_task(self.command("/create Blocked while waiting"))
        try:
            await asyncio.sleep(0)
            self.bridge.update_external_policy(self.owner.id, {"communication_policy": {
                "mode": "specific_agents", "agent_ids": [self.store.identity("Admin").id]}})
        finally:
            self.bridge.configuration_lock.release()
        self.assertIn("Always allowed", await task)
        self.assertNotIn("Blocked while waiting", Store(self.database).identities())

    async def test_expired_or_changed_bot_selections_cannot_assign(self):
        self.loaded_roots()
        await self.command("/create Expired project")
        data = self.choice()
        next(iter(self.bridge.admin.selections.values()))["expires"] = 0
        await self.bridge.handle(self.callback_job(data))
        self.assertIsNone(self.store.identity("Expired project").sleeve)
        await self.command("/bind Expired project")
        data = self.choice()
        self.store.data["identities"]["Admin"]["telegram"]["bot_id"] = 33
        self.store.save()
        await self.bridge.handle(self.callback_job(data))
        self.assertIn("expired", self.store.data["outbox"][-1]["text"])
        self.assertIsNone(Store(self.database).identity("Expired project").sleeve)

    async def test_binding_revalidates_root_and_refuses_to_replace_an_active_sleeve(self):
        client, roots = self.loaded_roots()
        await self.command("/create Changed source")
        data = self.choice()
        roots[0]["source"] = {"subAgent": "review"}
        await self.bridge.handle(self.callback_job(data))
        self.assertIn("Only root agents", self.store.data["outbox"][-1]["text"])
        self.assertIsNone(self.store.identity("Changed source").sleeve)
        roots[0]["source"] = "cli"
        roots.append({**roots[0], "id": THREAD, "status": {"type": "active"}})
        await self.command("/bind Worker team")
        await self.bridge.handle(self.callback_job(self.choice()))
        self.assertIn("Stop or finish", self.store.data["outbox"][-1]["text"])
        self.assertEqual(Store(self.database).identity("Worker team").sleeve.thread_id, THREAD)

    async def test_callback_notification_failure_does_not_replay_a_successful_assignment(self):
        _, roots = self.loaded_roots()
        await self.command("/create Quiet notification")
        self.bridge.telegram = AsyncMock(side_effect=ConnectionError("Telegram unavailable"))
        await self.bridge.handle(self.callback_job(self.choice()))
        self.assertEqual(Store(self.database).identity("Quiet notification").sleeve.thread_id, roots[0]["id"])
        self.assertIn("sleeve assigned", self.store.data["outbox"][-1]["text"])

    async def test_admin_sleeve_round_trips_and_database_enforces_singleton(self):
        self.assertIsInstance(Store(self.database).identity("Admin").sleeve, AdminAppSleeve)
        self.store.add_identity(AgentIdentity("Other"))
        self.store.data["sleeves"]["Other"] = {"kind": "admin_app"}
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.save()
        self.assertIsNone(Store(self.database).identity("Other").sleeve)
        with self.store.connect() as connection:
            row = connection.execute("SELECT * FROM sleeves WHERE kind = 'admin_app'").fetchone()
            self.assertIsNone(row["thread_id"])
            self.assertIsNone(row["socket_path"])

    async def test_concurrent_assignment_allows_only_one_owner_without_codex(self):
        await self.bridge.assign_sleeve("Admin")
        self.store.add_identity(AgentIdentity("Other"))
        results = await asyncio.gather(*(self.bridge.assign_sleeve(name, kind="admin_app")
            for name in ("Admin", "Other")), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, ValueError) for result in results), 1)
        self.assertEqual(sum(sleeve.get("kind") == "admin_app" for sleeve in Store(self.database).data["sleeves"].values()), 1)
        self.bridge.client.assert_not_called()

    async def test_api_assigns_and_detaches_admin_without_codex_fields(self):
        await self.bridge.assign_sleeve("Admin")
        app = create_app(self.store)
        app.on_startup.clear()
        app.on_cleanup.clear()
        async with TestClient(TestServer(app)) as client:
            response = await client.post("/api/identities/Admin/sleeve", json={"kind": "admin_app"})
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.json(), {"sleeve": {"kind": "admin_app"}})
            for invalid in ({"kind": "admin_app", "thread_id": THREAD}, {"kind": "admin_app", "socket_path": "/tmp/unused"}):
                self.assertEqual((await client.post("/api/identities/Admin/sleeve", json=invalid)).status, 400)
            self.store.add_identity(AgentIdentity("Other"))
            self.assertEqual((await client.post("/api/identities/Other/sleeve", json={"kind": "admin_app"})).status, 400)
            response = await client.get("/api/state")
            admin = next(item for item in (await response.json())["identities"] if item["name"] == "Admin")
            self.assertEqual(admin["sleeve"], {"kind": "admin_app"})
            self.assertEqual(admin["runtime"]["status"], "ready")
            self.assertEqual((await client.post("/api/identities/Admin/sleeve", json={"thread_id": None})).status, 200)
            self.assertIsNone(Store(self.database).identity("Admin").sleeve)

    async def test_active_codex_sleeve_cannot_be_replaced_by_admin(self):
        await self.bridge.assign_sleeve("Admin")
        client = AsyncMock()
        client.request.return_value = {"thread": {"status": {"type": "active"}}}
        self.bridge.client = AsyncMock(return_value=client)
        with self.assertRaisesRegex(ValueError, "Stop or finish"):
            await self.bridge.assign_sleeve("Worker team", kind="admin_app")
        self.assertEqual(Store(self.database).identity("Worker team").sleeve.kind, "codex")

    async def test_admin_can_be_replaced_by_root_codex_and_reassigned_elsewhere(self):
        new_thread = "22222222-2222-4222-8222-222222222222"
        client = AsyncMock()
        client.request.side_effect = [{"thread": {"id": new_thread, "source": "vscode", "cwd": "/repo", "status": {"type": "idle"}}}, {}, {"data": []}]
        self.bridge.client = AsyncMock(return_value=client)
        await self.bridge.assign_sleeve("Admin", new_thread, "/tmp/other.sock")
        self.assertEqual(Store(self.database).identity("Admin").sleeve.thread_id, new_thread)
        self.store.add_identity(AgentIdentity("Other"))
        self.bridge.client.reset_mock()
        await self.bridge.assign_sleeve("Other", kind="admin_app")
        self.bridge.client.assert_not_called()
        self.assertIsInstance(Store(self.database).identity("Other").sleeve, AdminAppSleeve)

    async def test_list_reports_status_and_sleeve_assignment_without_contacting_codex(self):
        self.bridge.sleeve_status["Worker team"] = {"status": "active", "checked_at": time.time() - 5}
        self.store.add_identity(AgentIdentity("Unassigned"))
        response = await self.command("/list@TestBot")
        self.assertIn("Admin — Ready | Sleeve: AdminAppSleeve", response)
        self.assertIn("Worker team — Working | Sleeve: Codex (checked 5s ago)", response)
        self.assertIn("Unassigned — Needs sleeve | Sleeve: None", response)
        self.bridge.mark_unavailable("Worker team", ConnectionError("socket is unavailable"))
        self.assertIn("Worker team — Unavailable", await self.command("/list"))
        self.bridge.sleeve_status.pop("Worker team")
        self.assertIn("Worker team — Not checked | Sleeve: Codex (checked not observed)", await self.command("/list"))
        self.bridge.client.assert_not_called()

    async def test_list_and_target_commands_obey_external_identity_scope(self):
        response = await self.command("/list", user_id=99)
        self.assertIn("Admin", response)
        self.assertNotIn("Worker team", response)
        response = await self.command("/kill Worker team", user_id=99)
        self.assertIn("access denied", response)
        self.bridge.client.assert_not_called()
        count = len(self.store.data["inbox"])
        self.ingest("Admin", "/list", user_id=77)
        self.assertEqual(len(self.store.data["inbox"]), count)

    async def test_removed_admin_commands_return_list_help(self):
        for command in ("/health", "/health Worker team", "/healt", "/agents", "/status", "/list Worker team"):
            response = await self.command(command)
            self.assertIn("Fixed commands only", response)
            self.assertIn("/list", response)
            self.assertNotIn("/health", response)
            self.assertNotIn("/agents", response)
        self.bridge.client.assert_not_called()

    async def test_only_fixed_commands_are_accepted(self):
        for command in ("do something", "/goal finish the task", "/exec rm -rf anything", "/kill", "/kill worker team"):
            response = await self.command(command)
            self.assertTrue("Fixed commands only" in response or "access denied" in response)
        self.assertIn("/list", await self.command("/help@TestBot"))
        self.bridge.client.assert_not_called()

    async def test_kill_cancels_queue_and_interrupts_exact_root_turn_without_detaching(self):
        original = self.store.identity("Worker team")
        queued = self.ingest("Worker team", "pending instruction")
        submitting = {**queued, "id": "22:inflight", "status": "submitting"}
        self.store.data["inbox"].append(submitting)
        self.bridge.approvals["old"] = {"name": "Worker team"}
        client = self.runtime([{"id": "active-turn", "status": "inProgress"}])
        response = await self.command("/kill Worker team")
        self.assertIn("cancelled 1 queued", response)
        self.assertIn("Interrupt requested", response)
        self.assertIn("submission(s) were in flight", response)
        self.assertEqual(queued["status"], "done")
        self.assertTrue(submitting["cancel_requested"])
        reopened = Store(self.database).identity("Worker team")
        self.assertEqual(reopened.id, original.id)
        self.assertEqual(reopened.sleeve, original.sleeve)
        self.assertNotIn("old", self.bridge.approvals)
        self.assertEqual(client.request.call_args.args, ("turn/interrupt", {"threadId": THREAD, "turnId": "active-turn"}))

    async def test_kill_reports_idle_unreachable_and_non_codex_targets(self):
        self.runtime()
        self.assertIn("No active turn", await self.command("/kill Worker team"))
        self.bridge.client = AsyncMock(side_effect=TimeoutError())
        self.assertIn("Runtime stop not confirmed", await self.command("/kill Worker team"))
        self.assertIn("5 seconds", self.store.data["outbox"][-1]["text"])
        self.assertIn("requires an assigned Codex sleeve", await self.command("/kill Admin"))

    async def test_kill_has_a_deadline_even_when_configuration_is_locked(self):
        original_timeout = asyncio.timeout
        await self.bridge.configuration_lock.acquire()
        try:
            with patch("agent_home.admin.asyncio.timeout", side_effect=lambda seconds: original_timeout(0.01)):
                response = await self.command("/kill Worker team")
            self.assertIn("timed out", response)
            self.assertIn("not confirmed", response)
            self.bridge.client.assert_not_called()
        finally:
            self.bridge.configuration_lock.release()

    async def test_kill_rejects_subagents_and_rechecks_access_before_interrupt(self):
        client = self.runtime(source={"subAgent": "review"})
        self.assertIn("Only root-agent", await self.command("/kill Worker team"))
        self.assertEqual(client.request.await_count, 1)
        client = AsyncMock()
        async def request(method, params):
            if method == "thread/read":
                return {"thread": {"source": "vscode"}}
            self.bridge.update_external_policy(self.owner.id, {"communication_policy": {"mode": "always_blocked", "agent_ids": []}})
            return {"data": [{"id": "active", "status": "inProgress"}]}
        client.request.side_effect = request
        self.bridge.client = AsyncMock(return_value=client)
        job = self.ingest("Admin", "/kill Worker team")
        await self.bridge.handle(job)
        self.assertNotIn("turn/interrupt", [call.args[0] for call in client.request.await_args_list])

    async def test_admin_worker_and_delivery_progress_while_normal_paths_are_blocked(self):
        blocked, entered, sent = asyncio.Event(), asyncio.Event(), asyncio.Event()
        client = AsyncMock()
        async def request(method, params):
            entered.set()
            await blocked.wait()
        client.request.side_effect = request
        self.bridge.ensure_sleeve = AsyncMock(return_value=client)
        self.ingest("Worker team", "slow work")
        self.bridge.enqueue("Worker team", "slow delivery")
        async def telegram(identity, method, **params):
            if identity.name == "Worker team":
                await blocked.wait()
            else:
                sent.set()
        self.bridge.telegram = telegram
        tasks = [asyncio.create_task(self.bridge.work()), asyncio.create_task(self.bridge.work(admin=True)),
                 asyncio.create_task(self.bridge.deliver()), asyncio.create_task(self.bridge.deliver(admin=True))]
        try:
            async with asyncio.timeout(2):
                await entered.wait()
                self.ingest("Admin", "/list")
                await sent.wait()
            self.assertFalse(tasks[0].done())
            self.assertTrue(any(entry["identity"] == "Worker team" for entry in self.store.data["outbox"]))
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_cancel_requested_during_submission_interrupts_returned_turn(self):
        job = self.ingest("Worker team", "work")
        client = AsyncMock()
        async def request(method, params):
            if method == "thread/read":
                return {"thread": {"status": {"type": "idle"}}}
            if method == "thread/turns/list":
                return {"data": []}
            if method == "turn/start":
                job["cancel_requested"] = True
                return {"turn": {"id": "late-turn"}}
            return {}
        client.request.side_effect = request
        self.bridge.ensure_sleeve = AsyncMock(return_value=client)
        await self.bridge.handle(job)
        self.assertEqual(client.request.call_args.args, ("turn/interrupt", {"threadId": THREAD, "turnId": "late-turn"}))

    async def test_schema_four_upgrade_preserves_data_and_adds_admin_constraint(self):
        self.store.data["sleeves"].pop("Admin")
        self.store.save()
        identities = self.store.identities()
        external = self.store.external_identities()
        with self.store.connect() as connection:
            connection.execute("ALTER TABLE sleeves RENAME TO sleeves_new")
            connection.execute("CREATE TABLE sleeves (identity_name TEXT PRIMARY KEY REFERENCES identities(name), kind TEXT NOT NULL CHECK(kind='codex'), thread_id TEXT NOT NULL, socket_path TEXT NOT NULL, thread_name TEXT, cwd TEXT, last_turn_id TEXT, UNIQUE(socket_path,thread_id))")
            connection.execute("INSERT INTO sleeves SELECT * FROM sleeves_new")
            connection.execute("DROP TABLE sleeves_new")
            connection.execute("PRAGMA user_version = 4")
        upgraded = Store(self.database)
        self.assertEqual(upgraded.identities(), identities)
        self.assertEqual(upgraded.external_identities(), external)
        backup = Path(self.temp.name) / "home.before-admin-sleeve.sqlite3"
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        with contextlib.closing(sqlite3.connect(backup)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)
        await Bridge(upgraded).assign_sleeve("Admin", kind="admin_app")
        self.assertIsInstance(Store(self.database).identity("Admin").sleeve, AdminAppSleeve)


if __name__ == "__main__":
    unittest.main()
