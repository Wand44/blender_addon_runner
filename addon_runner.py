r"""Run a Blender addon from any folder: launches Blender with the addon linked
in and enabled, then re-enables it every time you save a source file.

Examples:
    cd path\to\my_addon
    python addon_runner.py
    python addon_runner.py --debug --port 5678
    python addon_runner.py --print-config
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import bzr_color as c

HERE = Path(__file__).resolve().parent
IN_BLENDER = HERE / "blender_addon_runner.py"

STEAM_ROOTS = [
    r"C:\Program Files (x86)\Steam\steamapps\common\Blender",
    r"C:\Program Files\Steam\steamapps\common\Blender",
    os.path.expandvars(r"%LOCALAPPDATA%\Steam\steamapps\common\Blender"),
]
STANDARD_DIRS = [
    r"C:\Program Files\Blender Foundation",
    "/Applications",
    os.path.expanduser("~/Applications"),
]


def die(message):
    print(c.error("error: ") + message, file=sys.stderr)
    raise SystemExit(1)


def warn(message):
    print(c.warn("warning: ") + message, file=sys.stderr)


def link_note(message):
    print(c.info("  linked ") + message)


# ---------------------------------------------------------------------------
# Blender discovery
# ---------------------------------------------------------------------------

def detect_blender():
    exe = "blender.exe" if sys.platform == "win32" else "blender"

    for root in STEAM_ROOTS:
        candidate = Path(root) / exe
        if candidate.is_file():
            return candidate

    for parent in STANDARD_DIRS:
        base = Path(parent)
        if not base.is_dir():
            continue
        if "Blender Foundation" in str(base):
            for versioned in sorted(base.iterdir(), reverse=True):
                candidate = versioned / exe
                if candidate.is_file():
                    return candidate
        macos = base / "Blender.app" / "Contents" / "MacOS" / "blender"
        if macos.is_file():
            return macos
        candidate = base / exe
        if candidate.is_file():
            return candidate

    found = shutil.which("blender") or shutil.which("blender.exe")
    return Path(found) if found else None


def blender_version(executable):
    out = subprocess.run(
        [str(executable), "--version"],
        capture_output=True, text=True, timeout=60,
    ).stdout
    first = out.splitlines()[0] if out else ""
    # "Blender 4.2.1 LTS ( ... )"
    parts = first.split()
    for token in parts:
        if token and token[0].isdigit():
            return ".".join(token.split(".")[:2])
    return None


# ---------------------------------------------------------------------------
# Addon discovery
# ---------------------------------------------------------------------------

def read_manifest_id(addon_dir):
    """The `id` field from blender_manifest.toml, or None if this is a legacy addon."""
    manifest = addon_dir / "blender_manifest.toml"
    if not manifest.is_file():
        return None
    for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("id"):
            continue
        _, _, value = line.partition("=")
        value = value.split("#")[0].strip().strip('"').strip("'")
        if value:
            return value
    die(f"{manifest} has no top-level `id` field.")


def looks_like_addon(path):
    return (
        (path / "blender_manifest.toml").is_file()
        or (path / "__init__.py").is_file()
    )


def find_addons_near(root, depth=3, limit=25):
    """Addon folders within `depth` levels of `root`, skipping noise."""
    skip = {
        "node_modules", ".git", ".venv", "venv", "__pycache__", "site-packages",
        "AppData", "Application Data", "Local Settings", "Cookies",
        "History", "SendTo", "Templates", "Recent", "Start Menu",
        "My Documents", "NetHood", "PrintHood", "Documents", "Desktop",
        "Downloads", "Pictures", "Music", "Videos", "OneDrive", "Contacts",
        "Favorites", "Links", "Searches", "3D Objects", "Saved Games",
    }
    found = []
    root = Path(root)
    for current, dirs, _files in os.walk(root):
        here = Path(current)
        if here != root:
            try:
                if here.is_symlink() or not here.is_dir():
                    dirs[:] = []
                    continue
            except OSError:
                dirs[:] = []
                continue
        if looks_like_addon(here):
            found.append(here)
            dirs[:] = []  # don't descend into an addon looking for nested ones
            continue
        depth_left = depth - len(here.relative_to(root).parts)
        if depth_left <= 0:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
    return found[:limit]


# ---------------------------------------------------------------------------
# Linking the addon into Blender
# ---------------------------------------------------------------------------

def user_data_dir(version):
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "Blender Foundation" / "Blender" / version
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Blender" / version
    return Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")) / "blender" / version


def move_aside(path):
    """Rename `path` out of the way, returning the new location.

    Renaming rather than deleting: a duplicate checkout is usually still a git
    repo the user may want, and a timestamped name leaves the addon disabled
    rather than half-removed if something goes wrong.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.backup-{stamp}")
    counter = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.backup-{stamp}-{counter}")
        counter += 1
    shutil.move(str(path), str(backup))
    return backup


