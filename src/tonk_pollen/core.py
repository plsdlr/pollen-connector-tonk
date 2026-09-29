"""The writer's half of the garden: where things go, and how they get into tonk.

Shared by flush.py, status.py and install.py. The renderer's half lives in
the garden component (renderer/lib/rules.js), and the two MUST agree on the
spiral and the project curve -- `python -m tonk_pollen.core --parity` prints
this side for comparison.

Layout (decided 2026-09-24, plan file "Update" section):
  - project roots sit on a sine curve along the stripe, one every ROOT_GAP
    fields of arc length; `project.slot` is the registration order
  - a plant's x/y are its FIELD relative to its project's root, written once
  - every agent of a project grows the same spiral, skipping fields taken by
    anyone (occupancy is global, across projects)
"""
import fcntl, json, math, os, shutil, subprocess, sys

CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "tonk-pollen")
CONFIG = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                      "tonk-pollen", "config.json")
LOCK = os.path.join(CACHE, "tonk-cli.lock")   # every tonk call on this machine takes it
ENV = {**os.environ, "CI": "1"}


def load_config():
    """The machine's Pollen config ({"space": ...}), or {} when there is none."""
    try:
        with open(CONFIG) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# Which tonk space the garden writes to: TONK_POLLEN_SPACE, else the config
# file. No default -- a machine that isn't set up writes nothing, and
# `pollen status` says why.
SPACE = os.environ.get("TONK_POLLEN_SPACE") or load_config().get("space")

# ---- the building rules, as in renderer/lib/rules.js ---------------------

STEP = 5                 # cells per field
COLS, ROWS = 240, 4000   # the stripe, in cells
ORIGIN_X, ORIGIN_Z = COLS // 2, 50
ROOT_GAP = 16            # fields along the curve between project roots
CURVE_AMPLITUDE = 13     # fields
CURVE_WAVELENGTH = 48    # fields
MAX_REACH = 40           # fields from its root a plant may be; beyond is bad data
TURNS = [(0, 1), (1, 0), (0, -1), (-1, 0)]   # (dfx, dfz): +z, +x, -z, -x


def js_round(v):
    """Math.round: halves go up. Python's round() goes to even, which would
    put a root one field away from where the renderer draws it."""
    return math.floor(v + 0.5)


_roots = [(0.0, 0.0)]
_curve = {"fz": 0.0, "fx": 0.0, "walked": 0.0}


def _root_field(slot):
    """The slot-th root on the curve, unrounded (fx, fz). The same walk as
    rules.js rootField(): 0.01-field steps along z, measuring arc length."""
    dz = 0.01
    while len(_roots) <= slot:
        target = len(_roots) * ROOT_GAP
        c = _curve
        while c["walked"] < target:
            fz = c["fz"] + dz
            fx = CURVE_AMPLITUDE * math.sin(2 * math.pi * fz / CURVE_WAVELENGTH)
            c["walked"] += math.hypot(fx - c["fx"], dz)
            c["fz"], c["fx"] = fz, fx
        _roots.append((c["fx"], c["fz"]))
    return _roots[slot]


def root_fields(slot):
    """A project's root in whole fields (fx, fz) from the stripe's origin."""
    fx, fz = _root_field(slot)
    return js_round(fx), js_round(fz)


def _count_slots():
    n = 0
    while ORIGIN_Z + root_fields(n)[1] * STEP < ROWS - ROOT_GAP * STEP:
        n += 1
    return n


SLOTS = _count_slots()   # 74 at the sizes above


