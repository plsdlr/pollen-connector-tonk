"""Setting a machine and a project up for the garden.

    setup     writes the space to ~/.config/tonk-pollen/config.json
    enable    registers the Claude Code hooks in <project>/.claude/settings.local.json
    disable   takes them out again

Registering the hooks IS the opt-in: a project without them writes nothing to
the space. settings.local.json is Claude Code's personal, git-ignored layer,
so enabling a project never changes what its repo shares. Other hooks and
settings in the file are left as they are.
"""
import json, os, shlex, shutil, subprocess, sys

from . import core as g

# The Claude Code events the adapter needs (adapters/claude.py), and the tools
# worth a PostToolUse: the ones adapters.claude.ACTIVITY maps.
EVENTS = ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop", "SessionEnd")
MATCHER = "Write|Edit|MultiEdit|NotebookEdit|Read|Grep|Glob|WebFetch|WebSearch"
TIMEOUT = 5


def pollen_command():
    """The command a hook runs, with absolute paths so it never depends on the
    PATH Claude Code's hook shell happens to have.

    From a checkout (pollen.py): this Python, with -S -- the package is
    standard library only, and skipping site is the fastest start. From a
    uv/pipx install: the installed `pollen` script."""
    launcher = os.environ.get("TONK_POLLEN_LAUNCHER")
    if launcher:
        return f"{shlex.quote(sys.executable)} -S {shlex.quote(launcher)} hook claude"
    exe = shutil.which("pollen") or os.path.abspath(sys.argv[0])
    return f"{shlex.quote(exe)} hook claude"


def is_ours(command):
    """Whether a hook command runs the garden's Claude adapter: `pollen hook
    claude` or `python pollen.py hook claude` in any form, the same under its
    earlier name `garden`, or the script it grew out of
    (tonktest/garden/adapters/claude.py, hook.py before 2026-09-28)."""
    try:
        words = shlex.split(command or "")
    except ValueError:
        return False
    names = {os.path.basename(w) for w in words}
    if words[-2:] == ["hook", "claude"] and names & {"pollen", "pollen.py", "pollen.exe", "garden", "garden.py"}:
        return True
    return any(w.endswith(("adapters/claude.py", "/hook.py")) for w in words)


def settings_files(project):
    """The three places Claude Code reads hooks from, most local first."""
    return [os.path.join(project, ".claude", "settings.local.json"),
            os.path.join(project, ".claude", "settings.json"),
            os.path.expanduser("~/.claude/settings.json")]


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def without_ours(hooks):
    """`hooks` with every garden command taken out, and groups and events left
    empty by that dropped. Returns (hooks, how many were removed)."""
    out, removed = {}, 0
    for event, groups in hooks.items():
        kept_groups = []
        for group in groups or []:
            inner = group.get("hooks") or []
            kept = [h for h in inner if not is_ours(h.get("command"))]
            removed += len(inner) - len(kept)
            if kept:
                kept_groups.append({**group, "hooks": kept})
            elif not inner:
                kept_groups.append(group)
        if kept_groups:
            out[event] = kept_groups
    return out, removed


def enable(project):
    path = settings_files(project)[0]
    try:
        data = read_json(path)
    except ValueError as e:
        sys.exit(f"{path} isn't valid JSON ({e}); fix it first, nothing was changed")
    hooks, _ = without_ours(data.get("hooks") or {})      # re-enabling replaces, never doubles
    command = pollen_command()
    for event in EVENTS:
        group = {"hooks": [{"type": "command", "command": command, "timeout": TIMEOUT}]}
        if event == "PostToolUse":
            group = {"matcher": MATCHER, **group}
        hooks.setdefault(event, []).append(group)
    data["hooks"] = hooks
    write_json(path, data)
    print(f"enabled: {os.path.relpath(path, project)} runs `{command}`")
    for p in filter(runs_pollen, settings_files(project)[1:]):
        print(f"note: {p.replace(os.path.expanduser('~'), '~')} also runs the garden; "
              "remove it there or every event is reported twice")
    if not g.SPACE:
        print("no space configured yet: run `pollen setup --space NAME`")
    return 0


def disable(project):
    path = settings_files(project)[0]
    try:
        data = read_json(path)
    except ValueError as e:
        sys.exit(f"{path} isn't valid JSON ({e}); nothing was changed")
    hooks, removed = without_ours(data.get("hooks") or {})
    if removed:
        if hooks:
            data["hooks"] = hooks
        else:
            data.pop("hooks", None)
        write_json(path, data)
    print(f"disabled: removed {removed} hook(s) from {os.path.relpath(path, project)}" if removed
          else "not enabled here: nothing to remove")
    for p in filter(runs_pollen, settings_files(project)[1:]):
        print(f"note: {p.replace(os.path.expanduser('~'), '~')} still runs the garden; "
              "pollen only manages settings.local.json")
    return 0


def ours_in(path):
    """The events whose hooks in a settings file run the garden (an unreadable
    or missing file has none)."""
    try:
        hooks = read_json(path).get("hooks") or {}
    except (OSError, ValueError, AttributeError):
        return set()
    return {event for event, groups in hooks.items() for group in groups or []
            for h in group.get("hooks") or [] if is_ours(h.get("command"))}


def runs_pollen(path):
    return bool(ours_in(path))


def setup(space):
    try:
        config = read_json(g.CONFIG)
    except ValueError:
        config = {}
    config["space"] = space
    write_json(g.CONFIG, config)
    print(f"space: {space} (in {g.CONFIG.replace(os.path.expanduser('~'), '~')})")
    if os.environ.get("TONK_POLLEN_SPACE") not in (None, space):
        print(f"note: TONK_POLLEN_SPACE={os.environ['TONK_POLLEN_SPACE']} is set and wins over the file")
    if not shutil.which("tonk"):
        print("note: `tonk` isn't on the PATH; the garden writes through it")
        return 0
    p = subprocess.run(["tonk", "space", "--json"], capture_output=True, text=True, env=g.ENV)
    try:
        names = {r.get("name") for r in json.loads(p.stdout).get("rows", [])}
    except (ValueError, AttributeError):
        names = set()
    if space not in names:
        print(f"note: no space called {space} on this machine yet; join it with "
              f"`tonk join <invite-url> --name {space}`")
    return 0
