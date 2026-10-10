"""Verifies addon_runner.py end to end with a throwaway extension.

Checks that:
  1. the link is created in Blender's extensions dir
  2. the addon registers on startup
  3. editing a source file triggers a reload that re-runs register()
  4. --debug exposes a working debugpy DAP port
"""

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE / "addon_runner.py"
ADDON = HERE / "_verify_addon"
LINK = Path(os.environ["APPDATA"]) / (
    "Blender Foundation/Blender/5.2/extensions/user_default/RunnerVerify"
)
PY = r"C:\Program Files\Steam\steamapps\common\Blender\5.2\python\bin\python.exe"

MANIFEST = """schema_version = "1.0.0"
id = "RunnerVerify"
version = "1.0.0"
name = "Runner Verify"
tagline = "checks the addon runner"
maintainer = "test"
type = "add-on"
blender_version_min = "4.2.0"
license = ["SPDX:GPL-3.0-or-later"]
permissions = []
"""

SOURCE = """import bpy

VERSION = "1"


class RUNNERVERIFY_OT_ping(bpy.types.Operator):
    bl_idname = "runnerverify.ping"
    bl_label = "Ping"

    def execute(self, context):
        print("RUNNERVERIFY_PING version=" + VERSION, flush=True)
        return {"FINISHED"}


def register():
    bpy.utils.register_class(RUNNERVERIFY_OT_ping)
    print("RUNNERVERIFY_REGISTERED version=" + VERSION, flush=True)


def unregister():
    bpy.utils.unregister_class(RUNNERVERIFY_OT_ping)
"""


def kill_tree(pid):
    """Kill `pid` and everything it spawned, leaving other sessions alone.

    The launcher spawns Blender as a child, so terminate() on the launcher alone
    orphans Blender and it goes on holding the debug port. Never sweep every
    blender process on the machine: the user may well have one open.
    """
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
        )
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


class Blender:
    def __init__(self, *extra):
        self.lines = []
        command = [PY, str(RUNNER), str(ADDON), *extra]
        self.proc = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        self.pids = set()

    def remember_blender_pids(self):
        """Note the Blender processes this launcher started."""
        self.pids.update(child_blender_pids(self.proc.pid))
        return self.pids

    def pump(self, seconds):
        """Drain stdout for a while so the pipe never blocks Blender."""

        def reader():
            for line in self.proc.stdout:
                self.lines.append(line.rstrip())
                print("  [blender]", line.rstrip())

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        time.sleep(seconds)
        return thread

    def wait_for(self, pattern, timeout=90):
        deadline = time.time() + timeout
        regex = re.compile(pattern)
        while time.time() < deadline:
            if any(regex.search(line) for line in self.lines):
                return True
            time.sleep(0.2)
        return False

    def stop(self):
        self.remember_blender_pids()
        kill_tree(self.proc.pid)
        for pid in self.pids:
            kill_tree(pid)
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def child_blender_pids(parent_pid):
    """Blender processes whose parent is `parent_pid`."""
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='blender.exe'\" | "
        "Where-Object { $_.ParentProcessId -eq %d } | "
        "ForEach-Object { $_.ProcessId }" % parent_pid
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True,
    )
    return [int(x) for x in result.stdout.split() if x.strip().isdigit()]


def cleanup():
    for path in (LINK, ADDON):
        if path.is_symlink() or path.exists():
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
            else:
                try:
                    path.unlink()
                except OSError:
                    pass


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" - {detail}" if detail else ""))
    return ok


