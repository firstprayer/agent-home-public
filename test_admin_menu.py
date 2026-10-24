import asyncio
import dataclasses
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock

from agent_home.models import AgentIdentity
from agent_home.storage import Store
from test_admin import AdminFixtures, TOKEN


NEW_TOKEN = "987654321:" + "n" * 35


class AdminMenuTests(AdminFixtures):
    async def click(self, label, user_id=42):
        entry = next(entry for entry in reversed(self.store.data["outbox"]) if "reply_markup" in entry)
        buttons = entry["reply_markup"].get("inline_keyboard", [])
        button = next(button for row in buttons for button in row if button["text"] == label)
        self.bridge.telegram = AsyncMock(return_value={})
        await self.bridge.handle(self.callback_job(button["callback_data"], user_id))
        return self.store.data["outbox"][-1]

    async def deliver_prompt(self, message_id=101):
        entry = self.store.data["outbox"][-1]
        self.assertTrue(entry["reply_markup"]["force_reply"])
        self.bridge.telegram = AsyncMock(return_value={"message_id": message_id})
        await self.bridge.deliver_entry(self.store.identity("Admin"), entry)

    def reply(self, text, user_id=42, prompt_id=101):
        self.sequence += 1
        self.bridge.ingest("Admin", {"update_id": self.sequence, "message": {"message_id": self.sequence,
            "text": text, "from": {"id": user_id}, "chat": {"id": user_id, "type": "private"},
            "reply_to_message": {"message_id": prompt_id}}})
        return self.store.data["inbox"][-1]

    async def token_prompt(self, name="Worker team"):
        await self.command("/menu")
        await self.click(name)
        await self.click("Set / change Telegram token")
        await self.deliver_prompt()

    def verify_bot(self, bot_id=33, username="NewProjectBot"):
        self.bridge.start_poller = lambda name: None
        async def telegram(identity, method, **params):
            self.assertEqual(method, "getMe")
            return {"id": bot_id, "username": username}
        self.bridge.telegram = AsyncMock(side_effect=telegram)

    async def test_menu_is_cached_and_scoped_and_does_not_expose_tokens(self):
        await self.command("/start")
        self.assertIn("Choose an agent", self.store.data["outbox"][-1]["text"])
        entry = await self.click("Worker team")
        self.assertIn("@Worker teamBot", entry["text"])
        self.assertNotIn(TOKEN, json.dumps(self.store.data["outbox"]))
        self.assertTrue(any(button["text"] == "Stop work" for row in entry["reply_markup"]["inline_keyboard"] for button in row))
        self.bridge.client.assert_not_called()
        await self.command("/menu", user_id=99)
        buttons = self.store.data["outbox"][-1]["reply_markup"]["inline_keyboard"]
        self.assertEqual([button["text"] for row in buttons for button in row], ["Admin"])

    async def test_token_is_validated_saved_and_never_written_to_receipts_or_replies(self):
        await self.token_prompt()
        job = self.reply(NEW_TOKEN)
        self.assertNotIn(NEW_TOKEN, json.dumps(self.store.data["inbox"]))
        self.assertNotIn(NEW_TOKEN, self.database.read_bytes().decode(errors="ignore"))
        self.verify_bot()
        await self.bridge.handle(job)
        reopened = Store(self.database)
        self.assertEqual(reopened.identity("Worker team").telegram.token, NEW_TOKEN)
        self.assertEqual(reopened.identity("Worker team").telegram.bot_id, 33)
        self.assertIn("Connected @NewProjectBot", self.store.data["outbox"][-1]["text"])
        for key in ("inbox", "outbox", "events"):
            self.assertNotIn(NEW_TOKEN, json.dumps(reopened.data[key]))
        self.assertIn("/menu", await self.command("/help"))

    async def test_prompt_reply_is_bound_to_sender_bot_and_delivered_message(self):
        await self.token_prompt()
        old = self.store.identity("Worker team").telegram
        self.bridge.telegram = AsyncMock(side_effect=AssertionError("Token should not be verified"))
        await self.bridge.handle(self.reply(NEW_TOKEN, prompt_id=999))
        self.assertEqual(self.store.identity("Worker team").telegram, old)
        self.assertIn("current Admin prompt", self.store.data["outbox"][-1]["text"])
        self.assertNotIn(NEW_TOKEN, json.dumps(self.store.data["inbox"]))

    async def test_cancel_and_restart_expire_token_input_without_saving_or_replaying_it(self):
        await self.token_prompt()
        job = self.reply(NEW_TOKEN)
        await self.command("/cancel")
        await self.bridge.handle(job)
        self.assertEqual(self.store.identity("Worker team").telegram.token, TOKEN)
        await self.token_prompt()
        job = self.reply(NEW_TOKEN)
        self.bridge.admin.menu.sessions.clear()
        await self.bridge.handle(job)
        self.assertEqual(Store(self.database).identity("Worker team").telegram.token, TOKEN)
        self.assertNotIn(NEW_TOKEN, json.dumps(self.store.data["inbox"]))

    async def test_invalid_and_duplicate_tokens_preserve_the_current_connection(self):
        await self.token_prompt()
        old = self.store.identity("Worker team").telegram
        await self.bridge.handle(self.reply("not a valid token"))
        self.assertEqual(self.store.identity("Worker team").telegram, old)
        self.assertIn("Invalid Telegram bot token", self.store.data["outbox"][-1]["text"])
        await self.token_prompt()
        self.verify_bot(bot_id=11)
        await self.bridge.handle(self.reply(NEW_TOKEN))
        self.assertIn("already registered", self.store.data["outbox"][-1]["text"])
        self.assertEqual(Store(self.database).identity("Worker team").telegram, old)

    async def test_access_revoked_during_verification_cannot_update_the_token(self):
        await self.token_prompt()
        async def telegram(identity, method, **params):
            self.bridge.update_external_policy(self.owner.id, {"communication_policy": {
                "mode": "specific_agents", "agent_ids": [self.store.identity("Admin").id]}})
            return {"id": 33, "username": "NewProjectBot"}
        self.bridge.telegram = AsyncMock(side_effect=telegram)
        await self.bridge.handle(self.reply(NEW_TOKEN))
        self.assertEqual(Store(self.database).identity("Worker team").telegram.token, TOKEN)
        self.assertIn("Always allowed", self.store.data["outbox"][-1]["text"])

    async def test_concurrent_desktop_connection_change_is_not_overwritten(self):
        await self.token_prompt()
        self.store.data["identities"]["Worker team"]["telegram"]["token"] = "876543210:" + "x" * 35
        self.store.save()
        self.bridge.telegram = AsyncMock(side_effect=AssertionError("Stale input should fail before getMe"))
        await self.bridge.handle(self.reply(NEW_TOKEN))
        self.assertIn("connection changed", self.store.data["outbox"][-1]["text"])
        self.assertNotEqual(Store(self.database).identity("Worker team").telegram.token, NEW_TOKEN)

    async def test_menu_creates_identity_then_offers_connection_and_sleeve_actions(self):
        await self.command("/menu")
        await self.click("+ New agent identity")
        await self.deliver_prompt()
        await self.bridge.handle(self.reply("New project"))
        target = Store(self.database).identity("New project")
        self.assertIsNone(target.telegram)
        self.assertIsNone(target.sleeve)
        entry = self.store.data["outbox"][-1]
        self.assertIn("Created New project", entry["text"])
        labels = [button["text"] for row in entry["reply_markup"]["inline_keyboard"] for button in row]
        self.assertIn("Set / change Telegram token", labels)
        self.assertIn("Assign sleeve", labels)
        self.assertIn("Create new sleeve", labels)

    async def folder_menu(self, count=2):
        folders = tuple(Path(self.temp.name).resolve() / f"Project {index}" for index in range(count))
        for folder in folders:
            folder.mkdir(exist_ok=True)
        self.bridge.config = dataclasses.replace(self.bridge.config, sleeve_allowed_folders=folders)
        if "New sleeve project" not in self.store.identities():
            self.store.add_identity(AgentIdentity("New sleeve project"))
        await self.command("/menu")
        await self.click("New sleeve project")
        await self.click("Create new sleeve")
        return folders

    async def test_folder_picker_creates_and_assigns_exact_root_once_and_survives_restart(self):
        folders = await self.folder_menu(12)
        await self.click("Next →")
        entry = self.store.data["outbox"][-1]
        self.assertIn("(2/2)", entry["text"])
        for row in entry["reply_markup"]["inline_keyboard"]:
            for button in row:
                self.assertLessEqual(len(button["callback_data"].encode()), 64)
        button = entry["reply_markup"]["inline_keyboard"][0][0]
        await self.click(button["text"])
        self.assertIn(str(folders[10]), self.store.data["outbox"][-1]["text"])
        self.assertIn("--yolo", self.store.data["outbox"][-1]["text"])
        _, roots = self.loaded_roots()
        roots[0]["cwd"] = str(folders[10])
        self.bridge.create_sleeve = AsyncMock(return_value={"thread_id": roots[0]["id"], "socket_path": "/tmp/test.sock"})
        data = self.choice()
        await self.bridge.handle(self.callback_job(data))
        self.assertIn("created and assigned", self.store.data["outbox"][-1]["text"])
        self.assertEqual(Store(self.database).identity("New sleeve project").sleeve.thread_id, roots[0]["id"])
        self.assertEqual(self.bridge.create_sleeve.await_args.args[0], str(folders[10]))
        await self.bridge.handle(self.callback_job(data))
        self.bridge.create_sleeve.assert_awaited_once()

    async def test_empty_folder_list_reports_settings_without_starting_codex(self):
        await self.command("/menu")
        await self.click("Worker team")
        entry = await self.click("Create new sleeve")
        self.assertIn("No sleeve folders configured", entry["text"])
        self.assertIn("Settings", entry["text"])
        self.bridge.client.assert_not_called()

    async def test_stale_folders_bindings_expired_and_restarted_menus_cannot_launch(self):
        for change in ("folders", "binding", "expired", "restart"):
            with self.subTest(change=change):
                self.store.data["sleeves"].pop("New sleeve project", None)
                await self.folder_menu()
                await self.click(self.store.data["outbox"][-1]["reply_markup"]["inline_keyboard"][0][0]["text"])
                data = self.choice()
                if change == "folders":
                    self.bridge.config = dataclasses.replace(self.bridge.config, sleeve_allowed_folders=())
                elif change == "binding":
                    self.store.data["sleeves"]["New sleeve project"] = {
                        **self.store.data["sleeves"]["Worker team"], "thread_id": "33333333-3333-4333-8333-333333333333"}
                elif change == "expired":
                    self.bridge.admin.menu.sessions[(self.store.identity("Admin").id, 42)]["expires"] = 0
                else:
                    self.bridge.admin.menu.sessions.clear()
                self.bridge.create_sleeve = AsyncMock()
                await self.bridge.handle(self.callback_job(data))
                self.bridge.create_sleeve.assert_not_called()

    async def test_folder_confirmation_is_sender_scoped_and_uncertain_creation_is_not_replayed(self):
        await self.folder_menu()
        await self.click(self.store.data["outbox"][-1]["reply_markup"]["inline_keyboard"][0][0]["text"])
        data = self.choice()
        self.bridge.create_sleeve = AsyncMock(side_effect=ConnectionError("Launch result not confirmed"))
        await self.bridge.handle(self.callback_job(data, user_id=99))
        self.bridge.create_sleeve.assert_not_called()
        await self.bridge.handle(self.callback_job(data))
        self.assertIn("not confirmed", self.store.data["outbox"][-1]["text"])
        self.bridge.admin.menu.sessions.clear()
        await self.bridge.handle(self.callback_job(data))
        self.bridge.create_sleeve.assert_awaited_once()

    async def test_new_sleeve_action_rejects_scoped_senders_and_admin_itself(self):
        self.bridge.update_external_policy(next(external.id for external in self.store.external_identities() if external.user_id == "99"), {
            "communication_policy": {"mode": "specific_agents", "agent_ids": [self.store.identity(name).id for name in ("Admin", "Worker team")]}})
        for name, user in (("Worker team", 99), ("Admin", 42)):
            await self.command("/menu", user_id=user)
            entry = await self.click(name, user_id=user)
            self.assertFalse(any(button["text"] == "Create new sleeve" for row in entry["reply_markup"]["inline_keyboard"] for button in row))
            nonce = self.bridge.admin.menu.sessions[(self.store.identity("Admin").id, user)]["nonce"]
            self.bridge.create_sleeve = AsyncMock()
            await self.bridge.handle(self.callback_job(f"admin-menu:{nonce}:create", user_id=user))
            self.bridge.create_sleeve.assert_not_called()

    async def test_assignment_failure_keeps_created_sleeve_and_reports_no_automatic_retry(self):
        await self.folder_menu()
        await self.click(self.store.data["outbox"][-1]["reply_markup"]["inline_keyboard"][0][0]["text"])
        _, roots = self.loaded_roots()
        self.bridge.create_sleeve = AsyncMock(return_value={"thread_id": roots[0]["id"], "socket_path": "/tmp/test.sock"})
        self.bridge.assign_sleeve = AsyncMock(side_effect=ValueError("Stop or finish the existing turn before rebinding"))
        data = self.choice()
        await self.bridge.handle(self.callback_job(data))
        entry = self.store.data["outbox"][-1]
        self.assertIn("created", entry["text"])
        self.assertIn("but not assigned", entry["text"])
        self.assertIn(roots[0]["id"], entry["text"])
        await self.bridge.handle(self.callback_job(data))
        self.bridge.create_sleeve.assert_awaited_once()

    async def test_revocation_after_creation_prevents_assignment(self):
        await self.folder_menu()
        await self.click(self.store.data["outbox"][-1]["reply_markup"]["inline_keyboard"][0][0]["text"])
        _, roots = self.loaded_roots()
        async def create(*args, **kwargs):
            self.bridge.update_external_policy(self.owner.id, {"communication_policy": {
                "mode": "specific_agents", "agent_ids": [self.store.identity("Admin").id]}})
            return {"thread_id": roots[0]["id"], "socket_path": "/tmp/test.sock"}
        self.bridge.create_sleeve = AsyncMock(side_effect=create)
        await self.click("Create and assign")
        self.assertIsNone(Store(self.database).identity("New sleeve project").sleeve)
        self.assertIn("not assigned", self.store.data["outbox"][-1]["text"])

    async def test_menu_pages_keep_agent_selection_bound_to_its_uuid(self):
        for index in range(11):
            self.store.add_identity(AgentIdentity(f"Extra {index}"))
        await self.command("/menu")
        await self.click("Next →")
        self.assertIn("(2/2)", self.store.data["outbox"][-1]["text"])
        await self.click("Extra 10")
        record = self.bridge.admin.menu.sessions[(self.store.identity("Admin").id, 42)]
        self.assertEqual(record["target_id"], self.store.identity("Extra 10").id)

    async def test_previous_agent_card_cannot_configure_a_newly_selected_agent(self):
        await self.command("/menu")
        old = await self.click("Worker team")
        data = old["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        await self.click("← All agents")
        await self.click("Admin")
        await self.bridge.handle(self.callback_job(data))
        self.assertIn("expired", self.store.data["outbox"][-1]["text"])
        self.assertFalse(self.bridge.admin.menu.waiting(self.store.identity("Admin"), {"id": 42}))

    async def test_menu_binding_and_stop_reuse_existing_service_rules(self):
        _, roots = self.loaded_roots()
        self.store.add_identity(AgentIdentity("Menu project"))
        await self.command("/menu")
        await self.click("Menu project")
        await self.click("Assign sleeve")
        await self.bridge.handle(self.callback_job(self.choice()))
        self.assertEqual(self.store.identity("Menu project").sleeve.thread_id, roots[0]["id"])
        await self.command("/menu")
        await self.click("Menu project")
        self.runtime()
        await self.click("Stop work")
        self.assertIn("No active turn", self.store.data["outbox"][-1]["text"])

    async def test_token_redaction_also_covers_unsolicited_and_quoted_credentials(self):
        job = self.ingest("Admin", NEW_TOKEN)
        self.assertTrue(job["admin_token"])
        self.assertNotIn(NEW_TOKEN, self.database.read_bytes().decode(errors="ignore"))
        await self.bridge.handle(job)
        self.assertNotIn(NEW_TOKEN, json.dumps(self.store.data["outbox"]))
        self.sequence += 1
        self.bridge.ingest("Admin", {"update_id": self.sequence, "message": {"text": "/menu",
            "from": {"id": 42}, "chat": {"id": 42, "type": "private"},
            "reply_to_message": {"message_id": 123, "text": NEW_TOKEN}}})
        self.assertNotIn(NEW_TOKEN, json.dumps(self.store.data["inbox"]))

    async def test_cancelled_prompts_and_revoked_menus_are_not_delivered(self):
        await self.command("/menu")
        await self.click("Worker team")
        await self.click("Set / change Telegram token")
        prompt = self.store.data["outbox"][-1]
        await self.command("/cancel")
        self.bridge.telegram = AsyncMock()
        await self.bridge.deliver_entry(self.store.identity("Admin"), prompt)
        self.bridge.telegram.assert_not_called()
        self.assertNotIn(prompt, self.store.data["outbox"])
        await self.command("/menu")
        home = self.store.data["outbox"][-1]
        self.bridge.update_external_policy(self.owner.id, {"communication_policy": {
            "mode": "specific_agents", "agent_ids": [self.store.identity("Admin").id]}})
        await self.bridge.deliver_entry(self.store.identity("Admin"), home)
        self.bridge.telegram.assert_not_called()

    async def test_revocation_discards_queued_credentials_from_memory(self):
        await self.token_prompt()
        job = self.reply(NEW_TOKEN)
        self.bridge.update_external_policy(self.owner.id, {"communication_policy": {
            "mode": "specific_agents", "agent_ids": [self.store.identity("Admin").id]}})
        self.assertEqual(self.bridge.admin.menu.sessions, {})
        await self.bridge.handle(job)
        self.assertEqual(Store(self.database).identity("Worker team").telegram.token, TOKEN)

    async def test_scoped_user_cannot_forge_a_telegram_configuration_action(self):
        self.bridge.update_external_policy(next(external.id for external in self.store.external_identities() if external.user_id == "99"), {
            "communication_policy": {"mode": "specific_agents", "agent_ids": [self.store.identity(name).id for name in ("Admin", "Worker team")]}})
        await self.command("/menu", user_id=99)
        entry = await self.click("Worker team", user_id=99)
        self.assertFalse(any("Telegram token" in button["text"] for row in entry["reply_markup"]["inline_keyboard"] for button in row))
        nonce = self.bridge.admin.menu.sessions[(self.store.identity("Admin").id, 99)]["nonce"]
        await self.bridge.handle(self.callback_job(f"admin-menu:{nonce}:token", user_id=99))
        self.assertIn("Always allowed", self.store.data["outbox"][-1]["text"])

    async def test_revocation_while_stopping_old_poller_restores_original_listener(self):
        await self.token_prompt()
        async def poller():
            try:
                await asyncio.Future()
            finally:
                self.bridge.update_external_policy(self.owner.id, {"communication_policy": {
                    "mode": "specific_agents", "agent_ids": [self.store.identity("Admin").id]}})
        task = asyncio.create_task(poller())
        await asyncio.sleep(0)
        self.bridge.pollers["Worker team"] = task
        self.verify_bot()
        restarted = []
        self.bridge.start_poller = restarted.append
        await self.bridge.handle(self.reply(NEW_TOKEN))
        self.assertEqual(Store(self.database).identity("Worker team").telegram.token, TOKEN)
        self.assertEqual(restarted, ["Worker team"])

    async def test_duplicate_token_reply_does_not_erase_successful_confirmation(self):
        await self.token_prompt()
        first, duplicate = self.reply(NEW_TOKEN), self.reply(NEW_TOKEN)
        self.verify_bot()
        await self.bridge.handle(first)
        confirmation = self.store.data["outbox"][-1]
        await self.bridge.handle(duplicate)
        self.assertTrue(self.bridge.admin.menu.deliverable(self.store.identity("Admin"), confirmation))
        self.assertEqual(Store(self.database).identity("Worker team").telegram.token, NEW_TOKEN)


if __name__ == "__main__":
    unittest.main()
