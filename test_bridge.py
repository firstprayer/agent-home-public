import asyncio
import dataclasses
import json
import re
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from agent_home.config import ServiceConfig
from agent_home.models import AgentIdentity, CommunicationPolicy, ExternalIdentity, TelegramIdentity
from agent_home.service import Bridge
from agent_home.codex import CodexClient, is_root_thread
from agent_home.storage import Store
from agent_home.web import BRIDGE_KEY, create_app
from agent_home.utils import safe_error, text_chunks


THREAD = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
TOKEN = "123456789:" + "t" * 35


def legacy_database(store, version, rules=None):
    with store.connect() as connection:
        connection.execute("DROP TABLE external_identity_agents")
        connection.execute("DROP TABLE external_identities")
        connection.execute("ALTER TABLE identities ADD COLUMN allow_rules TEXT NOT NULL DEFAULT '{\"rules\":[]}'")
        for name, policy in (rules or {}).items():
            connection.execute("UPDATE identities SET allow_rules = ? WHERE name = ?", (json.dumps(policy), name))
        connection.execute(f"PRAGMA user_version = {version}")


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "agent-home.sqlite3"
        self.store = Store(self.database)
        self.tg = TelegramIdentity(TOKEN, "TestBot", 11, chats={"42": 42})
        self.store.add_identity(AgentIdentity("Trading", self.tg))
        self.store.data["sleeves"]["Trading"] = {"thread_id": THREAD, "socket_path": "/tmp/test-codex.sock",
            "last_turn_id": "previous", "thread_name": "Trading", "cwd": "/repo/trading"}
        self.bridge = Bridge(self.store)
        self.allow_users("Trading", "42")

    def allow_users(self, name, *users):
        agent_id = self.store.identity(name).id
        for external in self.store.external_identities():
            agents = external.communication_policy.agent_ids - {agent_id}
            if agents:
                self.store.save_external_identity(dataclasses.replace(external,
                    communication_policy=CommunicationPolicy("specific_agents", agents)))
            else:
                self.store.data["external_identities"].pop(external.id)
        for user in users:
            numeric = user.isdigit()
            external = next((item for item in self.store.external_identities()
                if (item.user_id == user if numeric else item.username == "@" + user.lstrip("@").lower())), None)
            agents = (external.communication_policy.agent_ids if external else frozenset()) | {agent_id}
            policy = CommunicationPolicy("specific_agents", agents)
            self.store.save_external_identity(dataclasses.replace(external, communication_policy=policy) if external else
                ExternalIdentity("telegram", user if numeric else None, None if numeric else user, policy))
        self.bridge.recheck_access()
        self.store.save()

    async def asyncTearDown(self):
        self.temp.cleanup()

    def update(self, user_id=42, text="hello", update_id=1, chat_type="private"):
        return {"update_id": update_id, "message": {"text": text, "from": {"id": user_id, "username": "owner"},
            "chat": {"id": user_id, "type": chat_type}}}

    async def test_unauthorized_and_group_messages_never_enter_inbox(self):
        self.bridge.ingest("Trading", self.update(user_id=99))
        self.bridge.ingest("Trading", self.update(chat_type="group", update_id=2))
        self.assertEqual(self.store.data["inbox"], [])
        self.assertEqual(self.store.data["outbox"], [])
        self.bridge.ingest("Trading", self.update(update_id=3))
        self.assertEqual(len(self.store.data["inbox"]), 1)
        self.assertEqual(Store(self.database).data["identities"]["Trading"]["telegram"]["offset"], 4)

    async def test_unlisted_sender_requires_registration(self):
        self.allow_users("Trading")
        self.bridge.ingest("Trading", self.update(text="delete everything"))
        self.assertEqual(self.store.data["inbox"], [])
        pending = self.store.data["pending_users"]["Trading"]["42"]
        self.assertEqual(pending["user_id"], 42)
        self.assertNotIn("text", pending)

    async def test_empty_agent_scope_and_blocked_identity_reject_messages(self):
        external = self.store.match_external_identity({"id": 42})
        for policy in (CommunicationPolicy("specific_agents"), CommunicationPolicy("always_blocked")):
            with self.subTest(policy=policy):
                self.bridge.update_external_policy(external.id, {"communication_policy": policy.to_dict()})
                self.bridge.ingest("Trading", self.update(update_id=self.bridge.identity("Trading").telegram.offset))
                self.assertEqual(self.store.data["inbox"], [])
                self.assertEqual(self.store.data["outbox"], [])

    async def test_username_is_pinned_to_numeric_id_and_survives_rename(self):
        self.allow_users("Trading", "example_operator")
        update = self.update(text="/start")
        update["message"]["from"]["username"] = "Example_Operator"
        self.bridge.ingest("Trading", update)
        self.assertEqual(self.store.match_external_identity({"id": 42}).user_id, "42")
        recycled = self.update(user_id=99, update_id=2)
        recycled["message"]["from"]["username"] = "example_operator"
        self.bridge.ingest("Trading", recycled)
        renamed = self.update(update_id=3)
        renamed["message"]["from"]["username"] = "different_name"
        self.bridge.ingest("Trading", renamed)
        self.assertEqual(len(self.store.data["inbox"]), 2)
        reopened = Store(self.database).external_identities()[0]
        self.assertEqual((reopened.username, reopened.user_id), ("@example_operator", "42"))
        self.assertIsNone(Store(self.database).match_external_identity({"id": 99, "username": "example_operator"}))

    async def test_specific_agent_policies_are_isolated(self):
        self.store.add_identity(AgentIdentity("Other", TelegramIdentity(TOKEN, "OtherBot", 22)))
        self.allow_users("Trading", "42", "99")
        self.allow_users("Other", "77")
        self.bridge.ingest("Trading", self.update(user_id=42))
        self.bridge.ingest("Trading", self.update(user_id=99, update_id=2))
        self.bridge.ingest("Other", self.update(user_id=42))
        self.bridge.ingest("Trading", self.update(user_id=77, update_id=3))
        self.assertEqual([job["identity"] for job in self.store.data["inbox"]], ["Trading", "Trading"])

    async def test_always_allowed_applies_to_current_and_future_agents_but_unknown_senders_are_rejected(self):
        external = self.store.match_external_identity({"id": 42})
        self.bridge.update_external_policy(external.id, {"communication_policy": {"mode": "always_allowed", "agent_ids": []}})
        self.store.add_identity(AgentIdentity("Future", TelegramIdentity(TOKEN, "FutureBot", 22)))
        self.bridge.ingest("Future", self.update())
        self.bridge.ingest("Future", self.update(user_id=99, update_id=2))
        self.assertEqual([job["identity"] for job in self.store.data["inbox"]], ["Future"])
        self.assertEqual(len(self.store.external_identities()), 1)
        self.assertEqual(self.bridge.unregistered_senders()[0]["user_id"], "99")

    async def test_numeric_identity_takes_precedence_over_unbound_username(self):
        external = self.store.match_external_identity({"id": 42})
        self.bridge.update_external_policy(external.id, {"communication_policy": {"mode": "always_blocked", "agent_ids": []}})
        self.bridge.create_external_identity({"provider": "telegram", "user_id": None, "username": "owner",
            "communication_policy": {"mode": "always_allowed", "agent_ids": []}})
        self.bridge.ingest("Trading", self.update())
        self.assertEqual(self.store.data["inbox"], [])
        self.assertEqual(self.bridge.unregistered_senders(), [])

    async def test_registering_pending_sender_does_not_replay_its_rejected_messages(self):
        self.bridge.ingest("Trading", self.update(user_id=99, text="do not replay"))
        external = self.bridge.create_external_identity({"provider": "telegram", "user_id": None, "username": "owner",
            "communication_policy": {"mode": "always_blocked", "agent_ids": []}})
        self.assertEqual(external.user_id, "99")
        self.assertEqual(self.bridge.unregistered_senders(), [])
        self.assertEqual(self.store.data["inbox"], [])
        self.bridge.update_external_policy(external.id, {"communication_policy": {"mode": "always_allowed", "agent_ids": []}})
        self.assertEqual(self.store.data["inbox"], [])
        self.bridge.ingest("Trading", self.update(user_id=99, update_id=2))
        self.assertEqual(len(self.store.data["inbox"]), 1)
        self.assertEqual(Store(self.database).external_identity(external.id).user_id, "99")

    async def test_revocation_drops_queued_input_replies_and_callbacks(self):
        self.bridge.ingest("Trading", self.update())
        self.bridge.enqueue("Trading", "private answer")
        self.assertEqual(len(self.store.data["outbox"]), 1)
        external = self.store.match_external_identity({"id": 42})
        self.bridge.update_external_policy(external.id, {"communication_policy": {"mode": "always_blocked", "agent_ids": []}})
        self.assertEqual(self.store.data["outbox"], [])
        self.assertEqual(self.store.data["inbox"][0]["status"], "done")
        client = AsyncMock()
        self.bridge.approvals["key"] = {"name": "Trading", "thread_id": THREAD,
            "turn_id": "turn", "request_id": 1, "client": client, "ws": client.ws}
        self.bridge.telegram = AsyncMock()
        await self.bridge.handle_callback(self.bridge.identity("Trading"), {"id": "click", "data": "allow:key",
            "from": {"id": 42}, "message": {"chat": {"id": 42, "type": "private"}}})
        client.ws.send_json.assert_not_called()

    async def test_revocation_during_sleeve_lookup_prevents_submission(self):
        client = AsyncMock()
        entered, proceed = asyncio.Event(), asyncio.Event()
        async def request(method, params):
            if method == "thread/read":
                entered.set()
                await proceed.wait()
                return {"thread": {"status": {"type": "idle"}}}
            return {"data": []}
        client.request.side_effect = request
        self.bridge.ensure_sleeve = AsyncMock(return_value=client)
        self.bridge.ingest("Trading", self.update())
        task = asyncio.create_task(self.bridge.handle(self.store.data["inbox"][0]))
        await entered.wait()
        external = self.store.match_external_identity({"id": 42})
        self.bridge.update_external_policy(external.id, {"communication_policy": {"mode": "always_blocked", "agent_ids": []}})
        proceed.set()
        await task
        self.assertNotIn("turn/start", [call.args[0] for call in client.request.await_args_list])

    async def test_replies_only_reach_turn_participants(self):
        self.allow_users("Trading", "42", "99")
        self.bridge.ingest("Trading", self.update(user_id=99))
        self.bridge.add_turn_recipient("Trading", "turn", 42)
        self.bridge.relay_item("Trading", "turn", {"id": "answer", "text": "only for the initiator"})
        self.assertEqual([entry["chat_id"] for entry in self.store.data["outbox"]], [42])
        self.bridge.add_turn_recipient("Trading", "turn", 99)
        self.bridge.relay_item("Trading", "turn", {"id": "next", "text": "both participated"})
        self.assertEqual([entry["chat_id"] for entry in self.store.data["outbox"]], [42, 42, 99])

    async def test_external_identity_api_validates_persists_and_removes_agent_policy_endpoints(self):
        app = create_app(self.store)
        app.on_startup.clear()
        app.on_cleanup.clear()
        async with TestClient(TestServer(app)) as client:
            body = {"provider": "telegram", "username": "Example_Operator", "user_id": None,
                    "communication_policy": {"mode": "always_allowed", "agent_ids": []}}
            response = await client.post("/api/external-identities", json=body)
            self.assertEqual(response.status, 201)
            external = await response.json()
            self.assertEqual(external["username"], "@example_operator")
            self.assertEqual(Store(self.database).external_identity(external["id"]).communication_policy.mode, "always_allowed")
            response = await client.post("/api/external-identities", json=body)
            self.assertEqual(response.status, 400)
            for invalid in ({"mode": "everyone", "agent_ids": []}, {"mode": "specific_agents", "agent_ids": [OTHER]},
                            {"mode": "always_allowed", "agent_ids": [self.store.identity("Trading").id]}):
                response = await client.post(f"/api/external-identities/{external['id']}", json={"communication_policy": invalid})
                self.assertEqual(response.status, 400)
                self.assertEqual(Store(self.database).external_identity(external["id"]).communication_policy.mode, "always_allowed")
            response = await client.post(f"/api/external-identities/{external['id']}", json={"communication_policy": {"mode": "always_blocked", "agent_ids": []}})
            self.assertEqual(response.status, 200)
            self.assertEqual(Store(self.database).external_identity(external["id"]).communication_policy.mode, "always_blocked")
            response = await client.get("/api/external-identities")
            self.assertEqual(len(await response.json()), 2)
            response = await client.get("/api/state")
            state = await response.json()
            self.assertNotIn("communication_rules", state["identities"][0])
            self.assertIn("external_identities", state)
            response = await client.post("/api/identities/Trading/communication", json={"communication_rules": {"rules": []}})
            self.assertEqual(response.status, 400)
            for path in ("communication-rules", "authorize"):
                response = await client.post(f"/api/identities/Trading/{path}", json={})
                self.assertEqual(response.status, 404)

    async def test_routes_exact_thread_and_preserves_permissions(self):
        client = AsyncMock()
        client.request.side_effect = [
            {"thread": {"name": "Trading", "cwd": "/repo/trading", "status": {"type": "idle"}}},
            {"data": []}, {"turn": {"id": "new-turn"}},
        ]
        self.bridge.ensure_sleeve = AsyncMock(return_value=client)
        self.bridge.ingest("Trading", self.update())
        await self.bridge.handle(self.store.data["inbox"][0])
        method, params = client.request.call_args.args
        self.assertEqual(method, "turn/start")
        self.assertEqual(params["threadId"], THREAD)
        self.assertEqual(params["input"], [{"type": "text", "text": "hello"}])
        self.assertNotIn("approvalPolicy", params)
        self.assertNotIn("sandboxPolicy", params)

    async def test_active_turn_is_steered_without_starting_a_second_turn(self):
        client = AsyncMock()
        client.request.side_effect = [
            {"thread": {"name": "Trading", "cwd": "/repo/trading", "status": {"type": "active"}}},
            {"data": [{"id": "running", "status": "inProgress"}]}, {"turnId": "running"},
        ]
        self.bridge.ensure_sleeve = AsyncMock(return_value=client)
        self.bridge.ingest("Trading", self.update())
        await self.bridge.handle(self.store.data["inbox"][0])
        method, params = client.request.call_args.args
        self.assertEqual(method, "turn/steer")
        self.assertEqual(params["expectedTurnId"], "running")
        self.assertEqual(params["input"], [{"type": "text", "text": "hello"}])

    async def test_all_slash_text_is_forwarded_verbatim_to_idle_and_active_codex_sleeves(self):
        texts = ("/goal finish the task", "/goal", "/goal pause", "/help", "/start", "/status", "/who",
                 "/stop", "/list", "/kill Trading", "/create Research", "/bind Research", "/menu", "/cancel", "/unknown", "/goal@TestBot finish the task",
                 "  /goal keep whitespace\nincluding this line  ")
        update_id = 0
        for active in (False, True):
            for text in texts:
                with self.subTest(active=active, text=text):
                    client = AsyncMock()
                    client.request.side_effect = [
                        {"thread": {"status": {"type": "active" if active else "idle"}}},
                        {"data": [{"id": "running", "status": "inProgress"}] if active else []},
                        {"turnId": "running"} if active else {"turn": {"id": "new"}},
                    ]
                    self.bridge.ensure_sleeve = AsyncMock(return_value=client)
                    update_id += 1
                    self.bridge.ingest("Trading", self.update(text=text, update_id=update_id))
                    await self.bridge.handle(self.store.data["inbox"][-1])
                    method, params = client.request.call_args.args
                    self.assertEqual(method, "turn/steer" if active else "turn/start")
                    self.assertEqual(params["threadId"], THREAD)
                    self.assertEqual(params["input"], [{"type": "text", "text": text}])
                    self.assertEqual(self.store.data["outbox"], [])
                    self.assertNotIn("turn/interrupt", [call.args[0] for call in client.request.await_args_list])

    async def test_slash_text_without_sleeve_warns_instead_of_becoming_a_bridge_command(self):
        self.store.data["sleeves"].clear()
        self.bridge.ensure_sleeve = AsyncMock()
        self.bridge.ingest("Trading", self.update(text="/help"))
        await self.bridge.handle(self.store.data["inbox"][0])
        self.assertIn("has no sleeve", self.store.data["outbox"][0]["text"])
        self.bridge.ensure_sleeve.assert_not_called()

    async def test_early_slash_reply_routes_only_to_its_sender_before_submission_returns(self):
        self.allow_users("Trading", "42", "99")
        self.store.data["identities"]["Trading"]["telegram"]["chats"]["99"] = 99
        client = CodexClient("/tmp/test-codex.sock", self.bridge.on_codex)
        client.ws = object()
        self.bridge.connected["Trading"] = client.ws
        self.bridge.ingest("Trading", self.update(text="/goal finish the task"))
        self.store.data["inbox"][0]["status"] = "submitting"
        await self.bridge.on_codex(client, {"method": "turn/started", "params": {
            "threadId": THREAD, "turn": {"id": "early"}}})
        await self.bridge.on_codex(client, {"method": "item/completed", "params": {
            "threadId": THREAD, "turnId": "early", "item": {
                "id": "reply", "type": "agentMessage", "text": "Response to slash text"}}})
        self.assertEqual(self.store.data["turn_recipients"]["Trading"]["early"], [42])
        self.assertEqual([entry["chat_id"] for entry in self.store.data["outbox"]], [42])

    async def test_reply_isolation_and_event_deduplication(self):
        client = CodexClient("/tmp/test-codex.sock", self.bridge.on_codex)
        client.ws = object()
        self.bridge.connected["Trading"] = client.ws
        item = {"id": "answer", "type": "agentMessage", "phase": "final_answer", "text": "done"}
        event = {"method": "item/completed", "params": {"threadId": OTHER, "turnId": "turn", "item": item}}
        await self.bridge.on_codex(client, event)
        self.assertEqual(self.store.data["outbox"], [])
        event["params"]["threadId"] = THREAD
        await self.bridge.on_codex(client, event)
        await self.bridge.on_codex(client, event)
        self.assertEqual([entry["text"] for entry in self.store.data["outbox"]], ["done"])

    async def test_stale_approval_does_not_send_a_decision(self):
        self.bridge.telegram = AsyncMock()
        await self.bridge.handle_callback(AgentIdentity("Trading", self.tg), {"id": "callback", "data": "allow:expired"})
        self.assertEqual(self.bridge.telegram.call_args.kwargs["text"], "This approval has expired")

    async def test_reconnect_recovers_new_turns_in_order(self):
        client = AsyncMock()
        client.request.return_value = {"data": [
            {"id": "newest", "status": "completed", "items": [{"id": "item2", "type": "agentMessage", "text": "two"}]},
            {"id": "middle", "status": "completed", "items": [{"id": "item1", "type": "agentMessage", "text": "one"}]},
            {"id": "previous", "status": "completed", "items": [{"id": "old", "type": "agentMessage", "text": "do not replay"}]},
        ]}
        await self.bridge.catch_up("Trading", client)
        self.assertEqual([entry["text"] for entry in self.store.data["outbox"]], ["one", "two"])
        self.assertEqual(self.store.data["sleeves"]["Trading"]["last_turn_id"], "newest")

    async def test_recovery_does_not_mark_a_partial_answer_as_delivered(self):
        client = AsyncMock()
        turn = {"id": "new", "status": "inProgress", "items": [{"id": "item", "type": "agentMessage", "text": "partial"}]}
        client.request.return_value = {"data": [turn]}
        await self.bridge.catch_up("Trading", client)
        self.assertEqual(self.store.data["outbox"], [])
        turn.update(status="completed", items=[{"id": "item", "type": "agentMessage", "text": "complete"}])
        await self.bridge.catch_up("Trading", client)
        self.assertEqual([entry["text"] for entry in self.store.data["outbox"]], ["complete"])

    async def test_restart_preserves_routes_and_recovers_missed_replies_once(self):
        self.bridge.add_turn_recipient("Trading", "new", 42)
        self.bridge.relay_item("Trading", "new", {"id": "progress", "text": "working"})
        restarted = Bridge(Store(self.database))
        client = AsyncMock()
        client.ws = object()
        client.request.side_effect = [
            {"thread": {"source": "cli"}}, {},
            {"data": [
                {"id": "new", "status": "completed", "items": [
                    {"id": "progress", "type": "agentMessage", "text": "working"},
                    {"id": "answer", "type": "agentMessage", "text": "finished while offline"}]},
                {"id": "previous", "status": "completed", "items": []}]}]
        restarted.client = AsyncMock(return_value=client)
        await restarted.ensure_sleeve("Trading")
        self.assertEqual([call.args[0] for call in client.request.await_args_list],
                         ["thread/read", "thread/resume", "thread/turns/list"])
        persisted = Store(self.database)
        self.assertEqual([(entry["chat_id"], entry["text"]) for entry in persisted.data["outbox"]],
                         [(42, "working"), (42, "finished while offline")])
        client.request.side_effect = None
        client.request.return_value = {"data": [{"id": "new", "status": "completed", "items": []}]}
        await Bridge(persisted).catch_up("Trading", client)
        self.assertEqual(len(Store(self.database).data["outbox"]), 2)

    async def test_rebind_refuses_active_old_thread(self):
        client = AsyncMock()
        client.request.return_value = {"thread": {"status": {"type": "active"}}}
        self.bridge.client = AsyncMock(return_value=client)
        with self.assertRaisesRegex(ValueError, "finish"):
            await self.bridge.assign_sleeve("Trading", OTHER, "/tmp/test-codex.sock")
        self.assertEqual(self.store.data["sleeves"]["Trading"]["thread_id"], THREAD)

    async def test_local_api_needs_no_token_but_rejects_foreign_websites(self):
        app = create_app(self.store)
        app.on_startup.clear()
        app.on_cleanup.clear()
        async with TestClient(TestServer(app)) as client:
            response = await client.get("/api/state")
            self.assertEqual(response.status, 200)
            response = await client.get("/api/state", headers={"Origin": "https://attacker.example"})
            self.assertEqual(response.status, 403)
            response = await client.get("/api/state", headers={"Host": "attacker.example"})
            self.assertEqual(response.status, 403)
            response = await client.get("/api/state", headers={"Sec-Fetch-Site": "cross-site"})
            self.assertEqual(response.status, 403)
            response = await client.post("/api/identities", data="name=malicious")
            self.assertEqual(response.status, 415)
            response = await client.get("/api/state")
            self.assertEqual(response.status, 200)
            body = await response.json()
            self.assertNotIn("token", body["identities"][0]["telegram"])
            self.assertNotIn(TOKEN, json.dumps(body))

    async def test_successful_instruction_has_no_routine_acknowledgment(self):
        client = AsyncMock()
        client.request.side_effect = [
            {"thread": {"name": "Trading", "cwd": "/repo", "status": {"type": "idle"}}},
            {"data": []}, {"turn": {"id": "started"}},
        ]
        self.bridge.ensure_sleeve = AsyncMock(return_value=client)
        self.bridge.ingest("Trading", self.update())
        await self.bridge.handle(self.store.data["inbox"][0])
        self.assertEqual(self.store.data["outbox"], [])
        self.assertEqual(Store(self.database).data["turn_recipients"]["Trading"]["started"], [42])

    async def test_missing_and_unreachable_sleeves_warn_without_submitting(self):
        self.bridge.ensure_sleeve = AsyncMock(side_effect=ConnectionError("gone"))
        for missing in (False, True):
            with self.subTest(missing=missing):
                if missing:
                    self.store.data["sleeves"].pop("Trading")
                self.store.data["outbox"].clear()
                await self.bridge.handle({"identity": "Trading", "message": self.update()["message"]})
                self.assertIn("Warning:", self.store.data["outbox"][0]["text"])
        self.assertEqual(self.bridge.ensure_sleeve.await_count, 1)

    async def test_identity_uuid_survives_lost_sleeve_replacement_and_detach(self):
        original_id = self.bridge.identity("Trading").id
        client = AsyncMock()
        client.request.side_effect = [ConnectionError("lost"),
            {"thread": {"id": OTHER, "source": "vscode", "name": "New body", "cwd": "/repo/new", "status": {"type": "idle"}}},
            {}, {"data": []}]
        self.bridge.client = AsyncMock(return_value=client)
        self.bridge.approvals["stale"] = {"name": "Trading"}
        await self.bridge.assign_sleeve("Trading", OTHER, "/tmp/new.sock")
        identity = Store(self.database).identity("Trading")
        self.assertEqual(identity.id, original_id)
        self.assertEqual(identity.sleeve.thread_id, OTHER)
        self.assertEqual(identity.telegram.bot_id, 11)
        self.assertTrue(self.store.match_external_identity({"id": 42}).communication_policy.allows(identity.id))
        self.assertEqual(self.bridge.approvals, {})
        client.request.side_effect = [{"thread": {"status": {"type": "idle"}}}]
        await self.bridge.assign_sleeve("Trading", None, "/tmp/new.sock")
        self.assertIsNone(Store(self.database).identity("Trading").sleeve)
        self.assertEqual(Store(self.database).identity("Trading").id, original_id)

    async def test_sleeve_cannot_belong_to_two_identities_even_during_concurrent_assignment(self):
        await self.bridge.create_identity("Other")
        self.store.data["sleeves"].clear()
        client = AsyncMock()
        async def request(method, params):
            await asyncio.sleep(0)
            if method == "thread/read":
                return {"thread": {"id": THREAD, "source": "vscode", "cwd": "/repo", "status": {"type": "idle"}}}
            return {"data": []}
        client.request.side_effect = request
        self.bridge.client = AsyncMock(return_value=client)
        results = await asyncio.gather(*(self.bridge.assign_sleeve(name, THREAD, "/tmp/test.sock")
                                         for name in ("Trading", "Other")), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, ValueError) for result in results), 1)
        self.assertEqual(len(Store(self.database).data["sleeves"]), 1)

    async def test_thread_listing_and_assignment_allow_only_root_agents(self):
        app = create_app(self.store)
        app.on_startup.clear()
        app.on_cleanup.clear()
        provider = AsyncMock()
        provider.socket_path = "/tmp/test-codex.sock"
        unknown = "33333333-3333-4333-8333-333333333333"
        threads = {
            THREAD: {"id": THREAD, "source": "vscode", "name": "Root", "cwd": "/repo", "status": {"type": "idle"}},
            OTHER: {"id": OTHER, "source": {"subAgent": {"thread_spawn": {"parent_thread_id": THREAD}}}},
            unknown: {"id": unknown, "source": "unknown"},
        }
        async def request(method, params):
            if method == "thread/loaded/list":
                return {"data": [unknown]} if params["cursor"] else {"data": [THREAD, OTHER], "nextCursor": "next"}
            return {"thread": threads[params["threadId"]]}
        provider.request.side_effect = request
        app[BRIDGE_KEY].client = AsyncMock(return_value=provider)
        self.store.data["sleeves"].pop("Trading")
        self.store.save()
        async with TestClient(TestServer(app)) as client:
            response = await client.get("/api/threads")
            self.assertEqual([thread["id"] for thread in (await response.json())["threads"]], [THREAD])
            for route in ("sleeve", "binding"):
                response = await client.post(f"/api/identities/Trading/{route}", json={"thread_id": OTHER})
                self.assertEqual(response.status, 400)
                self.assertIn("Only root agents", (await response.json())["error"])
        self.assertIsNone(Store(self.database).identity("Trading").sleeve)
        self.assertFalse(any(call.args[0] == "thread/turns/list" for call in provider.request.await_args_list))

    async def test_preexisting_subagent_sleeve_cannot_resume_receive_work_or_relay(self):
        client = AsyncMock()
        client.socket_path = "/tmp/test-codex.sock"
        client.ws = object()
        client.request.return_value = {"thread": {"source": {"subAgent": "review"}}}
        self.bridge.client = AsyncMock(return_value=client)
        self.bridge.ingest("Trading", self.update())
        await self.bridge.handle(self.store.data["inbox"][0])
        self.assertEqual([call.args[0] for call in client.request.await_args_list], ["thread/read"])
        self.assertEqual(self.bridge.sleeve_status["Trading"]["status"], "unavailable")
        self.assertIn("no usable root-agent sleeve", self.store.data["outbox"][0]["text"])
        await self.bridge.on_codex(client, {"method": "item/completed", "params": {
            "threadId": THREAD, "turnId": "turn", "item": {"id": "child-answer", "type": "agentMessage", "text": "do not relay"}}})
        self.assertEqual(len(self.store.data["outbox"]), 1)

    async def test_root_sleeve_is_validated_before_resume_and_cached_per_connection(self):
        client = AsyncMock()
        client.ws = object()
        client.request.side_effect = [{"thread": {"source": "vscode"}}, {}]
        self.bridge.client = AsyncMock(return_value=client)
        self.bridge.catch_up = AsyncMock()
        await self.bridge.ensure_sleeve("Trading")
        await self.bridge.ensure_sleeve("Trading")
        self.assertEqual([call.args[0] for call in client.request.await_args_list], ["thread/read", "thread/resume"])

    async def test_communication_replacement_clears_previous_bot_state_and_preserves_identity(self):
        original_id = self.bridge.identity("Trading").id
        self.bridge.ingest("Trading", self.update())
        self.bridge.enqueue("Trading", "private old answer")
        self.bridge.add_turn_recipient("Trading", "old-turn", 42)
        self.store.data["pending_users"]["Trading"] = {"99": {"seen_at": 0}}
        self.bridge.approvals["old"] = {"name": "Trading"}
        self.bridge.verify_telegram = AsyncMock(return_value=TelegramIdentity(TOKEN, "NewBot", 22))
        self.bridge.start_poller = lambda name: None
        await self.bridge.configure_communication("Trading", {"token": TOKEN})
        identity = Store(self.database).identity("Trading")
        self.assertEqual(identity.id, original_id)
        self.assertEqual(identity.sleeve.thread_id, THREAD)
        self.assertEqual(identity.telegram.bot_id, 22)
        self.assertEqual(identity.telegram.chats, {})
        self.assertEqual(self.store.data["outbox"], [])
        self.assertEqual(self.store.data["inbox"][0]["status"], "done")
        self.assertNotIn("Trading", self.store.data["pending_users"])
        self.assertNotIn("Trading", self.store.data["turn_recipients"])
        self.assertEqual(self.bridge.approvals, {})

    async def test_communication_can_be_disabled_or_updated_without_losing_chats(self):
        self.bridge.start_poller = lambda name: None
        await self.bridge.configure_communication("Trading", ...)
        self.assertEqual(Store(self.database).identity("Trading").telegram.chats, {"42": 42})
        await self.bridge.configure_communication("Trading", None)
        identity = Store(self.database).identity("Trading")
        self.assertIsNone(identity.telegram)
        self.assertEqual(identity.sleeve.thread_id, THREAD)
        self.assertEqual(self.bridge.recipient_chat_ids("Trading"), [])

    async def test_failed_communication_validation_does_not_change_state(self):
        before = json.dumps(self.store.data, sort_keys=True)
        self.bridge.verify_telegram = AsyncMock(side_effect=ValueError("invalid token"))
        with self.assertRaises(ValueError):
            await self.bridge.configure_communication("Trading", {"token": "invalid"})
        self.assertEqual(json.dumps(self.store.data, sort_keys=True), before)
        self.assertEqual(Store(self.database).identity("Trading").telegram.bot_id, 11)

    async def test_identity_api_works_without_codex_or_telegram_and_validates_bodies(self):
        app = create_app(self.store)
        app.on_startup.clear()
        app.on_cleanup.clear()
        async with TestClient(TestServer(app)) as client:
            response = await client.post("/api/identities", json={"name": "Independent"})
            self.assertEqual(response.status, 201)
            identity_id = (await response.json())["id"]
            response = await client.get("/api/state")
            independent = next(identity for identity in (await response.json())["identities"] if identity["name"] == "Independent")
            self.assertEqual(independent["id"], identity_id)
            self.assertIsNone(independent["sleeve"])
            self.assertIsNone(independent["telegram"])
            self.assertEqual(independent["runtime"]["status"], "unassigned")
            response = await client.post("/api/identities/Independent/communication", json={"telegram": None})
            self.assertEqual(response.status, 200)
            for body in ([], {"name": 7}, {"name": "Independent"}, {"name": "Bad", "telegram": "invalid"}):
                response = await client.post("/api/identities", json=body)
                self.assertEqual(response.status, 400)
            for body in ({"kind": "unknown", "thread_id": THREAD}, {"thread_id": 7}):
                response = await client.post("/api/identities/Independent/sleeve", json=body)
                self.assertEqual(response.status, 400)
            response = await client.get("/")
            self.assertEqual(response.status, 200)
            html = await response.text()
            self.assertIn('<div id="app"></div>', html)
            self.assertNotIn("unsafe-eval", response.headers["Content-Security-Policy"])
            assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html)
            self.assertTrue(any(path.endswith(".js") for path in assets))
            self.assertTrue(any(path.endswith(".css") for path in assets))
            for path in assets:
                response = await client.get(path)
                self.assertEqual(response.status, 200)

    async def test_duplicate_update_is_not_ingested_again(self):
        self.bridge.ingest("Trading", self.update())
        self.bridge.ingest("Trading", self.update())
        self.assertEqual(len(Store(self.database).data["inbox"]), 1)

    async def test_restart_marks_submitting_receipt_uncertain_without_replaying(self):
        self.bridge.ingest("Trading", self.update())
        self.store.data["inbox"][0]["status"] = "submitting"
        self.store.save()
        restarted = Bridge(Store(self.database))
        restarted.telegram = AsyncMock()
        try:
            await restarted.start(None)
            persisted = Store(self.database).data
            self.assertEqual(persisted["inbox"][0]["status"], "uncertain")
            self.assertIn("not replayed", persisted["outbox"][0]["text"])
            self.assertEqual(restarted.clients, {})
        finally:
            await restarted.stop(None)

    async def test_bot_registration_validates_tokens_and_exclusive_ownership(self):
        self.bridge.telegram = AsyncMock(return_value={"id": 11, "username": "TestBot"})
        for settings in ({"token": "invalid"}, {"token": 7}, {"token_file": "/not/supported"}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                await self.bridge.verify_telegram("Other", settings)
        self.bridge.telegram.assert_not_called()
        with self.assertRaisesRegex(ValueError, "already registered"):
            await self.bridge.verify_telegram("Other", {"token": TOKEN})
        same_bot = await self.bridge.verify_telegram("Trading", {"token": " " + TOKEN + " "})
        self.assertEqual(same_bot.bot_id, 11)
        self.assertEqual(same_bot.token, TOKEN)
        self.assertNotIn(TOKEN, repr(same_bot))

    async def test_direct_tokens_persist_but_never_appear_in_management_responses(self):
        app = create_app(self.store)
        app.on_startup.clear()
        app.on_cleanup.clear()
        app[BRIDGE_KEY].telegram = AsyncMock(return_value={"id": 77, "username": "DirectBot"})
        app[BRIDGE_KEY].start_poller = lambda name: None
        rotated = "987654321:" + "n" * 35
        async with TestClient(TestServer(app)) as client:
            response = await client.post("/api/identities", json={"name": "Direct", "telegram": {"token": TOKEN}})
            self.assertEqual(response.status, 201)
            self.assertNotIn(TOKEN, await response.text())
            self.assertEqual(Store(self.database).identity("Direct").telegram.token, TOKEN)
            response = await client.post("/api/identities/Direct/communication", json={"telegram": {"token": rotated}})
            self.assertEqual(response.status, 200)
            self.assertNotIn(rotated, await response.text())
            response = await client.get("/api/state")
            body = await response.text()
            self.assertNotIn(TOKEN, body)
            self.assertNotIn(rotated, body)
            self.assertNotIn('"token"', body)
        self.assertEqual(Store(self.database).identity("Direct").telegram.token, rotated)

    async def test_websocket_connection_and_rpc_over_unix_socket(self):
        async def socket(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for frame in ws:
                message = json.loads(frame.data)
                if "id" in message:
                    if message["method"] == "large/result":
                        result = {"text": "x" * (4 * 1024 * 1024 + 1)}
                    else:
                        result = {"data": [THREAD]} if message["method"] == "thread/loaded/list" else {}
                    await ws.send_json({"id": message["id"], "result": result})
            return ws
        app = web.Application()
        app.router.add_get("/", socket)
        runner = web.AppRunner(app)
        await runner.setup()
        path = str(Path(self.temp.name) / "codex.sock")
        await web.UnixSite(runner, path).start()
        client = CodexClient(path, AsyncMock())
        try:
            await client.connect()
            self.assertEqual((await client.request("thread/loaded/list", {}))["data"], [THREAD])
            self.assertEqual(len((await client.request("large/result", {}))["text"]), 4 * 1024 * 1024 + 1)
        finally:
            await client.close()
            await runner.cleanup()


class FormattingTests(unittest.TestCase):
    def test_root_detection_uses_confirmed_origin_and_rejects_every_subagent_kind(self):
        for source in ("cli", "vscode", "exec", "appServer", {"custom": "local-client"}):
            with self.subTest(source=source):
                self.assertTrue(is_root_thread({"source": source}))
        for source in (None, "unknown", "future-origin", {}, [], {"custom": 7},
                       {"subAgent": "review"}, {"subAgent": "compact"},
                       {"subAgent": {"thread_spawn": {"parent_thread_id": THREAD}}},
                       {"custom": "client", "subAgent": "review"}):
            with self.subTest(source=source):
                self.assertFalse(is_root_thread({"source": source}))

    def test_invalid_external_identities_and_policies_fail_closed(self):
        for value in [None, {}, {"mode": "all", "agent_ids": []}, {"mode": "always_allowed", "agent_ids": [THREAD]},
                      {"mode": "specific_agents", "agent_ids": [42]}, {"mode": "specific_agents", "agent_ids": ["missing"]},
                      {"mode": "always_blocked", "agent_ids": [], "fallback": True}, {"mode": [], "agent_ids": []}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                CommunicationPolicy.from_dict(value)
        for user_id, username in [(None, None), (42, None), ("0", None), (None, "*"), (None, "42")]:
            with self.subTest(user_id=user_id, username=username), self.assertRaises(ValueError):
                ExternalIdentity("telegram", user_id, username)

    def test_old_single_owner_configuration_is_migrated_without_losing_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "old.token"
            token_path.write_text(TOKEN)
            state = {"identities": {"Old": {"token_file": str(token_path), "username": "OldBot", "bot_id": 1,
                "offset": 7, "allowed_user_id": 42, "chat_id": 42}}, "bindings": {"Old": {"thread_id": THREAD}},
                "pending_users": {}, "inbox": [], "outbox": [{"identity": "Old", "text": "pending"}], "seen_items": [], "events": []}
            (Path(directory) / "state.json").write_text(json.dumps(state))
            store = Store(Path(directory) / "agent-home.sqlite3")
            self.assertEqual(store.external_identities()[0].user_id, "42")
            self.assertEqual(store.external_identities()[0].communication_policy.agent_ids, {store.identity("Old").id})
            self.assertEqual(store.data["outbox"][0]["chat_id"], 42)
            self.assertEqual(store.data["sleeves"]["Old"]["thread_id"], THREAD)
            self.assertTrue((Path(directory) / "state.before-allowlists.json").exists())

    def test_sqlite_migration_is_once_only_and_preserves_legacy_source(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            token_path = directory / "old.token"
            token_path.write_text(TOKEN)
            legacy = {"identities": {"Old": {"token_file": str(token_path), "username": "OldBot", "bot_id": 1,
                "offset": 7, "chats": {"42": 42}, "resolved_user_ids": {}}},
                "bindings": {"Old": {"thread_id": THREAD, "socket_path": "/tmp/old.sock", "last_turn_id": "last"}},
                "inbound_message_allow_rules": {"Old": {"rules": [{"telegram_user_filter": {"allowed_user_ids": ["42"]}}]}},
                "pending_users": {}, "inbox": [{"id": "1:6", "identity": "Old", "status": "received", "message": {"text": "pending"}}],
                "outbox": [{"identity": "Old", "chat_id": 42, "text": "pending reply"}], "seen_items": ["seen"], "events": []}
            source = directory / "state.json"
            original = json.dumps(legacy)
            source.write_text(original)
            database = directory / "state.sqlite3"
            store = Store(database)
            identity_id = store.identity("Old").id
            self.assertEqual(source.read_text(), original)
            self.assertEqual((directory / "state.before-sqlite.json").read_text(), original)
            self.assertEqual(database.read_bytes()[:16], b"SQLite format 3\x00")
            self.assertEqual(database.stat().st_mode & 0o777, 0o600)
            store.data["identities"]["Old"]["telegram"]["offset"] = 99
            store.save()
            reopened = Store(database)
            self.assertEqual(reopened.identity("Old").id, identity_id)
            self.assertEqual(reopened.identity("Old").telegram.offset, 99)
            self.assertEqual(reopened.data["inbox"][0]["status"], "received")
            self.assertEqual(reopened.data["outbox"][0]["text"], "pending reply")
            self.assertEqual(reopened.identity("Old").sleeve.last_turn_id, "last")
            self.assertEqual(reopened.data["seen_items"], ["seen"])

    def test_sqlite_constraints_roll_back_an_invalid_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite3"
            store = Store(database)
            store.add_identity(AgentIdentity("First"))
            store.add_identity(AgentIdentity("Second"))
            sleeve = {"kind": "codex", "thread_id": THREAD, "socket_path": "/tmp/test.sock"}
            store.data["sleeves"]["First"] = sleeve.copy()
            store.save()
            store.data["sleeves"]["Second"] = sleeve.copy()
            with self.assertRaises(sqlite3.IntegrityError):
                store.save()
            self.assertEqual(set(Store(database).data["sleeves"]), {"First"})
            self.assertEqual(set(Store(database).data["identities"]), {"First", "Second"})

    def test_sqlite_token_file_upgrade_preserves_identity_and_backs_up_before_conversion(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "home.sqlite3"
            token_file = Path(directory) / "old.token"
            token_file.write_text(TOKEN)
            store = Store(database)
            store.add_identity(AgentIdentity("Old", TelegramIdentity(TOKEN, "OldBot", 11, offset=7, chats={"42": 42})))
            identity_id = store.identity("Old").id
            telegram = store.data["identities"]["Old"]["telegram"]
            telegram.pop("token")
            telegram["token_file"] = str(token_file)
            store.save()
            legacy_database(store, 1)
            upgraded = Store(database).identity("Old")
            self.assertEqual(upgraded.id, identity_id)
            self.assertEqual(upgraded.telegram.token, TOKEN)
            self.assertEqual(upgraded.telegram.offset, 7)
            self.assertEqual(upgraded.telegram.chats, {"42": 42})
            backup = Path(directory) / "home.before-external-identities.sqlite3"
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            with sqlite3.connect(backup) as connection:
                previous = json.loads(connection.execute("SELECT telegram FROM identities").fetchone()[0])
                self.assertEqual(previous["token_file"], str(token_file))
                self.assertNotIn("token", previous)
            token_file.unlink()
            self.assertEqual(Store(database).identity("Old").telegram.token, TOKEN)

    def test_external_account_uniqueness_and_agent_foreign_keys_roll_back_invalid_checkpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "home.sqlite3"
            store = Store(database)
            agent = AgentIdentity("Research")
            store.add_identity(agent)
            external = ExternalIdentity("telegram", "42", "@owner", CommunicationPolicy("specific_agents", frozenset({agent.id})))
            store.save_external_identity(external)
            store.save()
            store.data["external_identities"][external.id]["communication_policy"]["agent_ids"] = [OTHER]
            with self.assertRaises(sqlite3.IntegrityError):
                store.save()
            self.assertEqual(Store(database).external_identity(external.id), external)
            store.data["external_identities"][external.id] = external.to_dict()
            duplicate = dataclasses.replace(external, id=THREAD, username="@duplicate")
            store.data["external_identities"][duplicate.id] = duplicate.to_dict()
            with self.assertRaises(sqlite3.IntegrityError):
                store.save()
            self.assertEqual(Store(database).external_identities(), [external])

    def test_external_identity_migration_preserves_access_checkpoints_and_private_backup(self):
        for version in (2, 3):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                database = Path(directory) / "home.sqlite3"
                store = Store(database)
                telegram = TelegramIdentity(TOKEN, "OldBot", 11, offset=17, chats={"42": 42})
                store.add_identity(AgentIdentity("Old", telegram))
                store.add_identity(AgentIdentity("Other", TelegramIdentity(TOKEN, "OtherBot", 12)))
                store.add_identity(AgentIdentity("Empty"))
                original_id = store.identity("Old").id
                store.data["identities"]["Old"]["telegram"]["resolved_user_ids"] = {"@owner": "42"}
                store.data["inbox"] = [{"id": "11:16", "identity": "Old", "status": "received"}]
                store.data["outbox"] = [{"identity": "Old", "chat_id": 42, "text": "reply"}]
                store.save()
                old_rules = {"rules": [{"telegram_user_filter": {"allowed_user_ids": ["@owner", "99"]}}]} if version == 2 else {
                    "rules": [{"type": "AllowTelegramUser", "users": ["@owner", "99"]}]}
                other_rules = {"rules": [{"type": "AllowTelegramUser", "users": ["42"]}]}
                legacy_database(store, version, {"Old": old_rules, "Other": other_rules})
                upgraded = Store(database)
                self.assertEqual(upgraded.identity("Old").id, original_id)
                self.assertEqual(upgraded.identity("Old").telegram, telegram)
                self.assertEqual(len(upgraded.external_identities()), 2)
                owner = upgraded.match_external_identity({"id": 42})
                self.assertEqual(owner.username, "@owner")
                self.assertEqual(owner.communication_policy.mode, "specific_agents")
                self.assertEqual(owner.communication_policy.agent_ids, {original_id, store.identity("Other").id})
                self.assertIsNone(upgraded.match_external_identity({"id": 77, "username": "owner"}))
                self.assertFalse(owner.communication_policy.allows(store.identity("Empty").id))
                self.assertEqual(upgraded.data["inbox"], store.data["inbox"])
                self.assertEqual(upgraded.data["outbox"], store.data["outbox"])
                backup = Path(directory) / "home.before-external-identities.sqlite3"
                self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
                with sqlite3.connect(backup) as connection:
                    self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], version)
                    self.assertEqual(json.loads(connection.execute("SELECT allow_rules FROM identities WHERE name = 'Old'").fetchone()[0]), old_rules)
                self.assertEqual(Store(database).external_identity(owner.id), owner)
                with upgraded.connect() as connection:
                    self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 6)
                    self.assertNotIn("allow_rules", {row["name"] for row in connection.execute("PRAGMA table_info(identities)")})
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM external_identities").fetchone()[0], 2)

    def test_invalid_legacy_rules_do_not_change_database_or_grant_access(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "home.sqlite3"
            store = Store(database)
            store.add_identity(AgentIdentity("Old"))
            malformed = json.dumps({"rules": [{"telegram_user_filter": {"allowed_user_ids": "42"}}]})
            legacy_database(store, 2, {"Old": json.loads(malformed)})
            with self.assertRaises(ValueError):
                Store(database)
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertEqual(connection.execute("SELECT allow_rules FROM identities").fetchone()[0], malformed)

    def test_conflicting_legacy_pins_stop_migration_without_changing_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "home.sqlite3"
            store = Store(database)
            for name, user_id in (("First", "42"), ("Second", "99")):
                store.add_identity(AgentIdentity(name, TelegramIdentity(TOKEN, name + "Bot", 11)))
                store.data["identities"][name]["telegram"]["resolved_user_ids"] = {"@owner": user_id}
            store.save()
            legacy_database(store, 3, {name: {"rules": [{"type": "AllowTelegramUser", "users": ["@owner"]}]}
                                       for name in ("First", "Second")})
            with self.assertRaisesRegex(ValueError, "Conflicting"):
                Store(database)
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'external_identities'").fetchone()[0], 0)

    def test_missing_legacy_token_file_leaves_database_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "home.sqlite3"
            store = Store(database)
            store.add_identity(AgentIdentity("Old", TelegramIdentity(TOKEN, "OldBot", 11)))
            telegram = store.data["identities"]["Old"]["telegram"]
            telegram.pop("token")
            telegram["token_file"] = str(Path(directory) / "missing.token")
            store.save()
            legacy_database(store, 1)
            with self.assertRaisesRegex(ValueError, "must be readable"):
                Store(database)
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
                self.assertEqual(json.loads(connection.execute("SELECT telegram FROM identities").fetchone()[0]), telegram)

    def test_future_database_schema_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "future.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.execute("PRAGMA user_version = 99")
            with self.assertRaisesRegex(ValueError, "Unsupported database"):
                Store(database)
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 99)

    def test_service_config_is_typed_and_resolves_relative_database_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "service.toml"
            path.write_text('db_path = "private/home.sqlite3"\nport = 9876\n')
            config = ServiceConfig.load(path)
            self.assertIsInstance(config, ServiceConfig)
            self.assertEqual(config.db_path, (path.parent / "private/home.sqlite3").resolve())
            self.assertEqual(config.port, 9876)
            override = ServiceConfig.load(path, db_path="~/other.sqlite3", port=9999)
            self.assertEqual(override.db_path, Path.home() / "other.sqlite3")
            self.assertEqual(override.port, 9999)
            for text in ('unknown = "value"', 'port = 0', 'port = true', 'db_path = 5', 'db_path = ""'):
                path.write_text(text)
                with self.subTest(text=text), self.assertRaises(ValueError):
                    ServiceConfig.load(path)

    def test_utf16_chunks_preserve_astral_characters(self):
        text = "😀" * 2500 + "abc"
        chunks = list(text_chunks(text))
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(chunk.encode("utf-16-le")) // 2 <= 3900 for chunk in chunks))

    def test_errors_redact_bot_token(self):
        token = "123456789:" + "a" * 35
        self.assertNotIn(token, safe_error(Exception("https://api.telegram.org/bot" + token + "/getUpdates")))


if __name__ == "__main__":
    unittest.main()
