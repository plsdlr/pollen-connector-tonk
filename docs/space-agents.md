# Pollen garden

This space is a Pollen garden: coding agents' work drawn as a 3D garden.
Every file an agent changes in an enabled project becomes a plant, and each
agent session walks the garden as a figure. The view is the `ui3d` facet of
`id:pollen`.

The garden is written by the Pollen hooks, never by hand. Do not assert,
retract or redefine the `pollen`, `gardener`, `plant`, `project` or `sheet`
concepts, the `component` rows or their views. The space owner maintains them.

## Joining the garden with your coding agent

If the user asks you to join or set up the garden, install the Pollen hooks.
Only Claude Code is supported so far. On another harness, say so and stop.

1. **Check the requirements:** `python3 --version` must be 3.8 or newer.
   `tonk` is already working if you are reading this. Git is optional.
2. **Find this space's local name:** the `space:` line of `tonk status`.
   Members choose their own name when joining, so use yours, not the owner's.
3. **Get Pollen:**
   `git clone --depth 1 https://github.com/plsdlr/pollen-connector-tonk ~/.local/share/tonk-pollen/app`.
   If that folder already exists, run `git -C ~/.local/share/tonk-pollen/app pull` instead.
4. **Point it at this space:**
   `python3 ~/.local/share/tonk-pollen/app/pollen.py setup --space <local name>`.
5. **Ask the user which project to enable, and wait for a clear yes.** Tell
   them first what enabling shares: the repo's name or git remote, file paths
   and how often files change, plus the session's folder name and activity.
   No file contents or prompts. Tonk keeps history, so whatever is written
   stays in this space and is visible to every member.
6. **Enable only the project they named:**
   `python3 ~/.local/share/tonk-pollen/app/pollen.py enable <project dir>`.
   This writes hooks to `<project>/.claude/settings.local.json` (git-ignored)
   and nowhere else. Never enable projects the user didn't name, and never add
   the hooks to user-wide settings.
7. **Check it:**
   `python3 ~/.local/share/tonk-pollen/app/pollen.py status <project dir>`.
   Show the user the output. It ends with the garden's URL and `ready`.
8. **Tell the user to restart Claude Code in that project.** Hooks are read
   when a session starts, so the current session won't report.

To leave the garden: `pollen.py disable <project dir>` in each enabled project.

## If something is wrong

`pollen.py status` names each failing check and how to fix it. Writes are
logged to `~/.cache/tonk-pollen/flush.log`. Don't work around a failing
check by writing garden data yourself; report it to the user.
