import asyncio
import dataclasses
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from agent_home.codex import DEFAULT_SOCKET
from agent_home.config import ServiceConfig
from agent_home.models import AgentIdentity, AgentSleeve
from agent_home.service import Bridge
from agent_home.storage import Store
from agent_home.sleeves import start_daemon, worker_running
from agent_home.web import BRIDGE_KEY, create_app


THREAD = '11111111-1111-4111-8111-111111111111'
OTHER = '22222222-2222-4222-8222-222222222222'


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
        self.store = Store(self.folder/'home.sqlite3')
        self.store.add_identity(AgentIdentity('Saved'))
        self.store.data['sleeves']['Saved'] = dataclasses.asdict(AgentSleeve(
            socket_path=str(DEFAULT_SOCKET), thread_id=THREAD, cwd=str(self.folder), last_turn_id='previous'))
        self.launch = {'thread_id':THREAD, 'socket_path':str(DEFAULT_SOCKET), 'folder':str(self.folder),
            'status':'running', 'initial_turn_id':'initial', 'worker_pid':123, 'codex_pid':456,
            'log_path':str(self.folder/'processes'/f'{THREAD}.log')}
        self.store.data['sleeve_launches'] = {'original':self.launch,
            'older':{**self.launch, 'thread_id':OTHER}}
        self.store.save()
        self.config = ServiceConfig(db_path=self.store.path, sleeve_allowed_folders=(self.folder,))
        self.bridge = Bridge(self.store,self.config)
        self.addAsyncCleanup(self.bridge.telegram.close)
        self.rpc = AsyncMock()
        self.rpc.ws = object()
        self.thread = {'id':THREAD,'source':'cli','cwd':str(self.folder),'status':{'type':'idle'}}
        self.resumed = {'thread':self.thread,'approvalPolicy':'never','sandbox':{'type':'dangerFullAccess'}}
        async def request(method, params):
            self.assertEqual(params['threadId'],THREAD)
            if method == 'thread/read': return {'thread':self.thread}
            if method == 'thread/resume': return self.resumed
            if method == 'thread/turns/list': return {'data':[]}
            self.fail(f'Unexpected RPC during recovery: {method}')
        self.rpc.request.side_effect = request
        self.bridge.client = AsyncMock(return_value=self.rpc)
        self.worker = {'worker_pid':789,'codex_pid':987,'log_path':self.launch['log_path']}

    async def recover(self):
        return await self.bridge.recover_sleeve('Saved',THREAD,str(DEFAULT_SOCKET))

    async def test_recovery_preserves_binding_checkpoints_all_session_records_and_never_sends_input(self):
        original = self.store.data['sleeves']['Saved'].copy()
        with patch('agent_home.service.codex_executable',return_value='/bin/codex'), \
                patch('agent_home.service.worker_running',return_value=False), \
                patch('agent_home.service.start_worker',return_value=self.worker) as start:
            result = await self.recover()
        self.assertEqual(result['thread_id'],THREAD)
        self.assertFalse(result['already_running'])
        start.assert_called_once_with(self.folder,THREAD,str(DEFAULT_SOCKET),'/bin/codex',Path(self.launch['log_path']))
        self.assertEqual(self.store.data['sleeves']['Saved'],original)
        restored = Store(self.store.path)
        self.assertEqual(set(restored.data['sleeve_launches']),{'original','older'})
        self.assertEqual(restored.data['sleeve_launches']['original']['initial_turn_id'],'initial')
        self.assertEqual(restored.data['sleeve_launches']['original']['codex_pid'],987)
        self.assertEqual(restored.identity('Saved').id,self.store.identity('Saved').id)
        self.assertEqual(self.bridge.sleeve_process('Saved')['status'],'running')

    async def test_reconnects_existing_cli_without_spawning_even_if_launch_folders_disabled(self):
        self.bridge.config = dataclasses.replace(self.config,sleeve_allowed_folders=())
        with patch('agent_home.service.codex_executable',return_value='/bin/codex'), \
                patch('agent_home.service.worker_running',return_value=True), \
                patch('agent_home.service.start_worker') as start:
            self.assertTrue((await self.recover())['already_running'])
            start.assert_not_called()

    async def test_stopped_daemon_is_started_without_creating_a_new_thread(self):
        self.bridge.client.side_effect = [ConnectionRefusedError('daemon stopped'),self.rpc]
        with patch('agent_home.service.codex_executable',return_value='/bin/codex'), \
                patch('agent_home.service.worker_running',return_value=False), \
                patch('agent_home.service.start_daemon') as daemon, \
                patch('agent_home.service.start_worker',return_value=self.worker):
            await self.recover()
        daemon.assert_called_once_with('/bin/codex')
        self.assertEqual(self.bridge.client.await_count,2)

    async def test_stale_binding_subagent_changed_permissions_or_disallowed_folder_never_launch(self):
        with patch('agent_home.service.codex_executable',return_value='/bin/codex'), \
                patch('agent_home.service.worker_running',return_value=False), \
                patch('agent_home.service.start_worker') as start:
            with self.assertRaisesRegex(ValueError,'changed'):
                await self.bridge.recover_sleeve('Saved',OTHER,str(DEFAULT_SOCKET))
            self.thread['source'] = {'subAgent':'review'}
            with self.assertRaisesRegex(ValueError,'root'): await self.recover()
            self.thread['source'] = 'cli'
            self.resumed['approvalPolicy'] = 'on-request'
            with self.assertRaisesRegex(ValueError,'permissions changed'): await self.recover()
            self.resumed['approvalPolicy'] = 'never'
            self.bridge.config = dataclasses.replace(self.config,sleeve_allowed_folders=())
            with self.assertRaisesRegex(ValueError,'No sleeve folders'): await self.recover()
            start.assert_not_called()

    async def test_unconfirmed_worker_start_is_durable_and_later_observed_worker_is_reused(self):
        with patch('agent_home.service.codex_executable',return_value='/bin/codex'), \
                patch('agent_home.service.worker_running',return_value=False), \
                patch('agent_home.service.start_worker',side_effect=RuntimeError('unconfirmed')):
            with self.assertRaises(RuntimeError): await self.recover()
        self.assertEqual(Store(self.store.path).data['sleeve_launches']['original']['recovery_status'],'unconfirmed')
        self.assertEqual(self.store.data['sleeves']['Saved']['thread_id'],THREAD)

    async def test_api_rejects_foreign_origin_unknown_fields_and_unmanaged_sleeves(self):
        app = create_app(self.store,self.config)
        app[BRIDGE_KEY].client = AsyncMock(return_value=self.rpc)
        body = {'thread_id':THREAD,'socket_path':str(DEFAULT_SOCKET)}
        path = '/api/identities/Saved/sleeve/recover'
        async with TestClient(TestServer(app)) as client:
            self.assertEqual((await client.post(path,json=body,headers={'Origin':'https://foreign.example'})).status,403)
            self.assertEqual((await client.post(path,json={**body,'folder':'/tmp'})).status,400)
            self.store.data['sleeve_launches'] = {}
            self.assertEqual((await client.post(path,json=body)).status,400)

    def test_daemon_timeout_and_unknown_process_state_fail_without_claiming_recovery(self):
        with patch('agent_home.sleeves.subprocess.run',side_effect=subprocess.TimeoutExpired('codex',20)):
            with self.assertRaisesRegex(RuntimeError,'startup was not confirmed'):
                start_daemon('/bin/codex')
            with self.assertRaisesRegex(RuntimeError,'Could not inspect'):
                worker_running(self.launch)


if __name__ == '__main__': unittest.main()
