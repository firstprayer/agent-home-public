"""Independent Codex TUI processes; Agent Home never owns their termination."""
import errno
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import select
import shutil
import struct
import subprocess
import sys
import termios
import time

_WORKERS = []


def worker_running(launch):
    """Observe a managed CLI; stored PIDs are never runtime routing addresses."""
    try:
        child = subprocess.run(["ps", "-p", str(int(launch["codex_pid"])), "-o", "ppid=,stat="],
            capture_output=True, text=True, timeout=2).stdout.split()
        if len(child) != 2 or int(child[0]) != launch["worker_pid"] or child[1].startswith("Z"):
            return False
        command = subprocess.run(["ps", "-p", str(int(launch["worker_pid"])), "-o", "args="],
            capture_output=True, text=True, timeout=2).stdout.strip()
        address = f" {launch['folder']} {launch['thread_id']} {launch['socket_path']}"
        return " --sleeve-worker " in command and (
            command.endswith(address) or command.endswith(address + " " + launch["log_path"]))
    except (KeyError, ValueError):
        return False
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("Could not inspect the saved CLI process; retry recovery before starting another process") from None


def locked_worker(log_path):
    descriptor = os.open(log_path.with_suffix(".lock"), os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "r+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            try:
                record = json.load(lock)
                if any(type(record.get(key)) is not int or record[key] <= 0 for key in ("codex_pid", "worker_pid")):
                    raise ValueError("Invalid keeper record")
                os.kill(record["codex_pid"], 0)
                return {**record, "log_path": str(log_path), "already_running": True}
            except (ValueError, KeyError, ProcessLookupError):
                raise RuntimeError("The sleeve process is starting or stopping; retry shortly") from None
    return None


def codex_environment(executable):
    environment = os.environ.copy()
    for key in ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_TOOL_CALL_ID", "CODEX_CI", "PYINSTALLER_RESET_ENVIRONMENT"):
        environment.pop(key, None)
    environment["PATH"] = os.pathsep.join([str(Path(executable).parent),
        environment.get("PATH", os.defpath), "/opt/homebrew/bin", "/usr/local/bin", str(Path.home()/".local/bin")])
    if getattr(sys, "frozen", False):
        # External Codex/tools must not load the frozen Python runtime's libraries.
        for key in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
            original = environment.pop(key + "_ORIG", None)
            if original is None:
                environment.pop(key, None)
            else:
                environment[key] = original
    return environment


def allowed_folder(value, roots):
    if not roots:
        raise ValueError("No sleeve folders are allowed. Configure them in Settings first")
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError("Choose an existing working folder")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("The working folder must be an absolute path")
    try:
        path = path.resolve(strict=True)
        permitted = [Path(root).resolve(strict=True) for root in roots if Path(root).is_dir()]
    except OSError:
        raise ValueError("The working folder is missing or unreadable") from None
    if not path.is_dir():
        raise ValueError("The working folder must be an existing directory")
    if not any(path.is_relative_to(root) for root in permitted):
        raise ValueError("The working folder must be inside an allowed folder (symlinks resolve to their actual location)")
    return path


def codex_executable():
    """Finder does not inherit shell startup files such as nvm's PATH."""
    directories = [str(Path.home()/".local/bin"), "/opt/homebrew/bin", "/usr/local/bin"]
    versions = Path.home()/".nvm/versions/node"
    if versions.is_dir():
        directories += [str(p/"bin") for p in sorted(versions.iterdir(), reverse=True) if p.is_dir()]
    search = os.pathsep.join([os.environ.get("PATH", os.defpath), *directories])
    executable = shutil.which("codex", path=search)
    if not executable:
        raise ValueError("Codex CLI was not found. Install Codex CLI before creating a sleeve")
    return executable


def start_worker(folder, thread_id, socket_path, executable, log_path):
    log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    existing = locked_worker(log_path)
    if existing:
        return existing
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command.append(str(Path(__file__).resolve().parent.parent/"bridge.py"))
    command += ["--sleeve-worker", executable, str(folder), thread_id, socket_path, str(log_path)]
    descriptor = os.open(log_path, os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "ab") as log:
        environment = os.environ.copy()
        if getattr(sys, "frozen", False):
            environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=log, cwd=folder, env=environment, start_new_session=True, close_fds=True)
    _WORKERS[:] = [worker for worker in _WORKERS if worker.poll() is None]
    _WORKERS.append(process)
    try:
        if not select.select([process.stdout], [], [], 15)[0]:
            raise RuntimeError("Codex launch was not confirmed. Check available root agents before retrying")
        line = process.stdout.readline(4096)
        if not line:
            process.wait(timeout=5)
            raise RuntimeError(f"Codex could not start. See its private launch log: {log_path}")
        result = json.loads(line)
        if result.get("status") != "started":
            raise RuntimeError(f"Codex could not start. See its private launch log: {log_path}")
        return {"worker_pid": process.pid, "codex_pid": result["pid"], "log_path": str(log_path)}
    finally:
        process.stdout.close()
        # Popen's Unix reaper handles eventual exit; no app shutdown hook kills it.


