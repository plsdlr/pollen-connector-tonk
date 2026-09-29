#!/usr/bin/env python3
"""Phase 0: record a harness's hook events, unchanged.

    python3 -S log-event.py <harness>     # the event as JSON on stdin

Appends {"harness", "logged_at", "event"} to
~/.cache/tonk-pollen/raw-<harness>.jsonl and nothing else: it writes nothing
to the garden, prints nothing, and always exits 0, so it can never block or
change the agent's work. install.py registers it as a Codex hook; the
OpenCode plugin (pollen-log.js) writes the same file format itself.

Events can include whole file contents and tool output. Delete the log after
reading it.
"""
import json, os, sys, time

CACHE = os.path.expanduser("~/.cache/tonk-pollen")


def main():
    harness = (sys.argv[1] if len(sys.argv) > 1 else "unknown").replace("/", "_")
    raw = sys.stdin.read()
    try:
        event = json.loads(raw)
    except ValueError:
        event = {"unparsed": raw}
    os.makedirs(CACHE, exist_ok=True)
    with open(os.path.join(CACHE, f"raw-{harness}.jsonl"), "a") as f:
        f.write(json.dumps({"harness": harness, "logged_at": round(time.time(), 3), "event": event}) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
