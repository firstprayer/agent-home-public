import asyncio
import contextlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch
import uuid

from aiohttp.test_utils import TestClient, TestServer

from agent_home.codex import CodexClient
from agent_home.config import ServiceConfig
from agent_home.models import AgentIdentity
from agent_home.service import Bridge
from agent_home.sleeves import allowed_folder, codex_environment, locked_worker, worker_running
from agent_home.storage import DatabaseLocks, Store
from agent_home.web import create_app

THREAD = '11111111-1111-4111-8111-111111111111'
WORKER_EXECUTABLE = None  # CI substitutes the built server to exercise its detached entry point.


class SettingsAndCreationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.folder = self.root/'projects/work'
        self.folder.mkdir(parents=True)
        self.config_path = self.root/'service.toml'
        self.config = ServiceConfig(db_path=self.root/'state/old.sqlite3', sleeve_allowed_folders=(self.root/'projects',))
        self.config.save(self.config_path)
        self.locks = DatabaseLocks()
        self.locks.acquire(self.config.db_path)
        self.addCleanup(self.locks.close)
        self.store = Store(self.config.db_path)
        self.store.add_identity(AgentIdentity('Existing'))
        self.bridge = Bridge(self.store, self.config, self.config_path, self.locks)
        self.addAsyncCleanup(self.bridge.telegram.close)

    def body(self, path=None, folders=None):
        return {'db_path': str(path or self.store.path),
            'sleeve_allowed_folders': folders if folders is not None else [str(self.root/'projects')]}

    async def test_database_moves_without_losing_queued_work_and_old_copy_survives(self):
        self.store.data['inbox'] = [{'id':'receipt','identity':'Existing','status':'received','message':{'text':'pending'}}]
        self.store.data['outbox'] = [{'identity':'Existing','text':'reply','chat_id':123}]
        self.store.data['sleeve_launches'] = {'nonce':{'thread_id':THREAD,'status':'starting'}}
        self.store.save()
        original = self.store.path
        original_data = json.loads(json.dumps(self.store.data))
        target = self.root/'new/location.sqlite3'
        result = await self.bridge.configure_settings(self.body(target, []))
        self.assertEqual(result['db_path'], str(target))
        self.assertEqual(result['sleeve_allowed_folders'], [])
        self.assertTrue(original.exists())
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(ServiceConfig.load(self.config_path).db_path, target)
        restored = Store(target)
        for key in ('identities','inbox','outbox','sleeve_launches'):
            self.assertEqual(restored.data[key], original_data[key])
        self.store.event('Existing','A later reply')
        self.assertEqual(Store(target).data['events'][-1]['message'], 'A later reply')
        self.assertFalse(any(event['message']=='A later reply' for event in Store(original).data['events']))
        other = DatabaseLocks()
        self.addCleanup(other.close)
        for path in (original,target):
            with self.assertRaises(BlockingIOError): other.acquire(path)

    async def test_existing_destination_and_locked_directory_are_rejected(self):
        existing = self.root/'other/existing.sqlite3'
        existing.parent.mkdir()
        existing.write_bytes(b'keep this file')
        before = self.config_path.read_bytes()
        with self.assertRaisesRegex(ValueError,'never overwritten'):
            await self.bridge.configure_settings(self.body(existing))
        self.assertEqual(existing.read_bytes(), b'keep this file')
        foreign = DatabaseLocks()
        self.addCleanup(foreign.close)
        foreign.acquire(existing)
        with self.assertRaisesRegex(ValueError,'Another service'):
            await self.bridge.configure_settings(self.body(existing.parent/'new.sqlite3'))
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertEqual(self.store.path, self.config.db_path)

    async def test_failed_config_commit_keeps_original_and_releases_target_locks(self):
        target = self.root/'new/state.sqlite3'
        with patch.object(ServiceConfig,'save',side_effect=OSError('simulated disk error')):
            with self.assertRaises(OSError): await self.bridge.configure_settings(self.body(target))
        self.assertEqual(self.store.path,self.config.db_path)
        self.assertEqual(ServiceConfig.load(self.config_path),self.config)
        self.assertFalse(target.exists())
        other = DatabaseLocks()
        self.addCleanup(other.close)
        other.acquire(target)

    async def test_settings_are_typed_persisted_and_empty_list_disables_creation(self):
        await self.bridge.configure_settings(self.body(folders=[]))
        self.assertEqual(ServiceConfig.load(self.config_path).sleeve_allowed_folders, ())
        with self.assertRaisesRegex(ValueError,'No sleeve folders'):
            await self.bridge.create_sleeve(str(self.folder),str(uuid.uuid4()))
        for body in ({'db_path':None,'sleeve_allowed_folders':[]}, self.body(folders='anywhere'),
                     self.body(folders=[{}]), {**self.body(),'port':9999}):
            with self.assertRaises(ValueError): await self.bridge.configure_settings(body)

    async def test_api_exposes_settings_without_credentials_and_validates_mutations(self):
        app = create_app(self.store,self.config,self.config_path,self.locks)
        async with TestClient(TestServer(app)) as client:
            response = await client.get('/api/settings')
            value = await response.json()
            self.assertEqual(value['db_path'],str(self.store.path))
            self.assertTrue(value['editable'])
            self.assertNotIn('token',value)
            self.assertEqual((await client.post('/api/settings',json=self.body(folders=[]))).status,200)
            self.assertEqual((await client.post('/api/sleeves',json={'folder':str(self.folder),'request_id':str(uuid.uuid4())})).status,400)
            self.assertEqual((await client.post('/api/sleeves',json={'folder':str(self.folder),'request_id':None})).status,400)
            self.assertEqual((await client.post('/api/settings',json=self.body(),headers={'Origin':'https://foreign.example'})).status,403)

    async def test_creation_is_exact_root_yolo_durable_and_idempotent(self):
        rpc = AsyncMock()
        rpc.request.return_value = {'approvalPolicy':'never','sandbox':{'type':'dangerFullAccess'},
            'thread':{'id':THREAD,'source':'appServer','cwd':str(self.folder)}}
        root = rpc.request.return_value
        rpc.request.side_effect = [root, {'turn': {'id': 'initial'}},
            RuntimeError(f'no rollout found for thread id {THREAD}'), {}]
        self.bridge.client = AsyncMock(return_value=rpc)
        nonce = str(uuid.uuid4())
        def started(*args):
            receipt=Store(self.store.path).data['sleeve_launches'][nonce]
            self.assertEqual(receipt['thread_id'],THREAD)
            self.assertEqual(receipt['status'],'starting')
            self.assertEqual(receipt['initial_turn_id'],'initial')
            self.assertEqual(rpc.request.await_args_list[-1].args[0], 'thread/resume')
            return {'worker_pid':123,'codex_pid':456,'log_path':'private.log'}
        with patch('agent_home.service.codex_executable',return_value='/bin/codex'), patch('agent_home.service.start_worker',side_effect=started) as launch:
            result = await self.bridge.create_sleeve(str(self.folder),nonce)
            again = await self.bridge.create_sleeve(str(self.folder),nonce)
        self.assertEqual(result,again)
        launch.assert_called_once()
        calls = rpc.request.await_args_list
        self.assertEqual([call.args[0] for call in calls], ['thread/start','turn/start','thread/resume','thread/resume'])
        self.assertEqual(calls[0].args[1],{'cwd':str(self.folder),'approvalPolicy':'never','sandbox':'danger-full-access','ephemeral':False})
        self.assertEqual(calls[1].args[1]['threadId'],THREAD)
        self.assertIn('Reply only: Ready.',calls[1].args[1]['input'][0]['text'])
        self.assertEqual(result['thread_id'],THREAD)
        self.assertEqual(Store(self.store.path).data['sleeve_launches'][nonce]['status'],'running')
        self.assertEqual(self.store.data['sleeves'],{})
        restored = Bridge(Store(self.store.path),self.config)
        self.addAsyncCleanup(restored.telegram.close)
        restored.client = AsyncMock()
        self.assertEqual(await restored.create_sleeve(str(self.folder),nonce),result)
        restored.client.assert_not_called()

    async def test_uncertain_rpc_is_checkpointed_and_never_replayed_on_restart(self):
        rpc = AsyncMock()
        rpc.request.side_effect = ConnectionError('lost response')
        self.bridge.client = AsyncMock(return_value=rpc)
        nonce = str(uuid.uuid4())
        with patch('agent_home.service.codex_executable',return_value='/bin/codex'):
            with self.assertRaises(ConnectionError): await self.bridge.create_sleeve(str(self.folder),nonce)
        restored = Bridge(Store(self.store.path),self.config)
        self.addAsyncCleanup(restored.telegram.close)
        restored.client = AsyncMock()
        with self.assertRaisesRegex(ValueError,'previous launch'):
            await restored.create_sleeve(str(self.folder),nonce)
        restored.client.assert_not_called()

    async def test_management_is_rechecked_after_rpc_and_before_initialization_or_worker_launch(self):
        for revoked_at in ("client", "thread/start", "turn/start", "thread/resume"):
            with self.subTest(revoked_at=revoked_at):
                permitted = True
                def check():
                    if not permitted:
                        raise ValueError("Management access revoked")
                rpc = AsyncMock()
                async def request(method, params):
                    nonlocal permitted
                    if method == revoked_at:
                        permitted = False
                    if method == "thread/start":
                        return {'approvalPolicy':'never','sandbox':{'type':'dangerFullAccess'},
                            'thread':{'id':THREAD,'source':'appServer','cwd':str(self.folder)}}
                    if method == "turn/start":
                        return {'turn': {'id': 'initial'}}
                    return {}
                async def client(socket):
                    nonlocal permitted
                    if revoked_at == "client":
                        permitted = False
                    return rpc
                rpc.request.side_effect = request
                self.bridge.client = AsyncMock(side_effect=client)
                nonce = str(uuid.uuid4())
                with patch('agent_home.service.codex_executable', return_value='/bin/codex'), patch('agent_home.service.start_worker') as worker:
                    with self.assertRaisesRegex(ValueError, 'revoked'):
                        await self.bridge.create_sleeve(str(self.folder), nonce, check_authorization=check)
                    worker.assert_not_called()
                if revoked_at == "client":
                    rpc.request.assert_not_called()
                    self.assertNotIn(nonce, self.store.data['sleeve_launches'])
                else:
                    self.assertEqual(Store(self.store.path).data['sleeve_launches'][nonce]['status'], 'unconfirmed')
                    before = rpc.request.await_count
                    with self.assertRaisesRegex(ValueError, 'previous launch'):
                        await self.bridge.create_sleeve(str(self.folder), nonce)
                    self.assertEqual(rpc.request.await_count, before)
                methods = [call.args[0] for call in rpc.request.await_args_list]
                if revoked_at == 'thread/start':
                    self.assertNotIn('turn/start', methods)
                if revoked_at == 'turn/start':
                    self.assertNotIn('thread/resume', methods)

    async def test_uncertain_initialization_or_failed_resume_never_launches_or_replays(self):
        root = {'approvalPolicy':'never','sandbox':{'type':'dangerFullAccess'},
            'thread':{'id':THREAD,'source':'appServer','cwd':str(self.folder)}}
        for replies in ([root, ConnectionError('lost initialization response')],
                        [root, {'turn':{'id':'initial'}}, RuntimeError('access denied')],
                        [root, {'turn':{'id':'initial'}}, asyncio.TimeoutError()]):
            rpc = AsyncMock()
            rpc.request.side_effect = replies
            self.bridge.client = AsyncMock(return_value=rpc)
            nonce = str(uuid.uuid4())
            with patch('agent_home.service.codex_executable',return_value='/bin/codex'), patch('agent_home.service.start_worker') as worker:
                with self.assertRaises((ConnectionError,RuntimeError)):
                    await self.bridge.create_sleeve(str(self.folder),nonce)
                worker.assert_not_called()
            restored = Bridge(Store(self.store.path),self.config)
            self.addAsyncCleanup(restored.telegram.close)
            restored.client = AsyncMock()
            with self.assertRaisesRegex(ValueError,'previous launch'):
                await restored.create_sleeve(str(self.folder),nonce)
            restored.client.assert_not_called()
            self.assertEqual(Store(self.store.path).data['sleeve_launches'][nonce]['status'],'unconfirmed')

    async def test_subagents_wrong_folders_and_weaker_permissions_never_launch_cli(self):
        rpc=AsyncMock()
        self.bridge.client=AsyncMock(return_value=rpc)
        examples=[('cli',str(self.folder),'on-request'),('cli',str(self.root),'never'),
                  ({'subAgent':{'parentThreadId':THREAD}},str(self.folder),'never')]
        with patch('agent_home.service.codex_executable',return_value='/bin/codex'), patch('agent_home.service.start_worker') as launch:
            for source,folder,approval in examples:
                rpc.request.return_value={'thread':{'id':THREAD,'cwd':folder,'source':source},
                    'approvalPolicy':approval,'sandbox':{'type':'dangerFullAccess'}}
                with self.assertRaises(RuntimeError): await self.bridge.create_sleeve(str(self.folder),str(uuid.uuid4()))
        launch.assert_not_called()