def start_daemon(executable):
    environment = codex_environment(executable)
    try:
        completed = subprocess.run([executable, "app-server", "daemon", "start"], env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=20, start_new_session=True)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Codex daemon startup was not confirmed; check Codex CLI and retry recovery") from None
    if completed.returncode:
        raise RuntimeError("Codex's shared daemon could not start. Check Codex CLI configuration and sign-in")


def run_worker(executable, folder, thread_id, socket_path, log_path):
    descriptor = os.open(Path(log_path).with_suffix(".lock"), os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "r+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("A keeper already owns this sleeve; retry recovery shortly") from None
        lock.seek(0)
        lock.truncate()
        lock.flush()
        _run_worker(executable, folder, thread_id, socket_path, lock)


def _run_worker(executable, folder, thread_id, socket_path, lock):
    """Own and drain a PTY independently of the bridge and its native launcher."""
    environment = codex_environment(executable)
    environment["TERM"] = "xterm-256color"
    pid, terminal = pty.fork()
    if pid == 0:
        os.chdir(folder)
        os.execve(executable, [executable, "resume", "--yolo", "--remote", f"unix://{socket_path}",
            "--cd", folder, thread_id], environment)
    fcntl.ioctl(terminal, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    pending = b""
    startup = bytearray()
    acknowledged = False
    began = time.monotonic()
    query = re.compile(rb"\x1b\[6n|\x1b\[c|\x1b\](10|11);\?(?:\x07|\x1b\\)")
    try:
        while True:
            if select.select([terminal], [], [], 0.1)[0]:
                try:
                    data = os.read(terminal, 65536)
                except OSError as error:
                    if error.errno == errno.EIO:
                        break
                    raise
                if not data:
                    break
                if not acknowledged:
                    startup.extend(data[:max(0, 8192-len(startup))])
                pending += data
                for match in query.finditer(pending):
                    request = match.group()
                    response = (b"\x1b[1;1R" if request == b"\x1b[6n" else b"\x1b[?1;2c"
                        if request == b"\x1b[c" else b"\x1b]" + match[1] + b";rgb:ffff/ffff/ffff\x1b\\")
                    os.write(terminal, response)
                # Preserve only an unfinished terminal query across read boundaries.
                last = pending.rfind(b"\x1b")
                pending = pending[last:] if last >= 0 and not query.fullmatch(pending[last:]) and len(pending)-last < 32 else b""
            exited, status = os.waitpid(pid, os.WNOHANG)
            if exited:
                return
            if not acknowledged and time.monotonic()-began >= 1:
                lock.seek(0)
                lock.truncate()
                json.dump({"worker_pid": os.getpid(), "codex_pid": pid}, lock)
                lock.flush()
                print(json.dumps({"status": "started", "pid": pid}), flush=True)
                sys.stdout.close()
                acknowledged = True
    finally:
        if not acknowledged:
            sys.stderr.buffer.write(startup)
        os.close(terminal)
        # This is the dedicated PTY owner's end, never an Agent Home shutdown action.
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
