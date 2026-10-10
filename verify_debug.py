"""Attach a real DAP client to the runner's --debug port and set a breakpoint.

Uses the same direct-TCP DAP client that verified the tcp_connection shape
against Blender earlier in this session, so a pass here means the runner's
debug port is genuinely usable, not just open.
"""

import json
import os
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
ADDON = HERE / "_probe_addon"
LINK = Path(r"C:\Users\test\AppData\Roaming\Blender Foundation\Blender\5.2\extensions\user_default\RunnerProbe")
PY = r"C:\Program Files\Steam\steamapps\common\Blender\5.2\python\bin\python.exe"
PORT = 5922

SOURCE_LINES = [
    "import bpy",
    "",
    "MARK = 1",
    "",
    "",
    "class RUNNERPROBE_OT_go(bpy.types.Operator):",
    '    bl_idname = "runnerprobe.go"',
    '    bl_label = "Go"',
    "",
    "    def execute(self, context):",
    "        x = 11",
    "        y = 22",
    "        z = x + y",
    "        self.report({'INFO'}, str(z))",
    "        return {'FINISHED'}",
]
BREAK_LINE = 13  # z = x + y


class Client:
    def __init__(self, host, port):
        self.messages = []
        self.seq = 0
        self._buf = b""
        self.sock = socket.create_connection((host, port), timeout=60)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        while True:
            try:
                chunk = self.sock.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            self._buf += chunk
            while True:
                sep = self._buf.find(b"\r\n\r\n")
                if sep < 0:
                    break
                length = None
                for line in self._buf[:sep].split(b"\r\n"):
                    if line.lower().startswith(b"content-length:"):
                        length = int(line.split(b":", 1)[1])
                if length is None:
                    self._buf = self._buf[sep + 4:]
                    continue
                total = sep + 4 + length
                if len(self._buf) < total:
                    break
                self.messages.append(json.loads(self._buf[sep + 4:total]))
                self._buf = self._buf[total:]

    def send(self, command, arguments=None):
        self.seq += 1
        message = {"seq": self.seq, "type": "request", "command": command}
        if arguments is not None:
            message["arguments"] = arguments
        payload = json.dumps(message).encode()
        self.sock.sendall(f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload)

    def wait(self, predicate, timeout=30, label=""):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for message in list(self.messages):
                if predicate(message):
                    return message
            time.sleep(0.05)
        raise TimeoutError(f"timed out waiting for {label}")


def main():
    ADDON.mkdir(exist_ok=True)
    (ADDON / "blender_manifest.toml").write_text(
        'schema_version = "1.0.0"\nid = "RunnerProbe"\nversion = "1.0.0"\n'
        'name = "Runner Probe"\ntagline = "dap check"\nmaintainer = "test"\n'
        'type = "add-on"\nblender_version_min = "4.2.0"\n'
        'license = ["SPDX:GPL-3.0-or-later"]\npermissions = []\n',
        encoding="utf-8",
    )
    (ADDON / "__init__.py").write_text(
        "\n".join(SOURCE_LINES) + "\n\n\ndef register():\n"
        "    bpy.utils.register_class(RUNNERPROBE_OT_go)\n"
        "    print('RUNNERPROBE_REGISTERED', flush=True)\n\n\n"
        "def unregister():\n"
        "    bpy.utils.unregister_class(RUNNERPROBE_OT_go)\n",
        encoding="utf-8",
    )

    lines = []
    proc = subprocess.Popen(
        [PY, str(RUNNER), str(ADDON), "--debug", "--port", str(PORT)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    threading.Thread(
        target=lambda: [lines.append(x.rstrip()) or print("  [b]", x.rstrip())
                        for x in proc.stdout],
        daemon=True,
    ).start()

    try:
        deadline = time.time() + 90
        connected = None
        while time.time() < deadline:
            try:
                connected = Client("127.0.0.1", PORT)
                break
            except OSError:
                time.sleep(1)
        if connected is None:
            raise SystemExit("never connected to the debug port")

        client = connected
        client.send("initialize", {
            "clientID": "probe", "adapterID": "python", "pathFormat": "path",
            "linesStartAt1": True, "columnsStartAt1": True,
            "supportsVariableType": True,
        })
        print("  initialize ok")

        client.send("attach", {"justMyCode": True})
        client.wait(lambda m: m.get("event") == "initialized", label="initialized")
        client.send("setBreakpoints", {
            "source": {"name": "__init__.py", "path": str(ADDON / "__init__.py")},
            "breakpoints": [{"line": BREAK_LINE}],
        })
        verified = client.wait(
            lambda m: m.get("command") == "setBreakpoints" and m.get("body"),
            label="setBreakpoints",
        )["body"]["breakpoints"][0]
        print(f"  breakpoint line={BREAK_LINE} verified={verified.get('verified')}")

        client.send("configurationDone")
        attach = client.wait(
            lambda m: m.get("type") == "response" and m.get("command") == "attach",
            label="attach",
        )
        print(f"  attach success={attach.get('success')}")

        # Breakpoints only bind if pydevd sees the file, so trigger the operator
        # by rewriting __init__.py through the runner's own reload path.
        (ADDON / "__init__.py").write_text(
            "\n".join(SOURCE_LINES) + "\n\n\ndef register():\n"
            "    bpy.utils.register_class(RUNNERPROBE_OT_go)\n"
            "    print('RUNNERPROBE_REGISTERED', flush=True)\n"
            "    bpy.ops.runnerprobe.go()\n\n\n"
            "def unregister():\n"
            "    bpy.utils.unregister_class(RUNNERPROBE_OT_go)\n",
            encoding="utf-8",
        )
        stopped = client.wait(
            lambda m: m.get("event") == "stopped", timeout=60, label="stopped",
        )
        print(f"  STOPPED reason={stopped['body'].get('reason')}")

        client.send("threads")
        tid = client.wait(
            lambda m: m.get("command") == "threads", label="threads",
        )["body"]["threads"][0]["id"]
        client.send("stackTrace", {"threadId": tid})
        frames = client.wait(
            lambda m: m.get("command") == "stackTrace", label="stackTrace",
        )["body"]["stackFrames"]
        top = frames[0]
        print(f"  top={top.get('name')} @ "
              f"{top.get('source', {}).get('path')}:{top.get('line')}")

        client.send("scopes", {"frameId": top["id"]})
        scopes = client.wait(
            lambda m: m.get("command") == "scopes", label="scopes",
        )["body"]["scopes"]
        locals_scope = next(s for s in scopes if s["name"] == "Locals")
        client.send("variables", {"variablesReference": locals_scope["variablesReference"]})
        variables = client.wait(
            lambda m: m.get("command") == "variables", label="variables",
        )["body"]["variables"]
        print(f"  locals={[(v['name'], v['value']) for v in variables]}")

        print("RESULT: DEBUG PORT USABLE - breakpoint hit and locals readable")
    finally:
        kill_tree(proc.pid)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        for path in (LINK, ADDON):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass


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


if __name__ == "__main__":
    try:
        main()
    except TimeoutError as exc:
        print(f"RESULT: FAILED\n{exc}")
        raise SystemExit(1)