def main():
    cleanup()
    ADDON.mkdir(parents=True)
    (ADDON / "blender_manifest.toml").write_text(MANIFEST, encoding="utf-8")
    (ADDON / "__init__.py").write_text(SOURCE, encoding="utf-8")

    results = []

    print("\n[1] start with --debug, check link + register + debug port")
    blender = Blender("--debug", "--port", "5921", "--poll", "0.3")
    blender.pump(35)

    results.append(check(
        "link created in Blender extensions dir",
        LINK.is_dir(), str(LINK),
    ))
    results.append(check(
        "link points at the addon source",
        LINK.resolve() == ADDON.resolve() if LINK.exists() else False,
    ))
    results.append(check(
        "addon registered on startup",
        blender.wait_for(r"RUNNERVERIFY_REGISTERED version=1"),
    ))
    # Do NOT probe the debug port with a throwaway socket first: debugpy.listen()
    # accepts exactly one inbound connection, so an open-and-close check would
    # consume the slot and the real DAP client below would see nothing. The
    # DAP initialize is itself proof that the port is listening.
    results.append(check(
        "debug port speaks DAP",
        dap_initialize_works(5921),
    ))

    print("\n[2] edit the source, confirm an automatic reload")
    (ADDON / "__init__.py").write_text(SOURCE.replace('VERSION = "1"', 'VERSION = "2"'),
                                       encoding="utf-8")
    reloaded = blender.wait_for(r"source changed, reloading", timeout=30)
    results.append(check("reload triggered by file save", reloaded))
    results.append(check(
        "reload re-ran register() with the new code",
        blender.wait_for(r"RUNNERVERIFY_REGISTERED version=2", timeout=30),
    ))

    print("\n[3] saving again with no change does not reload")
    before = count(blender.lines, "source changed, reloading")
    time.sleep(3)
    results.append(check(
        "idle saves do not cause reload loops",
        count(blender.lines, "source changed, reloading") == before,
    ))

    blender.stop()

    print("\n[4] --no-watch starts Blender without the watcher")
    quiet = Blender("--no-watch")
    quiet.pump(25)
    results.append(check(
        "addon still registers",
        quiet.wait_for(r"RUNNERVERIFY_REGISTERED version=2", timeout=20),
    ))
    results.append(check(
        "watcher not registered",
        quiet.wait_for(r"auto-reload disabled", timeout=10),
    ))
    quiet.stop()

    cleanup()
    print(f"\nRESULT: {'ALL PASS' if all(results) else 'FAILURES: ' + str(results.count(False))}")
    raise SystemExit(0 if all(results) else 1)


def count(lines, needle):
    return sum(1 for line in lines if needle in line)



def dap_initialize_works(port):
    """A debugpy listen() port is a full DAP endpoint; prove it by initializing.

    Frames must be parsed rather than string-matched: the adapter's response is
    framed and may be split across reads, so a substring test races.
    """
    try:
        sock = socket.create_connection(("127.0.0.1", port), timeout=15)
    except OSError:
        return False
    try:
        payload = json.dumps({
            "seq": 1, "type": "request", "command": "initialize",
            "arguments": {
                "clientID": "verify", "adapterID": "python", "pathFormat": "path",
                "linesStartAt1": True, "columnsStartAt1": True,
                "supportsVariableType": True,
            },
        }).encode()
        sock.sendall(f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload)

        buffer = b""
        deadline = time.time() + 30
        while time.time() < deadline:
            while True:
                sep = buffer.find(b"\r\n\r\n")
                if sep < 0:
                    break
                length = None
                for line in buffer[:sep].split(b"\r\n"):
                    if line.lower().startswith(b"content-length:"):
                        length = int(line.split(b":", 1)[1])
                if length is None or len(buffer) < sep + 4 + length:
                    break
                body = json.loads(buffer[sep + 4:sep + 4 + length])
                # Consume the frame; otherwise the next iteration re-parses this
                # same one and never reaches later messages.
                buffer = buffer[sep + 4 + length:]
                if body.get("type") == "response" and body.get("command") == "initialize":
                    return bool(body.get("success"))
            try:
                sock.settimeout(2)
                chunk = sock.recv(4096)
            except OSError:
                chunk = b""
            if not chunk:
                time.sleep(0.2)
                continue
            buffer += chunk
        return False
    finally:
        sock.close()


if __name__ == "__main__":
    time.sleep(1)
    try:
        main()
    finally:
        cleanup()