class FolderBoundaryTests(unittest.TestCase):
    def test_roots_children_siblings_traversal_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory).resolve()
            root=base/'allowed'
            child=root/'child'
            child.mkdir(parents=True)
            outside=base/'allowed-other'
            outside.mkdir()
            (root/'escape').symlink_to(outside,target_is_directory=True)
            self.assertEqual(allowed_folder(str(root),[root]),root)
            self.assertEqual(allowed_folder(str(child),[root]),child)
            for path in (outside,root/'..'/'allowed-other',root/'escape',root/'missing'):
                with self.assertRaises(ValueError): allowed_folder(str(path),[root])
            for path in ('relative','',None):
                with self.assertRaises(ValueError): allowed_folder(path,[root])


class EmptyThreadProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_unmaterialized_turn_history_becomes_empty(self):
        client=CodexClient('/unused',AsyncMock())
        client.ws=AsyncMock()
        client.ws.closed=False
        async def reply(message):
            client.pending[message['id']].set_exception(RuntimeError(
                f'thread {THREAD} is not materialized yet; thread/turns/list is unavailable before first user message'))
        client.ws.send_json.side_effect=reply
        self.assertEqual(await client.request('thread/turns/list',{'threadId':THREAD}),{'data':[],'nextCursor':None})
        with self.assertRaises(RuntimeError): await client.request('thread/read',{'threadId':THREAD})
        with self.assertRaises(RuntimeError): await client.request('thread/turns/list',{'threadId':str(uuid.uuid4())})
        async def denied(message): client.pending[message['id']].set_exception(RuntimeError('access denied'))
        client.ws.send_json.side_effect=denied
        with self.assertRaisesRegex(RuntimeError,'access denied'):
            await client.request('thread/turns/list',{'threadId':THREAD})


