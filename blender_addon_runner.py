"""Runs inside Blender. Enables the addon and re-enables it when its files change.

Launched by addon_runner.py via `blender --python <this file>`. All settings
arrive through BZR_* environment variables because Blender's argument parser
mangles paths containing spaces.
"""

import os
import sys
import time
import traceback
from pathlib import Path

# bzr_color.py sits next to this file; it is not importable until we say so.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bzr_color as c  # noqa: E402

import bpy  # noqa: E402

ADDON_DIR = Path(os.environ["BZR_ADDON_DIR"])
MODULE = os.environ["BZR_ADDON_MODULE"]
LEGACY = os.environ["BZR_LEGACY"] == "1"
DEBUG_PORT = int(os.environ.get("BZR_DEBUG_PORT", "0"))

POLL_SECONDS = max(0.05, float(os.environ.get("BZR_POLL_SECONDS", "0.5")))
DEBOUNCE_SECONDS = 0.4
WATCH = os.environ.get("BZR_WATCH", "1") == "1"

_state = {"snapshot": None, "changed_at": 0.0}


def log(message):
    print(c.faint("[addon-runner]") + f" {message}", flush=True)


def log_error(message):
    print(c.faint("[addon-runner]") + " " + c.error(message), flush=True)


def addon_name():
    return MODULE if LEGACY else f"bl_ext.user_default.{MODULE}"


def source_snapshot():
    """mtime+size of every .py file under the addon, plus the manifest.

    A dict so a rename/remove is detected as a difference too.
    """
    snapshot = {}
    for path in ADDON_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[str(path)] = (stat.st_mtime_ns, stat.st_size)
    manifest = ADDON_DIR / "blender_manifest.toml"
    if manifest.is_file():
        stat = manifest.stat()
        snapshot[str(manifest)] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def purge_modules():
    """Drop the addon's modules so a re-enable re-reads changed source.

    Disabling an addon does not evict its submodules from sys.modules, so a
    plain disable/enable cycle silently keeps running the old code.
    """
    package = addon_name()
    for name in [
        name for name in sys.modules
        if name == package or name.startswith(package + ".")
    ]:
        del sys.modules[name]


def enable():
    if LEGACY:
        bpy.ops.preferences.addon_refresh()
    else:
        bpy.ops.extensions.repo_refresh_all()
    try:
        bpy.ops.preferences.addon_disable(module=addon_name())
    except Exception:
        pass  # not enabled yet, or already disabled
    purge_modules()
    bpy.ops.preferences.addon_enable(module=addon_name())
    log(c.ok(f"enabled '{addon_name()}'"))


def _poll():
    try:
        snapshot = source_snapshot()

        if _state["snapshot"] is None:
            _state["snapshot"] = snapshot
            return POLL_SECONDS

        if snapshot != _state["snapshot"]:
            # Editors write in bursts; wait for the dust to settle so we reload
            # once with the final contents rather than several times mid-save.
            _state["changed_at"] = time.time()
            _state["snapshot"] = snapshot
            return POLL_SECONDS

        if (
            _state["changed_at"]
            and time.time() - _state["changed_at"] >= DEBOUNCE_SECONDS
        ):
            _state["changed_at"] = 0.0
            log(c.info("source changed, reloading..."))
            try:
                enable()
            except Exception:
                log_error("reload failed:\n" + traceback.format_exc())

        return POLL_SECONDS
    except Exception:
        log_error("watcher error:\n" + traceback.format_exc())
        return 5.0


def start_debugger():
    try:
        import debugpy
    except ImportError:
        log_error("debugpy is not importable inside Blender; skipping debugger")
        return
    debugpy.configure(subProcess=False)
    try:
        debugpy.listen(("127.0.0.1", DEBUG_PORT))
    except Exception as exc:
        log_error(f"could not listen on debug port {DEBUG_PORT}: {exc}")
        return
    log(c.info(f"debugpy listening on 127.0.0.1:{DEBUG_PORT}"))


def main():
    log(f"addon dir : {ADDON_DIR}")
    log(f"module    : {addon_name()}")
    if str(ADDON_DIR) not in sys.path:
        sys.path.insert(0, str(ADDON_DIR))

    if DEBUG_PORT:
        start_debugger()

    try:
        enable()
    except Exception:
        log_error("initial enable failed:\n" + traceback.format_exc())

    if WATCH:
        bpy.app.timers.register(_poll, persistent=True)
        log(f"watching for changes (every {POLL_SECONDS}s). "
            + c.ok("Edit and save to reload."))
    else:
        log(c.warn("auto-reload disabled; re-run this script to pick up changes."))


main()