"""`pollen status [DIR]`: check everything the hooks need, for this machine
and one project, and print the garden's URL. Exit 0 when everything that
matters passes, 1 when something blocks the garden. Writes nothing to the
space.
"""
import json, os, re, shlex, shutil, subprocess, sys

from . import __version__, core as g
from .install import EVENTS, is_ours, ours_in, read_json, settings_files

CONCEPTS = ("gardener", "plant", "project", "sheet")


class Report:
    def __init__(self):
        self.failed = False

    def line(self, ok, what, detail=""):
        """ok: True passes, False blocks the garden, None is only a warning."""
        mark = {True: "ok  ", False: "FAIL", None: "warn"}[ok]
        print(f"  {mark} {what}" + (f": {detail}" if detail else ""))
        if ok is False:
            self.failed = True


def tonk(*args):
    p = subprocess.run(["tonk", *args], capture_output=True, text=True, env=g.ENV, timeout=60)
    return p.returncode, p.stdout, p.stderr.strip()


def hook_registrations(project):
    """{settings file: set of events whose command runs the garden}, for the
    three places Claude Code reads hooks from. A command pointing at a file
    that isn't there any more is listed as a broken event."""
    found = {}
    for path in settings_files(project):
        events = ours_in(path)
        if not events:
            continue
        hooks = read_json(path).get("hooks") or {}
        for event in list(events):
            for group in hooks.get(event) or []:
                for h in group.get("hooks") or []:
                    command = h.get("command") or ""
                    if not is_ours(command):
                        continue
                    try:
                        words = shlex.split(command)
                    except ValueError:
                        continue
                    for w in words:
                        if os.sep in w and not os.path.exists(os.path.expanduser(w)):
                            events.add(f"{event} (points at a missing {w})")
        found[path] = events
    return found


def status(project):
    project = os.path.abspath(project)
    r = Report()

    print("machine")
    r.line(True, "pollen", __version__)
    r.line(True, "python", sys.version.split()[0])
    source = "TONK_POLLEN_SPACE" if os.environ.get("TONK_POLLEN_SPACE") else g.CONFIG
    r.line(bool(g.SPACE), "space configured", f"{g.SPACE} (from {source})" if g.SPACE
           else "none: run `pollen setup --space NAME`")
    tonk_bin = shutil.which("tonk")
    r.line(bool(tonk_bin), "tonk CLI", tonk_bin or "not on PATH")
    r.line(True if g.GIT else None, "git", g.GIT or "not installed: projects fall back to their folder")

    url = None
    if tonk_bin and g.SPACE:
        print(f"space {g.SPACE}")
        code, out, err = tonk("space", "--json")
        rows = json.loads(out).get("rows", []) if code == 0 else []
        row = next((x for x in rows if x.get("name") == g.SPACE), None)
        r.line(bool(row), "joined on this machine", row["subject"] if row else
               f"not in `tonk space`; join it with `tonk join <invite-url> --name {g.SPACE}`")
        if row:
            code, out, err = tonk("--space", g.SPACE, "status", "--json")
            st = json.loads(out) if code == 0 else {}
            signed = (st.get("account") or {}).get("signedIn")
            r.line(bool(signed), "signed in", (st.get("account") or {}).get("account", err[:120]))
            state = (st.get("sync") or {}).get("state", "unknown")
            r.line(True if state == "synced" else None, "sync", state)
            missing = []
            for concept in CONCEPTS:
                code, out, err = tonk("--space", g.SPACE, "query", concept, "--json")
                if code != 0:
                    missing.append(concept)
            r.line(not missing, "garden schema", "gardener, plant, project, sheet present" if not missing
                   else f"missing {', '.join(missing)}: the space isn't set up for the garden")
            code, out, err = tonk("--space", g.SPACE, "remote", "--json")
            try:
                remotes = json.loads(out)
                remotes = remotes.get("rows", remotes) if isinstance(remotes, dict) else remotes
                endpoint = next((x.get("endpoint") for x in remotes if x.get("name") == "origin"), None) \
                    or next((x.get("endpoint") for x in remotes), None)
            except (ValueError, AttributeError, TypeError):
                endpoint = None
            if endpoint:
                origin = re.sub(r"/ucan/?$", "", endpoint.rstrip("/"))
                url = f"{origin}/space/{row['subject']}/id:pollen@pollen!ui3d"
            r.line(True if endpoint else None, "remote", endpoint or "none: this space doesn't sync anywhere")

    print(f"project {project}")
    where = g.locate(os.path.join(project, ".pollen-status-probe"), project)
    r.line(bool(where), "plants as", f"id:{where[0]}/… (project {where[1]})" if where
           else "nothing: the folder is skipped (ignored, or under a skipped folder)")
    regs = hook_registrations(project)
    covered = set().union(*regs.values()) if regs else set()
    missing = [e for e in EVENTS if e not in covered]
    broken = sorted(e for e in covered if "missing" in e)
    if not regs:
        r.line(False, "hooks registered", "none: run `pollen enable` in the project")
    else:
        where_from = ", ".join(os.path.relpath(p, project) if p.startswith(project) else p.replace(
            os.path.expanduser("~"), "~") for p in regs)
        r.line(not missing and not broken, "hooks registered",
               f"in {where_from}" + (f"; missing {', '.join(missing)}" if missing else "")
               + (f"; {'; '.join(broken)}" if broken else ""))

    print("last activity")
    log = os.path.join(g.CACHE, "flush.log")
    try:
        with open(log) as f:
            tail = f.read().splitlines()[-200:]
        wrote = [l for l in tail if " wrote " in l]
        bad = [l for l in tail if "failed" in l or "dropped" in l or "no space configured" in l]
        r.line(True if wrote else None, "last write", wrote[-1][:100] if wrote else "none in the log yet")
        r.line(None if bad else True, "recent problems", bad[-1][:120] if bad else "none")
    except OSError:
        r.line(None, "flush log", f"none yet at {log}")

    print()
    if url:
        print(f"garden: {url}")
    print("ready" if not r.failed else "not ready: fix the FAIL lines above")
    return 1 if r.failed else 0
