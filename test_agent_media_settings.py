import dataclasses
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer

from agent_home.models import AgentIdentity, TelegramIdentity
from agent_home.storage import Store
from agent_home.web import BRIDGE_KEY, create_app


class AgentMediaSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / 'home.sqlite3'
        self.store = Store(self.database)
        self.original = AgentIdentity('Research', TelegramIdentity('123456789:' + 't' * 35, 'ResearchBot', 11))
        self.store.add_identity(self.original)
        self.store.add_identity(AgentIdentity('Studio'))
        app = create_app(self.store)
        app.on_startup.clear()
        app.on_cleanup.clear()
        self.bridge = app[BRIDGE_KEY]
        self.bridge.telegram = AsyncMock()
        self.bridge.start_poller = lambda name: None
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def test_defaults_and_update_are_identity_scoped_and_durable_without_changing_bot_or_id(self):
        state = await (await self.client.get('/api/state')).json()
        self.assertTrue(all(agent['allow_external_media'] for agent in state['identities']))
        response = await self.client.post('/api/identities/Research/communication', json={'allow_external_media':False})
        self.assertEqual(response.status, 200)
        restored = Store(self.database)
        self.assertEqual(restored.identity('Research'), dataclasses.replace(self.original, allow_external_media=False))
        self.assertTrue(restored.identity('Studio').allow_external_media)
        await self.bridge.configure_communication('Research', ...)
        self.assertFalse(Store(self.database).identity('Research').allow_external_media)
        state = await (await self.client.get('/api/state')).json()
        self.assertFalse(next(agent for agent in state['identities'] if agent['name'] == 'Research')['allow_external_media'])
        self.bridge.telegram.assert_not_called()

    async def test_creation_accepts_an_explicit_preference_and_rejects_non_boolean_values_before_changes(self):
        response = await self.client.post('/api/identities', json={'name':'Private workspace', 'allow_external_media':False})
        self.assertEqual(response.status, 201)
        self.assertFalse(Store(self.database).identity('Private workspace').allow_external_media)
        for value in (None, 0, 1, 'false', [], {}):
            for endpoint, body in (('/api/identities', {'name':'Invalid setting', 'allow_external_media':value}),
                                   ('/api/identities/Research/communication', {'allow_external_media':value})):
                with self.subTest(value=value, endpoint=endpoint):
                    response = await self.client.post(endpoint, json=body)
                    self.assertEqual(response.status, 400)
        self.assertNotIn('Invalid setting', self.store.identities())
        self.assertEqual(Store(self.database).identity('Research'), self.original)
        self.bridge.telegram.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'boolean'):
            AgentIdentity('Invalid', allow_external_media='false')

    async def test_schema_five_migration_defaults_to_enabled_preserves_queues_and_creates_one_private_backup(self):
        self.store.data['outbox'] = [{'identity':'Research','chat_id':42,'media':{'path':'/downloads/image.png','root':'/workspace'}}]
        self.store.data['inbox'] = [{'id':'pending','identity':'Research','status':'received','message':{'text':'Continue'}}]
        self.store.save()
        with self.store.connect() as connection:
            connection.execute('ALTER TABLE identities DROP COLUMN allow_external_media')
            connection.execute('PRAGMA user_version = 5')
        migrated = Store(self.database)
        self.assertEqual(migrated.identity('Research'), self.original)
        self.assertEqual(migrated.data['inbox'], self.store.data['inbox'])
        self.assertEqual(migrated.data['outbox'], self.store.data['outbox'])
        backup = self.database.with_name('home.before-external-media.sqlite3')
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(backup) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 5)
            self.assertNotIn('allow_external_media', [row[1] for row in connection.execute('PRAGMA table_info(identities)')])
        original_backup = backup.read_bytes()
        migrated.data['identities']['Research']['allow_external_media'] = False
        migrated.save()
        self.assertFalse(Store(self.database).identity('Research').allow_external_media)
        self.assertEqual(backup.read_bytes(), original_backup)
        with migrated.connect() as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 6)


if __name__ == '__main__':
    unittest.main()
