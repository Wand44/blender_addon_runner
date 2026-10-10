# Blender addon runner

 **AI-coded personal dev tool.** I wrote this with AI for my own use, to make
iterating on Blender addons easier. It is an editor-agnostic code runner: edit
in whatever editor you like, and the running Blender reloads your source.
 It links your folder into
Blender's addon directory, starts Blender with the addon enabled, and reloads it
every time you save a source file.

```
cd C:\path\to\my_addon
python "C:\path\blender_addon_runner\addon_runner.py"
```

No install step, no copying files, no Blender preferences to change. Save in
your editor, and the running Blender picks the change up.

## Requirements

- Blender installed (auto-detected: Steam, `C:\Program Files\Blender Foundation`,
  macOS `/Applications`, or `PATH`)
- Your addon folder must contain either `blender_manifest.toml` (Blender 4.2+
  extension) or `__init__.py` (legacy addon)

Nothing is installed into your Python environment. The runner only uses the
standard library.

## Usage

```
addon_runner.py [addon] [options]
```

`addon` defaults to the current directory, so `cd`-ing into the addon folder and
running the script with no arguments is the normal case.

| Option | Meaning |
| --- | --- |
| `--debug` | Expose a debugpy server on `--port` so a DAP client can attach |
| `--port N` | Debug port, default `5678` |
| `--blender PATH` | Use a specific Blender executable instead of auto-detecting |
| `--no-link` | Assume the addon is already inside Blender's own addon directory |
| `--color` / `--no-color` | Force or suppress coloured output |
| `--replace` | If a real directory sits where the link goes, move it aside and link anyway |
| `--poll SECONDS` | How often to check for file changes, default `0.5` |
| `--no-watch` | Start Blender but do not reload on save |
| `--open FILE` | Also open a `.blend` file |
| `--print-config` | Print what would be launched, then exit |

Examples:

```powershell
# the normal case
cd C:\src\my_addon
python "C:\Users\test\Documents\python\Blender addon runner\addon_runner.py"

# debugging enabled, custom port
python "C:\Users\test\Documents\python\Blender addon runner\addon_runner.py" --debug --port 5678

# check the plan without launching anything
python "C:\Users\test\Documents\python\Blender addon runner\addon_runner.py" --print-config
```

## How the linking works

Blender only loads addons from its own directories, so the runner creates a
directory link from your source folder into the matching location:

- Extensions (with `blender_manifest.toml`) go to
  `%APPDATA%\Blender Foundation\Blender\<version>\extensions\user_default\<id>`
- Legacy addons go to
  `%APPDATA%\Blender Foundation\Blender\<version>\scripts\addons\<folder name>`

On Windows this uses `mklink /J` (a directory junction), which needs neither
Administrator rights nor Developer Mode. Elsewhere it uses a symlink.

Because it is a link, edits land in the same files Blender has open. Nothing is
copied, so nothing can go stale.

The runner refuses to remove a real directory at the link path, since that would
delete your work.

This comes up when Blender's addon folder already holds a copy of the addon —
for example a clone made with `git clone` into the extensions directory, or a
previous manual install. `--replace` handles it:

```powershell
python addon_runner.py --replace
```

It renames the existing directory to `<name>.backup-<timestamp>` and links your
folder in its place. Nothing is deleted, so if the two checkouts turn out to be
different work you still have both. Delete the backup yourself when you're sure.

## Colours

Errors print red and warnings yellow, including the ones Blender itself writes —
its tracebacks and `Warning: 'relit' does not contain '_PT_'` style messages pass
through the runner and are coloured as they stream past.

Colour turns itself off when output is not a terminal, so piping to a file or
running the verify scripts gives clean plain text. Force it either way with
`--color` / `--no-color`, or with the `NO_COLOR` environment variable.

## Reload behaviour

A `bpy.app.timers` callback compares the modification time and size of every
`.py` file under the addon. On a change it waits 0.4s for the editor to finish
writing, then disables the addon, evicts its modules from `sys.modules`, and
re-enables it.

Evicting the modules matters: Blender does not forget an addon's submodules when
you disable it, so a plain disable/enable cycle keeps running the old code and
your changes appear to do nothing.

Reload runs on Blender's main thread, so it is safe for the `bpy` API. If the
addon raises during reload, the traceback is printed and Blender keeps running
with the last working version.

Note that a reload re-runs `register()`. State your addon keeps in module-level
globals is reset, and anything holding a reference to an old class or operator
will be stale.

## Debugging

`--debug` starts a debugpy server inside Blender. That port is a complete DAP
endpoint, so attach a DAP client to it directly.

With Zed, add this to `.zed/debug.json` in the addon folder:

```json
[
  {
    "label": "Blender: Attach",
    "adapter": "Debugpy",
    "request": "attach",
    "tcp_connection": { "host": "127.0.0.1", "port": 5678 },
    "justMyCode": true
  }
]
```

The `port` must match the runner's `--port` (default `5678`).

Use `tcp_connection`, not a `connect` block. `debugpy.listen()` already runs a
full debug adapter on that port; a `connect` block makes the client wait for a
separate debug server that never arrives.

Breakpoints work in addon source. Set one, then trigger the code in Blender.

Requires the debuggee to be running normally, not in background mode: background
Blender does not run `bpy.app.timers`, so the main thread never services the
attach handshake.

## Files

- `addon_runner.py` — the launcher. Runs on your machine; finds Blender, links the
  addon, starts it.
- `blender_addon_runner.py` — runs inside Blender via `--python`. Enables the addon
  and watches for changes.
- `bzr_color.py` — terminal colours. Errors red, warnings yellow, plus a few dim and
  cyan accents.
- `verify.py` — end-to-end check of launch, linking, and auto-reload against a
  throwaway extension. Run it if you change either script.
- `verify_debug.py` — attaches a real DAP client to the `--debug` port, sets a
  breakpoint in addon source, and reads locals. Slower; run it if you change the
  debugging code.