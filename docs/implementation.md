# Private mode: hashed file and repo names

Plan, 2026-09-29. **Built 2026-10-16**, with these decisions: opt-in at
`enable` (the space's AGENTS.md has the agent offer it), 8-byte hashes
(16 hex chars), and type hints instead of paths in the local flush log too.

## Goal

Some users don't want their repo and file names in a shared space. With
private mode on for a project, Pollen writes **no readable names** for it:
no file paths, no repo name or remote, no folder name. The garden still
draws the same trees in the same places, with the same species by file type.

Opt-in, per project, off by default. The agent offers it while enabling a
project (AGENTS.md step 5).

## What is written today

| record | field | example | used by the renderer for |
|---|---|---|---|
| plant | entity id | `id:github.com/org/repo/src/auth/login.ts` | a stable key only (shade = hash of it) |
| | `path` | `src/auth/login.ts` | species only (`speciesOf` in `renderer/lib/species.js`) |
| | `project`, `by`, `x`, `y`, `growth` | ids, numbers | placement, growth |
| project | entity id | `id:proj-github.com-org-repo` | a stable key only |
| | `name`, `remote` | `repo`, `github.com/org/repo` | nothing (never read) |
| gardener | `name` | `repo·3f9a` (folder + session) | nothing (read, never shown) |
| | `at` | a plant's entity id | which tree the figure walks to |

Everything readable is in fields the renderer doesn't need, except `path`,
and of the path it only needs the **file type**.

## Design

### Where the setting lives: in the hook command

`pollen enable --private` registers the hooks as
`… pollen.py hook claude --private`. The adapter adds `"p": 1` to every spool
line it writes; the flusher treats a session as private when its lines say
so. Per project, because the hooks are per project
(`.claude/settings.local.json`), with no extra config to keep in step.
`pollen enable` without the flag switches a project back.

End-of-turn scan events are made from a session's own lines, so they inherit
the flag.

### The seed

Hashing without a secret can be reversed by hashing likely names
(`README.md`, `src/main.rs`, every path of a public repo). So all hashes are
**HMAC-SHA256 keyed with a seed**:

- `~/.config/tonk-pollen/seed`: 32 random bytes (`secrets.token_bytes`),
  mode 0600, created the first time a project is enabled with `--private`.
- `TONK_POLLEN_SEED` (hex) overrides the file, for teams or CI.
- `pollen seed show` prints it (hex) to copy to your other machines;
  `pollen seed import <hex>` installs one. Same seed, same ids: one garden
  across your machines, or across a team that shares it.
- Without the seed, nobody (members included) can compute a candidate hash.
  With a per-user seed, two people on the same repo get separate plants and
  project roots; a shared team seed merges them again.

Tonk has no CLI primitive to derive or store this (checked 2026-09-29: no
sign/derive/secret commands; the account's X25519 sealing key is used
internally for space seeds only). See "Later".

### What a private session writes

`h(x)` = first 8 bytes of HMAC-SHA256(seed, x), lowercase hex (16 chars).

| field | normal | private |
|---|---|---|
| plant id | `id:<ident>/<rel>` | `id:plant/<h(ident + "\0" + rel)>` |
| plant `path` | `<rel>` | the **type hint** (below) |
| project id | `id:proj-<ident>` | `id:proj-<h(ident)>` |
| project `name`, `remote` | repo folder, remote slug | `h(ident)` (fields are required) |
| gardener `name` | `<folder>·<s4>` | `agent·<s4>` |

`ident` is what `core.locate()` returns today (normalised git remote, or
`local/<folder>`), so the same file on two machines with the same seed is
still one plant.

### The type hint

`speciesOf()` decides the species from the file name, first match wins:
exact name, name prefix, compound extension, last extension. The hint keeps
exactly what it needs and nothing else:

1. **Generic names**, sent as they are (lowercased basename): the names and
   prefixes `species.js` lists —
   `rakefile`; `readme* license* licence* copying* changelog* authors*
   contributing* notice*`; `.gitignore .gitattributes .gitmodules
   .editorconfig .npmrc .nvmrc .prettierrc* .eslintrc* .env .env.*
   .dockerignore codeowners`. For a prefix match only the prefix is kept
   (`readme-internal.md` → `readme`): speciesOf() tries prefixes before
   extensions, so an extension would add nothing.
2. **Known extensions**: `x` + the longest suffix `species.js` knows
   (`x.d.ts`, `x.blade.php`, `x.ts`). Unknown middle parts are dropped
   (`plan.v2.md` → `x.md`).
3. **Anything else**: `x` (species Unknown).

The table of names and extensions is copied into `core.py` from
`species.js`. `tonktest/maintainer/species_parity.py` fails when the two
drift, and runs the real speciesOf() in node to check that sample paths and
their hints give the same species.

Excluded files (build, CI, lock files, binaries) stay excluded before any of
this runs.

## Changes

**pollen-connector-tonk**
- `adapters/claude.py`: accept `--private`, add `"p": 1` to spool lines.
- `cli.py`: pass `--private` through to `hook` and `enable`; new
  `seed show|import`.
- `install.py`: `enable --private` writes the flag into the hook command.
  `is_ours()` must learn it: today it requires the command to *end* with
  `hook claude`, so `hook claude --private` would not be recognised, and
  `enable`/`disable` would leave stale hooks behind.
- `core.py`: `seed()`, `h()`, `type_hint(rel)`, the species table.
- `flush.py`: per session, pick normal or private ids and fields
  (lines 160–215).
- `status.py`: show `private: yes/no` per project, and the seed's
  fingerprint (`h("fingerprint")[:8]`), so two machines can check they match.
- `tests/`: type hints (every rule above), stable ids across runs, no plain
  path or remote anywhere in a private batch (assert on the generated
  notation), `enable --private` round trip.
- `README.md` "What gets shared", `docs/space-agents.md` step 5: offer
  private mode, say what it does and doesn't hide.

**tonktest**
- `maintainer/`: species parity check.
- After the AGENTS.md changes: `setup.py home --space pollen` and
  `tonk space agents set` to update the hosted space.

**No change** to the schema, the renderer or the component: the fields keep
their shape; only their contents change.

## What it does not hide

- **History.** Names written before a project went private stay in the
  space's history. Switching an existing project to private also starts new
  plants (new ids); retracting the old ones hides them from the garden, not
  from history.
- **File types and counts**: how many `.ts` files, which generic files exist.
- **Timing**: when someone works, how often a file changes, which session.
- **Local files**: the flush log names private files by type hint only, but
  `sessions.json` in `~/.cache/tonk-pollen` and the spool still hold real
  paths on the user's own machine, briefly (the spool) or as the session's
  working folder.

## Decided (2026-10-16)

1. Hash length: 8 bytes.
2. Opt-in (`enable --private`), offered by the agent during setup.
3. The flush log names private files by type hint only; a private
   end-of-turn scan logs only a count.

## Test plan

1. Unit tests above, on Python 3.8 and 3.13.
2. `testrepo` enabled with `--private` on `pollen`: edit a README, a `.d.ts`,
   an unknown file; check `tonk query plant --json` has only `x…`/generic
   hints and `id:plant/…` ids, and the renderer draws Docs, TypeScript and
   Unknown species.
3. Second machine with the seed imported: the same file grows the same tree.

## Later

- **Account-sealed seed.** Tonk seals space seeds to the account's X25519
  key so any signed-in device can reopen them (`tonk-cli/src/custody.rs`).
  A way for apps to keep a secret there would give the same seed on every
  device, no copying. Worth asking the Tonk team for; 0.6.17 removed the
  `tonk account` commands altogether (accounts are managed in the
  browser), so it would need a new surface.