def link_target(module, legacy, version):
    base = user_data_dir(version)
    if legacy:
        return base / "scripts" / "addons" / module
    return base / "extensions" / "user_default" / module


def link_addon(addon_dir, target, replace=False):
    """Point Blender's addon directory at our source tree.

    Uses a directory junction on Windows (no admin or Developer Mode needed) and
    a symlink elsewhere.
    """
    try:
        if target.resolve() == addon_dir.resolve():
            link_note("addon already lives in Blender's directory; no link needed")
            return
    except OSError:
        pass

    if target.is_symlink() or target.exists():
        # A real directory here is someone's work - possibly a second checkout of
        # the same addon. With --replace, move it aside rather than deleting it,
        # so nothing is lost if the two turn out to be different.
        if target.is_dir() and not target.is_symlink():
            if not replace:
                die(
                    f"{target} is a real directory, not a link.\n"
                    f"If it is an old or duplicate copy of this addon, re-run with "
                    f"--replace to move it aside and link this folder instead."
                )
            backup = move_aside(target)
            warn(f"moved existing directory aside to {backup}")
        else:
            target.unlink()

    target.parent.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(target), str(addon_dir)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            die(f"could not link {addon_dir} -> {target}:\n{result.stdout}{result.stderr}")
    else:
        target.symlink_to(addon_dir, target_is_directory=True)

    link_note(f"{addon_dir} -> {target}")


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------

def stream_process_output(process):
    """Relay Blender's output, colouring errors and warnings.

    Blender writes its diagnostics to stdout alongside normal output, so relay
    the whole stream rather than inheriting it - otherwise a piped run loses
    the messages that matter.
    """
    try:
        for line in process.stdout:
            sys.stdout.write(c.highlight(line))
            sys.stdout.flush()
    except (BrokenPipeError, OSError):
        pass  # our own output was piped into something that stopped reading
    finally:
        process.stdout.close()


def build_env(addon_dir, module, legacy, port, poll, watch):
    env = dict(os.environ)
    env.update({
        "BZR_ADDON_DIR": str(addon_dir),
        "BZR_ADDON_MODULE": module,
        "BZR_LEGACY": "1" if legacy else "0",
        "BZR_DEBUG_PORT": str(port),
        "BZR_WATCH": "1" if watch else "0",
        "BZR_POLL_SECONDS": str(poll),
        # Blender's stdout is a pipe back to us, so it cannot detect the terminal.
        "BZR_COLOR": "1" if c.ENABLED else "0",
    })
    return env


