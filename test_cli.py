"""Exercise desktop startup against real, isolated server processes."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

from agent_home.config import ServiceConfig

SERVER = [sys.executable, str(Path(__file__).parent / "bridge.py")]


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name).resolve()
        self.environment = {**os.environ, "HOME": str(self.home)}
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.config = self.home / "custom.toml"
        self.config.write_text(f'db_path = "data/home.sqlite3"\nport = {self.port}\n')

    @contextmanager
    def server(self, *args):
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen([*SERVER, "--desktop", *map(str, args)], env=self.environment,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors)
            try:
                self.assertTrue(select.select([process.stdout], [], [], 20)[0], "Server startup timed out")
                line = process.stdout.readline()
                if not line:
                    process.wait(timeout=10)
                    errors.seek(0)
                    self.fail(f"Server exited {process.returncode}: {errors.read().decode()}")
                message = json.loads(line)
                self.assertEqual(message["event"], "ready")
                yield process, message["url"]
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    raise
                process.stdin.close()
                process.stdout.close()

    def request(self, url, data=None):
        request = Request(url, data=json.dumps(data).encode() if data is not None else None,
            headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=5) as response:
            return response.read()

    def test_desktop_config_created_privately_and_preserved(self):
        with patch.dict(os.environ, self.environment):
            path = ServiceConfig.desktop_path()
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(ServiceConfig.load(path).db_path,
                self.home / ".config/agent-communication/agent-home.sqlite3")
            path.write_text('db_path = "custom.sqlite3"\nport = 9001\n')
            self.assertEqual(ServiceConfig.desktop_path(), path)
            self.assertEqual(ServiceConfig.load(path).port, 9001)

    def test_ready_means_listening_assets_and_database_survive_restart(self):
        with self.server("--config", self.config) as (_, url):
            self.assertEqual(url, f"http://127.0.0.1:{self.port}/")
            html = self.request(url).decode()
            self.assertIn("/assets/", html)
            self.assertIn(b"identities", self.request(url + "api/state"))
            self.request(url + "api/identities", {"name": "Desktop test"})
        with self.server("--config", self.config) as (_, url):
            state = json.loads(self.request(url + "api/state"))
            self.assertEqual([identity["name"] for identity in state["identities"]], ["Desktop test"])
        self.assertEqual((self.home / "data/home.sqlite3").stat().st_mode & 0o777, 0o600)

    def test_desktop_uses_existing_default_service_configuration(self):
        path = self.home / ".config/agent-communication/service.toml"
        path.parent.mkdir(parents=True)
        path.write_text(self.config.read_text())
        with self.server() as (_, url):
            self.assertEqual(url, f"http://127.0.0.1:{self.port}/")
            self.assertIn(b"identities", self.request(url + "api/state"))
        self.assertTrue((path.parent / "data/home.sqlite3").is_file())

    def test_launcher_disappearance_stops_only_its_server(self):
        with self.server("--config", self.config) as (process, _):
            process.stdin.close()
            self.assertEqual(process.wait(timeout=15), 0)
        with self.server("--config", self.config) as (_, url):
            self.assertIn(b"identities", self.request(url + "api/state"))

    def test_second_process_refuses_same_database_without_touching_first(self):
        with self.server("--config", self.config) as (_, url):
            duplicate = subprocess.run([*SERVER, "--desktop", "--config", str(self.config)],
                env=self.environment, input=b"", capture_output=True, timeout=15)
            self.assertEqual(duplicate.returncode, 1)
            self.assertEqual(duplicate.stdout, b"")
            self.assertIn(b"already using this database", duplicate.stderr)
            self.assertIn(b"identities", self.request(url + "api/state"))

    def test_settings_move_live_database_then_restart_from_persisted_location(self):
        target = self.home / "moved/home.sqlite3"
        folder = self.home / "projects"
        folder.mkdir()
        with self.server("--config", self.config) as (_, url):
            self.request(url + "api/identities", {"name": "Before relocation"})
            body = {"db_path": str(target), "sleeve_allowed_folders": [str(folder)]}
            result = json.loads(self.request(url + "api/settings", body))
            self.assertEqual(result["db_path"], str(target))
            self.assertEqual(result["sleeve_allowed_folders"], [str(folder)])
            self.request(url + "api/identities", {"name": "After relocation"})
            state = json.loads(self.request(url + "api/state"))
            ids = {identity["name"]: identity["id"] for identity in state["identities"]}
        with self.server("--config", self.config) as (_, url):
            restored = json.loads(self.request(url + "api/state"))
            self.assertEqual({identity["name"]: identity["id"] for identity in restored["identities"]}, ids)
            self.assertEqual(json.loads(self.request(url + "api/settings"))["db_path"], str(target))
        self.assertTrue((self.home / "data/home.sqlite3").is_file())
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