def spiral(count):
    """The first `count` fields of the square spiral, as (fx, fz) offsets, in
    planting order -- rules.js spiralFields()."""
    out = [(0, 0)]
    leg = 0
    while len(out) < count:
        dfx, dfz = TURNS[leg % 4]
        for _ in range(leg // 2 + 1):
            fx, fz = out[-1]
            out.append((fx + dfx, fz + dfz))
        leg += 1
    return out[:count]


def slots_of(projects):
    """Each project's slot, as the renderer derives it: stored slots in range
    count; projects without one follow the highest, in id order. Returns
    ({id: slot}, next free slot)."""
    slots, nxt = {}, 0
    for p in projects:
        s = p.get("slot")
        if isinstance(s, int) and 0 <= s < SLOTS:
            slots[p["this"]] = s
            nxt = max(nxt, s + 1)
    for p in sorted((p for p in projects if p["this"] not in slots and p.get("slot") is None),
                    key=lambda p: p["this"]):
        slots[p["this"]] = nxt
        nxt += 1
    return slots, nxt


def as_int(v):
    """signed-integer reads back as a signed STRING ("+0", "-6")."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def occupied(plants, slots):
    """Every field any plant stands on, absolute (fx, fz). Plants with bad data
    are skipped -- the renderer does not draw them either."""
    taken = set()
    for p in plants:
        slot = slots.get(p.get("project"))
        x, y = as_int(p.get("x")), as_int(p.get("y"))
        if slot is None or slot >= SLOTS or x is None or y is None:
            continue
        rfx, rfz = root_fields(slot)
        taken.add((rfx + y, rfz + x))       # stored (x, y) are (fz, fx) offsets
    return taken


def next_field(slot, taken):
    """The first field on the project's spiral nobody has taken, as the stored
    (x, y) offset -- or None if the project is out of reach (full)."""
    rfx, rfz = root_fields(slot)
    for k, (fx, fz) in enumerate(_spiral_iter()):
        if max(abs(fx), abs(fz)) > MAX_REACH:
            return None
        if (rfx + fx, rfz + fz) not in taken:
            taken.add((rfx + fx, rfz + fz))
            return fz, fx


def _spiral_iter():
    fx = fz = 0
    yield fx, fz
    leg = 0
    while True:
        dfx, dfz = TURNS[leg % 4]
        for _ in range(leg // 2 + 1):
            fx, fz = fx + dfx, fz + dfz
            yield fx, fz
        leg += 1


# ---- tonk and git -----------------------------------------------------------

class TonkError(RuntimeError):
    pass


def tonk(*args, stdin=None):
    """One serialized tonk call. Concurrent invocations contend on the shared
    profile and fail outright (~25-30% at two), so every call takes the lock."""
    if not SPACE:
        raise TonkError(f"no space configured: set TONK_POLLEN_SPACE or \"space\" in {CONFIG}")
    os.makedirs(os.path.dirname(LOCK), exist_ok=True)
    with open(LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        p = subprocess.run(["tonk", "--space", SPACE, *args], input=stdin,
                           capture_output=True, text=True, env=ENV)
    if p.returncode:
        raise TonkError(f"tonk {args[0]} failed ({p.returncode}): {p.stderr.strip()[:400]}")
    return p.stdout


def query(concept):
    return json.loads(tonk("query", concept, "--json") or "[]")


# Git, when it is installed and the file is in a repo, gives the best answer;
# without it, the session's folder stands in. Both must work.
GIT = shutil.which("git")


def git(where, *args):
    p = subprocess.run([GIT, "-C", where, *args], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def slug(remote):
    """Normalise a remote URL into host/owner/repo."""
    r = remote[:-4] if remote.endswith(".git") else remote   # str.removesuffix is 3.9+
    r = r.rstrip("/")
    for pre in ("https://", "http://", "ssh://", "git@"):
        if r.startswith(pre):
            r = r[len(pre):]
    return r.replace(":", "/").lower()


# Without git: folders whose files are never the work (tool output,
# dependencies, caches), standing in for .gitignore.
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "target", "dist", "build", "out",
    "__pycache__", ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".next", ".nuxt", ".cache", ".gradle", ".idea", ".vscode",
}


def project_root(cwd):
    """The folder a session's work lives in: the git top level when git is
    there and cwd is in a repo, else cwd itself."""
    if not cwd or not os.path.isdir(cwd):
        return None
    return (git(cwd, "rev-parse", "--show-toplevel") if GIT else None) or os.path.abspath(cwd)


def changed_since(root, since, told=()):
    """Files under `root` modified at or after `since` (epoch seconds), minus
    `told`. Plain stat calls, the same on macOS and Linux; no git, no external
    tools. Folders in SKIP_DIRS are not entered (the caller still runs each
    file through locate(), which applies .gitignore when git is there).

    Some filesystems (HFS+, FAT) keep whole seconds, which would put a file
    written just after `since` before it; a whole-second time is compared
    with `since` rounded down. Nanosecond filesystems (ext4, APFS) compare
    exactly, so the end of the previous turn never leaks in."""
    told = {os.path.abspath(p) for p in told}
    exact, coarse = int(since * 1e9), int(since) * 1_000_000_000
    found = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not os.path.islink(os.path.join(d, x))]
        for f in files:
            p = os.path.join(d, f)
            try:
                m = os.lstat(p).st_mtime_ns
            except OSError:
                continue                    # gone since the listing
            if m >= (coarse if m % 1_000_000_000 == 0 else exact) and p not in told:
                found.append(p)
    return sorted(found)


def locate(path, root):
    """(project id, project name, relative path) for a file, or None when it is
    not one the garden should show.

    With git, when it is installed and the file is inside a repo: the project
    is the normalised `origin` remote (or `local/<repo>` with none -- never an
    invented URL), the path is relative to the repo root, and files inside
    .git or ignored by git are skipped. So the same file on two machines is
    one plant, and the repo's own .gitignore decides what is not the work.

    Without: `root` -- the session's working directory, as Claude Code
    reports it with every event -- is the project, `local/<its folder name>`.
    Files outside it, or under a folder in SKIP_DIRS, are skipped."""
    path = os.path.abspath(path)
    d = os.path.dirname(path)
    top = git(d, "rev-parse", "--show-toplevel") if GIT and os.path.isdir(d) else None
    if top:
        rel = os.path.relpath(path, top)
        if rel.startswith("..") or rel == ".git" or rel.startswith(".git" + os.sep):
            return None
        if subprocess.run([GIT, "-C", top, "check-ignore", "-q", rel]).returncode == 0:
            return None
        remote = git(top, "remote", "get-url", "origin")
        ident = slug(remote) if remote else "local/" + os.path.basename(top)
        return ident, os.path.basename(top), rel

    root = os.path.abspath(root or "")
    if not root or not os.path.isdir(root):
        return None
    rel = os.path.relpath(path, root)
    if rel.startswith("..") or any(part in SKIP_DIRS for part in rel.split(os.sep)[:-1]):
        return None
    name = os.path.basename(root)
    return "local/" + name, name, rel


# ---- files the garden never plants --------------------------------------------
# Build/CI/packaging files and archives/binaries: there are many of them and
# they say little about the work. Matched case-insensitively.

EXCLUDED_NAMES = {n.lower() for n in (
    "Makefile", "GNUmakefile", "CMakeLists.txt", "Dockerfile", "Containerfile",
    "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml",
    "Justfile", "Taskfile.yml", "BUILD", "WORKSPACE", "Jenkinsfile", "Procfile",
    "Cargo.toml", "Cargo.lock", "package.json", "package-lock.json", "yarn.lock",
    "pnpm-lock.yaml", "bun.lockb", "pyproject.toml", "setup.py", "setup.cfg",
    "poetry.lock", "uv.lock", "Pipfile", "go.mod", "go.sum", "Gemfile",
    "Gemfile.lock", "pom.xml", "build.gradle.kts", "settings.gradle.kts",
    "flake.nix", "flake.lock", "mix.exs", "rebar.config", ".gitlab-ci.yml",
)}
EXCLUDED_PREFIXES = ("requirements",)                   # requirements*.txt
EXCLUDED_DIRS = (".github/workflows/", ".circleci/")
EXCLUDED_DIR_SUFFIXES = (".xcodeproj",)                # a directory named *.xcodeproj
EXCLUDED_EXTS = {
    # build, CI & packaging
    ".cmake", ".mk", ".gradle", ".bzl", ".bazel", ".nix", ".dockerfile",
    ".csproj", ".fsproj", ".vbproj", ".sln", ".vcxproj", ".lock", ".gemspec",
    # archives & binaries
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".7z", ".rar",
    ".jar", ".war", ".whl", ".deb", ".rpm", ".dmg", ".iso", ".exe", ".dll",
    ".so", ".dylib", ".a", ".o", ".lib", ".wasm", ".bin", ".class", ".pyc",
}


def excluded(rel):
    """Whether a repo-relative path is one the garden never plants."""
    p = rel.replace(os.sep, "/").lower()
    name = p.rsplit("/", 1)[-1]
    if name in EXCLUDED_NAMES:
        return True
    if name.startswith(EXCLUDED_PREFIXES) and name.endswith(".txt"):
        return True
    if any(f"/{d}" in f"/{p}" for d in EXCLUDED_DIRS):
        return True
    if any(seg.endswith(EXCLUDED_DIR_SUFFIXES) for seg in p.split("/")[:-1]):
        return True
    return os.path.splitext(name)[1] in EXCLUDED_EXTS


def quote(text):
    """A notation string literal."""
    return json.dumps(text)


if __name__ == "__main__" and sys.argv[1:] == ["--parity"]:
    print(json.dumps({"slots": SLOTS,
                      "roots": [[ORIGIN_X + root_fields(s)[0] * STEP, ORIGIN_Z + root_fields(s)[1] * STEP]
                                for s in range(SLOTS)],
                      "spiral": spiral(200)}))
