"""Turn the hooks' spool into tonk writes: one batch, one document, one commit.

Started detached by an adapter (`python -m tonk_pollen.flush`); only one runs at a time (a non-blocking flock).
It waits DEBOUNCE for a burst of tool calls to finish, drains the spool,
reads what it needs from tonk once, and writes everything in ONE eval with
sync, so the browser sees it. Exits after IDLE seconds with nothing to do.

What a batch writes (the garden schema, set up by the space owner):
  gardener   id:agent/<session>: created on first sight (colour = the next
             palette index); activity when it changes; seen when anything
             else is written, or every SEEN_EVERY seconds at most
  project    id:proj-<slug>: created with the next free slot on the curve; an
             older project without a slot gets the one the renderer already
             gives it, written down so it can never shift
  plant      id:<project>/<path>: a new file takes the first free field on its
             project's spiral (anyone's plant blocks it) and growth 1; each
             further touch adds 1 to growth. `project` always -- a plant
             without it is stored but matches no query. Build/CI files and
             binaries (core.excluded) are never planted. Files
             changed through Bash are found at the end of the turn by
             scan_turns() and count as one touch each.

Log: ~/.cache/tonk-pollen/flush.log.
"""
import fcntl, json, os, sys, time
from collections import OrderedDict

from . import core as g

CACHE = g.CACHE
SPOOL = os.path.join(CACHE, "spool.jsonl")
FLUSHER_LOCK = os.path.join(CACHE, "flusher.lock")
STATE = os.path.join(CACHE, "sessions.json")

DEBOUNCE = 0.4      # seconds to let a burst of tool calls land in one batch
IDLE = 3.0          # seconds with nothing to do before exiting
SEEN_EVERY = 300    # write `seen` at most this often when nothing else changes
PALETTE = 8         # renderer AGENT_COLORS
RETRIES = 3         # a batch that fails to write is put back this many times


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, file=sys.stderr, flush=True)


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE)


def drain():
    """Take everything in the spool. Renaming first means hooks keep appending
    to a fresh file while this one is read; the short pause lets a hook that
    opened the old file just before the rename finish its line."""
    if not os.path.exists(SPOOL) or os.path.getsize(SPOOL) == 0:
        return []
    work = f"{SPOOL}.{os.getpid()}"
    os.replace(SPOOL, work)
    time.sleep(0.05)
    lines = []
    with open(work) as f:
        for raw in f:
            try:
                lines.append(json.loads(raw))
            except ValueError:
                pass
    os.remove(work)
    return lines


def requeue(lines):
    """Put a failed batch back, counting attempts, dropping it after RETRIES."""
    keep = [dict(l, retry=l.get("retry", 0) + 1) for l in lines if l.get("retry", 0) + 1 < RETRIES]
    if len(keep) < len(lines):
        log(f"dropping {len(lines) - len(keep)} event(s) after {RETRIES} failed writes")
    with open(SPOOL, "a") as f:
        for l in keep:
            f.write(json.dumps(l) + "\n")


def gardener_doc(fields):
    return "gardener!:\n" + "".join(f"  {k}: {v}\n" for k, v in fields.items())


def scan_turns(sessions, state):
    """Files changed where the hooks can't see -- through Bash (heredocs,
    sed -i, codegen, mv), which Claude uses for files in practice. A turn
    opens a window at its prompt (`turn`) and a Stop with nothing left running
    in the background closes it (`scan`): every file under the project root
    modified inside the window, and not already reported by the file tools,
    is added to the session's events as a touch. Modification times only, so
    it works with git and without (core.changed_since).

    Anything else that changed files during the turn -- an editor, a
    formatter, another session in the folder -- is counted as this session's
    work. Deletions aren't seen.

    Returns {session: window} to keep once the batch is written; a batch that
    fails is requeued with its window still open."""
    after = {}
    for s, evs in sessions.items():
        w = dict(state.get(s, {}).get("window") or {})
        extra = []
        for e in evs:
            if e.get("turn") and "since" not in w:
                w = {"since": e["t"], "told": []}
            if "touch" in e and "since" in w:
                w["told"].append(os.path.abspath(e["touch"]))
            if e.get("scan") and "since" in w:
                root = g.project_root(e.get("cwd"))
                if root:
                    t0 = time.time()
                    found = g.changed_since(root, w["since"], w["told"])
                    if found:
                        rels = [os.path.relpath(p, root) for p in found]
                        log(f"scan {os.path.basename(root)}: {len(found)} changed outside the hooks "
                            f"({(time.time() - t0) * 1000:.0f} ms): "
                            + ", ".join(rels[:8]) + (" …" if len(rels) > 8 else ""))
                    extra += [{"s": s, "t": e["t"], "cwd": e.get("cwd"), "touch": p} for p in found]
                w = {}
        evs.extend(extra)
        after[s] = w
    return after


