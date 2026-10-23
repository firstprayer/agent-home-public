"""Command-line entry point; holds the single-writer state lock."""

import argparse
import logging
import json
import os
from pathlib import Path
import signal
import sys
import threading

from aiohttp import web

from .storage import Store, DatabaseLocks
from .config import ServiceConfig
from .web import create_app

LOG = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Local agent identities and replaceable Codex sleeves")
    parser.add_argument("--config", type=Path, help="Service TOML configuration")
    parser.add_argument("--db-path", type=Path, help="Override the configured SQLite database path")
    parser.add_argument("--state-dir", type=Path, help="Legacy alias for --db-path DIR/agent-home.sqlite3")
    parser.add_argument("--port", type=int, help="Override the configured port")
    parser.add_argument("--desktop", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--sleeve-worker", nargs=5, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.sleeve_worker:
        from .sleeves import run_worker
        run_worker(*args.sleeve_worker)
        return
    if args.state_dir and args.db_path:
        parser.error("Use --db-path or --state-dir, not both")
    try:
        config_path = args.config or (ServiceConfig.desktop_path() if args.desktop else None)
        config = ServiceConfig.load(config_path,
            db_path=args.db_path or (args.state_dir / "agent-home.sqlite3" if args.state_dir else None), port=args.port)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    locks = DatabaseLocks()
    try:
        locks.acquire(config.db_path)
    except BlockingIOError:
        parser.exit(1, "A bridge is already using this database or legacy state directory.\n")
    store = Store(config.db_path)
    LOG.info("Local dashboard: http://127.0.0.1:%s/", config.port)
    if args.desktop:
        # The app owns this process: close it even if the native launcher crashes.
        def watch_launcher():
            sys.stdin.buffer.read()
            os.kill(os.getpid(), signal.SIGTERM)
        threading.Thread(target=watch_launcher, daemon=True).start()
    def report_ready(_):
        print(json.dumps({"event": "ready", "url": f"http://127.0.0.1:{config.port}/"}), flush=True)
    try:
        web.run_app(create_app(store, config, config_path, locks), host="127.0.0.1", port=config.port,
            access_log=None, print=report_ready if args.desktop else None)
    finally:
        locks.close()
