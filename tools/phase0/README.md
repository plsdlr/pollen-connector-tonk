# Phase 0: logging Codex CLI and OpenCode

Before the garden gets real adapters for Codex CLI and OpenCode, these
loggers record what each harness actually sends, unchanged. They write
nothing to the garden.

```sh
python3 tools/phase0/install.py <test-project>
```

Then run one real session in each harness in that project: at least one
prompt, a file edit, a shell command, and (in Codex) a subagent. The events
land in `~/.cache/tonk-pollen/raw-codex.jsonl` and `raw-opencode.jsonl`.

What the logs need to answer:

- **Codex:** how `Stop` behaves while a subagent is still working; which
  tools reads and web searches show up as.
- **OpenCode:** which event marks the start of a turn; exactly what
  `tool.execute.after` receives (tool name, `args.filePath`?).

The logs can contain whole files and tool output. Read them, then delete
them. To remove the loggers, delete the project's `.codex/hooks.json` and
`.opencode/plugins/pollen-log.js`.
