"""Claude Code hook: note what the session just did, and get out of the way.

Run as `pollen hook claude`, registered by `pollen enable` for SessionStart,
UserPromptSubmit, PostToolUse, Stop and SessionEnd. Claude Code pipes the
event as JSON on stdin.

It does NOT call tonk or git. A tonk call costs 55-440 ms and two at once fail
~25-30% of the time; a hook runs on every tool call. So this appends one line
to a spool file and, if no flusher is running, starts flush.py detached --
which turns the spool into one tonk write.

Cost: ~24 ms a call measured as a bare `python3 -S` script (2026-09-24);
about 11 ms is the interpreter starting and ~12 ms is `import json` (it pulls
in `re`). Hand-parsing the event would save that, and is not worth being
fragile for. No tonk, no git, no subprocess unless a flusher has to be
started -- and nothing else of the package is imported (see cli.py).

Always exits 0 and prints nothing: a garden must never get in the way of the
work it draws.
"""
import fcntl, json, os, sys, time

CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "tonk-pollen")
SPOOL = os.path.join(CACHE, "spool.jsonl")
FLUSHER_LOCK = os.path.join(CACHE, "flusher.lock")
RAW_FLAG = os.path.join(CACHE, "capture-raw")   # touch to log raw events, rm to stop
RAW = os.path.join(CACHE, "raw.jsonl")

# What a tool says the session is doing: renderer REACTIONS names.
ACTIVITY = {
    "Edit": "coding", "MultiEdit": "coding", "NotebookEdit": "coding",
    "Write": "writing",
    "Read": "reading", "Grep": "reading", "Glob": "reading",
    "WebFetch": "research", "WebSearch": "research",
}
TOUCHES = {"Edit", "MultiEdit", "Write", "NotebookEdit"}   # the work: these plant and grow


def event_of(data):
    """The spool line for a hook event, or None if it is nothing to report."""
    kind = data.get("hook_event_name")
    line = {"s": data.get("session_id") or "", "t": round(time.time(), 3),
            "cwd": data.get("cwd") or ""}
    if not line["s"]:
        return None
    if kind == "SessionStart":
        line["spawn"] = True
    elif kind == "UserPromptSubmit":
        line["activity"] = "thinking"
        line["turn"] = True                 # opens the window flush.py scans
    elif kind in ("Stop", "SessionEnd"):
        # A Stop can come while a subagent the turn started is still working
        # (it is listed in background_tasks); the agent isn't asleep then, and
        # its subagent's tool events keep showing the work.
        busy = any(t.get("status") == "running" for t in data.get("background_tasks") or []
                   if isinstance(t, dict))
        if not busy:
            line["activity"] = "sleeping"
            line["scan"] = True             # closes the window: find what Bash changed
        line["now"] = True                  # flush without waiting
    elif kind == "PostToolUse":
        tool = data.get("tool_name") or ""
        activity = ACTIVITY.get(tool)
        if not activity:
            return None                     # a tool we do not map is not news
        line["activity"] = activity
        if tool in TOUCHES:
            inp = data.get("tool_input") or {}
            path = inp.get("file_path") or inp.get("notebook_path")
            if path:
                line["touch"] = path
    else:
        return None
    return line


def start_flusher():
    """Start flush.py unless one is already running. The lock is only probed
    here; flush.py takes it for real."""
    try:
        fd = os.open(FLUSHER_LOCK, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return                          # running; it will pick the line up
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
    import subprocess                       # only when starting one: it is the slow import
    # The package's parent on PYTHONPATH: from a plain checkout (pollen.py)
    # nothing is installed for `-m` to find. -S when we run without site too.
    pkg_parent = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        p for p in (pkg_parent, os.environ.get("PYTHONPATH")) if p))
    subprocess.Popen([sys.executable, *(["-S"] if sys.flags.no_site else []), "-m", "tonk_pollen.flush"],
                     env=env,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=open(os.path.join(CACHE, "flush.log"), "a"),
                     start_new_session=True, close_fds=True)


def capture(data):
    """While RAW_FLAG exists, keep every event exactly as Claude Code sent it,
    to see what a payload carries (e.g. whether a subagent's events say so).
    Off by default; the check is one stat. Write events include the whole
    file content, so this grows fast -- switch it off after a test."""
    if os.path.exists(RAW_FLAG):
        with open(RAW, "a") as f:
            f.write(json.dumps(data) + "\n")


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    os.makedirs(CACHE, exist_ok=True)
    capture(data)
    line = event_of(data)
    if not line:
        return
    # One write of one short line with O_APPEND: lines from sessions running
    # side by side cannot interleave.
    with open(SPOOL, "a") as f:
        f.write(json.dumps(line) + "\n")
    start_flusher()


def run():
    """The hook's entry point: never raises, never prints."""
    try:
        main()
    except Exception:
        pass