def main():
    parser = argparse.ArgumentParser(
        description="Run a Blender addon with auto-reload.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:")[1],
    )
    parser.add_argument("addon", nargs="?", default=".", help="addon folder (default: cwd)")
    parser.add_argument("--debug", action="store_true", help="expose a debugpy server")
    parser.add_argument("--port", type=int, default=5678, help="debug port (default 5678)")
    parser.add_argument("--blender", help="path to the Blender executable")
    parser.add_argument("--no-link", action="store_true", help="skip creating the link")

    color = parser.add_mutually_exclusive_group()
    color.add_argument("--color", dest="color", action="store_true", default=None,
                       help="force coloured output")
    color.add_argument("--no-color", dest="color", action="store_false",
                       help="disable coloured output")
    parser.add_argument(
        "--replace", action="store_true",
        help="if a real directory (not a link) is in the way, move it aside to "
             "<name>.backup-<timestamp> and link this folder instead",
    )
    parser.add_argument("--poll", type=float, default=0.5, help="watch interval in seconds")
    parser.add_argument("--no-watch", action="store_true", help="do not reload on save")
    parser.add_argument("--open", dest="blend", help="a .blend file to open")
    parser.add_argument("--print-config", action="store_true", help="print the plan and exit")
    args = parser.parse_args()
    c.override(args.color)

    addon_dir = Path(args.addon).expanduser().resolve()
    if not addon_dir.is_dir():
        die(f"{addon_dir} is not a directory.")
    if not looks_like_addon(addon_dir):
        nearby = find_addons_near(addon_dir)
        message = (
            f"{addon_dir} does not look like a Blender addon.\n"
            f"An addon folder contains blender_manifest.toml (Blender 4.2+ extension) "
            f"or __init__.py (legacy addon)."
        )
        if nearby:
            message += "\n\nFound nearby - pass one as the first argument:\n"
            for path in nearby:
                message += f"  python addon_runner.py \"{path}\"\n"
        else:
            message += (
                f"\n\nNothing addon-shaped found within 3 levels of {addon_dir}.\n"
                f"cd into your addon folder first, or pass its path as the first "
                f"argument."
            )
        die(message)

    executable = Path(args.blender).expanduser() if args.blender else detect_blender()
    if not executable or not executable.is_file():
        die(
            "could not find a Blender executable.\n"
            "Install Blender or pass --blender /path/to/blender"
        )

    module = read_manifest_id(addon_dir)
    legacy = module is None
    if legacy:
        module = addon_dir.name

    version = blender_version(executable)
    if not version:
        die(f"could not determine the version of {executable}.")

    target = link_target(module, legacy, version)

    print(f"{c.faint('blender')}  : {executable} ({c.bold(version)})")
    print(f"{c.faint('addon')}    : {addon_dir}")
    print(f"{c.faint('module')}   : {c.bold(module)}"
          + (c.faint("  (legacy addon)") if legacy else c.faint("  (extension)")))
    print(f"{c.faint('debug')}    : "
          + (c.info(f"port {args.port}") if args.debug else c.faint("off")))
    print(f"{c.faint('reload')}   : "
          + (c.faint("disabled") if args.no_watch else c.ok("on save")))

    if not args.no_link:
        link_addon(addon_dir, target, replace=args.replace)

    command = [str(executable), "--python", str(IN_BLENDER)]
    if args.blend:
        command.append(str(Path(args.blend).expanduser()))

    if args.print_config:
        print("\n" + c.faint("command  : ") + " ".join(command))
        print(c.faint("env      :"))
        for key, value in sorted(build_env(
            addon_dir, module, legacy, args.port, args.poll, not args.no_watch
        ).items()):
            if key.startswith("BZR_"):
                print(f"  {key}={value}")
        return

    env = build_env(
        addon_dir, module, legacy,
        args.port if args.debug else 0,
        args.poll, not args.no_watch,
    )

    process = subprocess.Popen(
        command, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    print(f"\n{c.ok('Blender running')} (pid {process.pid}). "
          f"Ctrl+C here stops watching, not Blender.")
    try:
        stream_process_output(process)
        process.wait()
    except KeyboardInterrupt:
        print("\n" + c.warn("stopping Blender..."))
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    main()