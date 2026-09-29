// pollen-log.js -- Phase 0: record what OpenCode tells a
// plugin, unchanged, to ~/.cache/tonk-pollen/raw-opencode.jsonl (the same
// line format as log-event.py). It writes nothing to the garden and
// never throws, so it can't block or change the agent's work.
//
// Two questions the docs leave open, which this answers:
//   - which event marks the START of a turn (a user message? chat.message?)
//   - exactly what tool.execute.after receives (tool name, args.filePath?)
//
// Install: `python3 install.py <project>` copies it into <project>/.opencode/plugins/
// (or copy it to ~/.config/opencode/plugins/ by hand).
// Events can include whole files and tool output; delete the log after reading.

import { appendFileSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const dir = join(homedir(), ".cache", "tonk-pollen");
const file = join(dir, "raw-opencode.jsonl");

function log(kind, payload) {
  try {
    mkdirSync(dir, { recursive: true });
    const seen = new WeakSet();
    const line = JSON.stringify(
      { harness: "opencode", logged_at: Date.now() / 1000, event: { kind, ...payload } },
      (_, v) => {
        if (typeof v === "object" && v !== null) {
          if (seen.has(v)) return "[circular]";
          seen.add(v);
        }
        return typeof v === "bigint" ? String(v) : v;
      },
    );
    appendFileSync(file, line + "\n");
  } catch {
    // a log must never get in the way
  }
}

export const PollenLog = async ({ directory, worktree }) => {
  log("plugin.loaded", { directory, worktree });
  return {
    // every bus event: session.*, message.*, file.edited, ...
    event: async ({ event }) => log("event", { event }),
    "tool.execute.before": async (input, output) => log("tool.execute.before", { input, output }),
    "tool.execute.after": async (input, output) => log("tool.execute.after", { input, output }),
    // not in the plugin docs' event list; logged if OpenCode calls it
    "chat.message": async (input, output) => log("chat.message", { input, output }),
  };
};