class DetachedProcessTests(unittest.TestCase):
    def test_external_cli_gets_user_libraries_without_parent_session_markers(self):
        with patch('agent_home.sleeves.sys.frozen',True,create=True), patch.dict(os.environ,
                {'LD_LIBRARY_PATH':'/bundle','LD_LIBRARY_PATH_ORIG':'/user/libs',
                 'DYLD_LIBRARY_PATH':'/bundle','CODEX_THREAD_ID':THREAD,'PYINSTALLER_RESET_ENVIRONMENT':'1'}):
            environment=codex_environment('/custom/bin/codex')
        self.assertEqual(environment['LD_LIBRARY_PATH'],'/user/libs')
        self.assertNotIn('DYLD_LIBRARY_PATH',environment)
        self.assertNotIn('CODEX_THREAD_ID',environment)
        self.assertNotIn('PYINSTALLER_RESET_ENVIRONMENT',environment)
        self.assertTrue(environment['PATH'].startswith('/custom/bin'+os.pathsep))

    def test_codex_pty_process_survives_its_launching_process_and_answers_cursor_query(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            record=root/'observed.json'
            executable=root/'fake-codex'
            executable.write_text(f'#!{sys.executable}\n' + '''import json, os, sys, time, tty
tty.setraw(0)
os.write(1,b"\\x1b[6n")
response=os.read(0,64)
with open(os.environ["SLEEVE_TEST_RECORD"],"w") as stream:
    json.dump({"pid":os.getpid(),"cwd":os.getcwd(),"args":sys.argv[1:],"cursor":response.decode()},stream)
time.sleep(60)
''')
            executable.chmod(0o700)
            prefix = f'import sys; sys.frozen=True; sys.executable={WORKER_EXECUTABLE!r}; ' if WORKER_EXECUTABLE else ''
            code=(prefix+'from agent_home.sleeves import start_worker; from pathlib import Path; import json; '
                f'print(json.dumps(start_worker(Path({str(root)!r}),{THREAD!r},"/tmp/test.sock",{str(executable)!r},Path({str(root/"worker.log")!r}))))')
            launched=subprocess.run([sys.executable,'-c',code],env={**os.environ,'SLEEVE_TEST_RECORD':str(record)},capture_output=True,text=True,timeout=15)
            self.assertEqual(launched.returncode,0,launched.stderr)
            result=json.loads(launched.stdout)
            try:
                # The worker acknowledges process creation, not the test CLI's Python startup.
                deadline = time.monotonic() + 5
                while not record.exists() and time.monotonic() < deadline:
                    time.sleep(0.1)
                observed=json.loads(record.read_text())
                self.assertEqual(observed['pid'],result['codex_pid'])
                self.assertEqual(observed['cwd'],str(root))
                self.assertIn('--yolo',observed['args'])
                self.assertIn(THREAD,observed['args'])
                self.assertEqual(observed['cursor'],'\x1b[1;1R')
                os.kill(result['codex_pid'],0)
                os.kill(result['worker_pid'],0)
                receipt={**result,'folder':str(root),'thread_id':THREAD,'socket_path':'/tmp/test.sock'}
                self.assertTrue(worker_running(receipt))
                duplicate=subprocess.run([sys.executable,'-c',code],env={**os.environ,'SLEEVE_TEST_RECORD':str(record)},capture_output=True,text=True,timeout=15)
                self.assertEqual(duplicate.returncode,0,duplicate.stderr)
                self.assertEqual(json.loads(duplicate.stdout)['codex_pid'],result['codex_pid'])
                self.assertTrue(json.loads(duplicate.stdout)['already_running'])
                os.kill(result['codex_pid'],signal.SIGKILL)
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    try:
                        if locked_worker(root/'worker.log') is None: break
                    except RuntimeError:
                        pass
                    time.sleep(0.1)
                else: self.fail('Keeper did not release its lock after the CLI was killed')
                self.assertFalse(worker_running(receipt))
                previous_pid=result['codex_pid']
                recovered=subprocess.run([sys.executable,'-c',code],env={**os.environ,'SLEEVE_TEST_RECORD':str(record)},capture_output=True,text=True,timeout=15)
                self.assertEqual(recovered.returncode,0,recovered.stderr)
                result=json.loads(recovered.stdout)
                self.assertNotEqual(result['codex_pid'],previous_pid)
                self.assertTrue(worker_running({**receipt,**result}))
            finally:
                with contextlib.suppress(ProcessLookupError): os.kill(result['codex_pid'],signal.SIGTERM)


if __name__=='__main__': unittest.main()