def process(lines, state):
    sessions = OrderedDict()
    for l in lines:
        sessions.setdefault(l["s"], []).append(l)
    windows = scan_turns(sessions, state)
    touches = any("touch" in e for evs in sessions.values() for e in evs)
    unknown = [s for s in sessions if not state.get(s, {}).get("known")]

    gardeners = g.query("gardener") if unknown else []
    have_gardener = {r["this"] for r in gardeners}
    if touches:
        projects = g.query("project")
        plants = {p["this"]: p for p in g.query("plant")}
        slots, next_slot = g.slots_of(projects)
        taken = g.occupied(plants.values(), slots)
        known_projects = {p["this"]: p for p in projects}

    doc = []
    now = time.time()
    written_projects = set()
    for s, evs in sessions.items():
        agent = f"id:agent/{s}"
        st = state.setdefault(s, {})
        fields = OrderedDict()
        cwd = next((e["cwd"] for e in evs if e.get("cwd")), "")   # the session's project root
        if not st.get("known") and agent not in have_gardener:
            fields.update(this=agent, name=g.quote(f"{os.path.basename(cwd) or 'agent'}·{s[:4]}"),
                          rx=0, ry=0, color=len(have_gardener) % PALETTE)
            have_gardener.add(agent)
            log(f"new gardener {agent} colour {fields['color']}")
        st["known"] = True

        # The work: every touched file, in a fixed order so a batch lands the
        # same way whatever order the tools ran in.
        counts = OrderedDict()
        for e in evs:
            if "touch" in e:
                counts[e["touch"]] = counts.get(e["touch"], 0) + 1
        at = None
        for path in sorted(counts):
            where = g.locate(path, cwd)
            if not where:
                continue
            ident, name, rel = where
            if g.excluded(rel):
                # build/CI files and binaries are never planted, nor grown
                log(f"skip {rel}: excluded (build/CI or binary)")
                continue
            pid = "id:proj-" + ident.replace("/", "-")
            key = f"id:{ident}/{rel}"
            n = counts[path]
            if key in plants:
                p = plants[key]
                p["growth"] = (g.as_int(p.get("growth")) or 1) + n
                doc.append(f"plant!:\n  this: {key}\n  growth: {p['growth']}\n")
                at = key
                continue
            if pid not in slots:
                slots[pid], next_slot = next_slot, next_slot + 1
            stored = known_projects.get(pid, {}).get("slot")
            valid = isinstance(stored, int) and 0 <= stored < g.SLOTS
            if pid not in written_projects and not valid:
                # new; or an older project the renderer slots by derivation
                # (write it down so later projects cannot shift it); or one
                # with a bad slot (the renderer would not draw it at all)
                doc.append(f"project!:\n  this: {pid}\n  name: {g.quote(name)}\n"
                           f"  remote: {g.quote(ident)}\n  slot: {slots[pid]}\n")
                written_projects.add(pid)
                known_projects[pid] = {"this": pid, "slot": slots[pid]}
            if slots[pid] >= g.SLOTS:
                log(f"no room on the stripe for {pid} (slot {slots[pid]}); {rel} not planted")
                continue
            spot = g.next_field(slots[pid], taken)
            if spot is None:
                log(f"{pid} is full ({g.MAX_REACH} fields out); {rel} not planted")
                continue
            x, y = spot
            doc.append(f"plant!:\n  this: {key}\n  project: {pid}\n  path: {g.quote(rel)}\n"
                       f"  x: {x}\n  y: {y}\n  by: {agent}\n  growth: {n}\n")
            plants[key] = {"this": key, "x": x, "y": y, "growth": n, "project": pid}
            at = key
            log(f"plant {key} at field ({x}, {y}) of slot {slots[pid]}")

        activity = next((e["activity"] for e in reversed(evs) if "activity" in e), None)
        if activity and activity != st.get("activity"):
            fields["activity"] = g.quote(activity)
            st["activity"] = activity
        if at:
            fields["at"] = at
        if fields or now - st.get("seen", 0) > SEEN_EVERY:
            fields["seen"] = f"{now:.3f}"
            st["seen"] = now
        if fields:
            fields.setdefault("this", agent)
            fields.move_to_end("this", last=False)
            doc.append(gardener_doc(fields))

    if doc:
        out = g.tonk("eval", "-", stdin="\n".join(doc))
        rev = next((l for l in out.splitlines() if l.startswith("revision-after")), "")
        log(f"wrote {len(doc)} expression(s) for {len(sessions)} session(s) {rev}")
    for s, w in windows.items():
        state.setdefault(s, {})["window"] = w


def run():
    os.makedirs(CACHE, exist_ok=True)
    fd = os.open(FLUSHER_LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False                      # another flusher has it
    try:
        if not g.SPACE:
            # Not set up: drop what the hooks spooled rather than retry it forever.
            lines = drain()
            if lines:
                log(f"no space configured ({len(lines)} event(s) dropped): "
                    f"set TONK_POLLEN_SPACE or \"space\" in {g.CONFIG}; see `pollen status`")
            return True
        state = load_state()
        idle_since = time.time()
        while True:
            lines = drain()
            if lines:
                if not any(l.get("now") for l in lines):
                    time.sleep(DEBOUNCE)
                    lines += drain()
                try:
                    process(lines, state)
                    save_state(state)
                except g.TonkError as e:
                    log(f"write failed, requeued: {e}")
                    requeue(lines)
                    time.sleep(1)
                idle_since = time.time()
            elif time.time() - idle_since > IDLE:
                break
            else:
                time.sleep(0.15)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    return True


if __name__ == "__main__":
    # A hook that appended just as this flusher was leaving saw the lock held
    # and started nothing -- so look once more after letting go.
    while run() and os.path.exists(SPOOL) and os.path.getsize(SPOOL) > 0:
        pass
