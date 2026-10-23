from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from agent_home.models import AgentIdentity
from agent_home.service import Bridge
from agent_home.storage import Store


THREAD = "11111111-1111-4111-8111-111111111111"


class EmptySleeveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "home.sqlite3")
        self.store.add_identity(AgentIdentity("Empty"))
        self.bridge = Bridge(self.store)
        self.client = SimpleNamespace(socket_path="/tmp/codex.sock", ws=object(), request=AsyncMock())
        self.bridge.client = AsyncMock(return_value=self.client)
        self.thread = {"id": THREAD, "source": "appServer", "cwd": self.temp.name, "status": {"type": "idle"}}

    async def test_assign_initializes_confirmed_empty_root_once_and_hides_dummy_reply(self):
        self.client.request.side_effect = [{"thread": self.thread},
            RuntimeError(f"no rollout found for thread id {THREAD}"), {"data": []},
            {"turn": {"id": "initial"}}, RuntimeError(f"failed to read thread: rollout at /test/rollout-date-{THREAD}.jsonl is empty"),
            {"data": [{"id": "initial", "status": "completed"}]},
            {}, {"data": [{"id": "initial", "status": "completed"}]}]
        await self.bridge.assign_sleeve("Empty", THREAD, self.client.socket_path)
        self.assertEqual([call.args[0] for call in self.client.request.await_args_list].count("turn/start"), 1)
        self.assertIn("Reply only: Ready.", self.client.request.await_args_list[3].args[1]["input"][0]["text"])
        restored = Bridge(Store(self.store.path))
        self.assertEqual(next(iter(restored.store.data["sleeve_initializations"].values()))["status"], "ready")
        self.client.request.reset_mock(side_effect=True)
        self.client.request.return_value = {}
        await restored.resume_sleeve(self.client, THREAD)
        self.assertEqual(self.client.request.call_args.args[0], "thread/resume")
        self.bridge.enqueue = lambda *args, **kwargs: self.fail("Dummy initialization leaked to Telegram")
        self.bridge.relay_item("Empty", "initial", {"id": "ready", "text": "Ready."})

    async def test_lost_initialization_response_is_durable_and_not_replayed(self):
        self.client.request.side_effect = [RuntimeError("list_turns is not supported yet"),
            {"data": []}, ConnectionError("response lost")]
        with self.assertRaises(ConnectionError):
            await self.bridge.resume_sleeve(self.client, THREAD)
        restored = Bridge(Store(self.store.path))
        self.client.request.reset_mock(side_effect=True)
        with self.assertRaisesRegex(RuntimeError, "will not be replayed"):
            await restored.resume_sleeve(self.client, THREAD)
        self.client.request.assert_not_called()

    async def test_completed_history_and_unrelated_failures_never_trigger_dummy(self):
        for error, history in [(RuntimeError("permission denied"), None),
                               (RuntimeError(f"no rollout found for thread id {THREAD}"), [{"id": "existing"}])]:
            self.client.request.reset_mock(side_effect=True)
            self.client.request.side_effect = [error] + ([{"data": history}] if history else [])
            with self.assertRaises(RuntimeError):
                await self.bridge.resume_sleeve(self.client, THREAD)
            self.assertNotIn("turn/start", [call.args[0] for call in self.client.request.await_args_list])

    async def test_subagent_assignment_is_rejected_before_initialization(self):
        self.client.request.return_value = {"thread": self.thread | {"source": {"subAgent": {"thread_spawn": {}}}}}
        with self.assertRaisesRegex(ValueError, "root agents"):
            await self.bridge.assign_sleeve("Empty", THREAD, self.client.socket_path)
        self.assertEqual([call.args[0] for call in self.client.request.await_args_list], ["thread/read"])


if __name__ == "__main__":
    unittest.main()
