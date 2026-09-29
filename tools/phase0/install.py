#!/usr/bin/env python3
"""Install the Phase 0 loggers into a project, for Codex CLI and OpenCode.

    python3 install.py <project> [--force]

Writes <project>/.codex/hooks.json, which runs this folder's log-event.py for
every Codex hook event, and copies pollen-log.js into
<project>/.opencode/plugins/. Both only log raw events to
~/.cache/tonk-pollen/raw-<harness>.jsonl; nothing reaches the garden.

An existing .codex/hooks.json is left alone unless --force: this is meant for
a throwaway test project, not for merging into real hook settings. Codex only
loads project hooks once the project's .codex/ layer is trusted.
"""
import json, os, shlex, shutil, sys

HERE = os.path.dirname(os.path.realpath(__file__))

# Every Codex hook event, so the logs show which ones fire and when.
CODEX_EVENTS = ("SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse",
                "PermissionRequest", "PostToolUse", "PreCompact", "PostCompact",
                "SubagentStart", "SubagentStop", "Stop", "Interrupt")


def main(argv):
    args = [a for a in argv if a != "--force"]
    if len(args) != 1 or not os.path.isdir(args[0]):
        sys.exit(__doc__.strip())
    project, force = os.path.abspath(args[0]), "--force" in argv

    command = (f"{shlex.quote(sys.executable)} -S "
               f"{shlex.quote(os.path.join(HERE, 'log-event.py'))} codex")
    hooks = {"description": "pollen, phase 0: log every Codex hook event to "
                            "~/.cache/tonk-pollen/raw-codex.jsonl (writes nothing to the garden)",
             "hooks": {e: [{"hooks": [{"type": "command", "command": command, "timeout": 5}]}]
                       for e in CODEX_EVENTS}}
    path = os.path.join(project, ".codex", "hooks.json")
    if os.path.exists(path) and not force:
        print(f"skipped {path}: it exists (--force replaces it)")
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(hooks, f, indent=2)
            f.write("\n")
        print(f"codex:    {path} runs {command}")

    plugin = os.path.join(project, ".opencode", "plugins", "pollen-log.js")
    os.makedirs(os.path.dirname(plugin), exist_ok=True)
    shutil.copyfile(os.path.join(HERE, "pollen-log.js"), plugin)
    print(f"opencode: {plugin}")
    print("logs go to ~/.cache/tonk-pollen/raw-codex.jsonl and raw-opencode.jsonl")


if __name__ == "__main__":
    main(sys.argv[1:])
