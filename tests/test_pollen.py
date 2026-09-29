"""Run with `python3 -m unittest discover tests` from the repo root. Standard
library only; nothing here calls tonk."""
import json, os, sys, tempfile, time, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from tonk_pollen import core, install
from tonk_pollen.adapters import claude


class Events(unittest.TestCase):
    def ev(self, **kw):
        return claude.event_of({"session_id": "s1", "cwd": "/w", **kw})

    def test_prompt_opens_a_turn(self):
        line = self.ev(hook_event_name="UserPromptSubmit")
        self.assertTrue(line["turn"])
        self.assertEqual(line["activity"], "thinking")

    def test_edit_touches(self):
        line = self.ev(hook_event_name="PostToolUse", tool_name="Edit", tool_input={"file_path": "/w/a.py"})
        self.assertEqual((line["activity"], line["touch"]), ("coding", "/w/a.py"))

    def test_unmapped_tool_is_not_news(self):
        self.assertIsNone(self.ev(hook_event_name="PostToolUse", tool_name="Bash"))

    def test_stop_with_running_subagent_stays_awake(self):
        line = self.ev(hook_event_name="Stop", background_tasks=[{"status": "running"}])
        self.assertNotIn("scan", line)
        self.assertTrue(line["now"])

    def test_stop_scans_and_sleeps(self):
        line = self.ev(hook_event_name="Stop")
        self.assertEqual((line["activity"], line["scan"]), ("sleeping", True))

    def test_no_session_no_line(self):
        self.assertIsNone(claude.event_of({"hook_event_name": "SessionStart"}))


class Core(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(core.slug("git@github.com:Org/Repo.git"), "github.com/org/repo")
        self.assertEqual(core.slug("https://github.com/org/repo/"), "github.com/org/repo")

    def test_excluded(self):
        for rel in ("Cargo.lock", "requirements-dev.txt", ".github/workflows/ci.yml", "a/b.xcodeproj/x", "x.whl"):
            self.assertTrue(core.excluded(rel), rel)
        for rel in ("src/main.rs", "README.md", "requirements.md"):
            self.assertFalse(core.excluded(rel), rel)

    def test_layout_is_stable(self):
        # Must match the renderer's rules.js; these are the values it draws.
        self.assertEqual(core.SLOTS, 74)
        self.assertEqual(core.spiral(5), [(0, 0), (0, 1), (1, 1), (1, 0), (1, -1)])

    def test_locate_without_git(self):
        saved, core.GIT = core.GIT, None
        try:
            with tempfile.TemporaryDirectory() as d:
                self.assertEqual(core.locate(os.path.join(d, "src", "a.py"), d)[::2],
                                 ("local/" + os.path.basename(d), os.path.join("src", "a.py")))
                self.assertIsNone(core.locate(os.path.join(d, "node_modules", "x.js"), d))
                self.assertIsNone(core.locate("/elsewhere/a.py", d))
        finally:
            core.GIT = saved

    def test_changed_since(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.path.join(d, "old.txt")
            open(old, "w").close()
            os.utime(old, (time.time() - 100,) * 2)
            since = time.time() - 1
            new = os.path.join(d, "new.txt")
            open(new, "w").close()
            os.makedirs(os.path.join(d, ".git"))
            open(os.path.join(d, ".git", "HEAD"), "w").close()
            self.assertEqual(core.changed_since(d, since), [new])
            self.assertEqual(core.changed_since(d, since, told=[new]), [])


class Enable(unittest.TestCase):
    def test_commands_are_recognised(self):
        for c in ("/home/u/.local/bin/pollen hook claude",
                  "'/usr/bin/python3' -S '/opt/my pollen/pollen.py' hook claude",
                  "/home/u/.local/bin/garden hook claude",           # before the rename
                  "'/usr/bin/python3' -S '/opt/my garden/garden.py' hook claude",
                  "python3 -S /x/garden/adapters/claude.py"):
            self.assertTrue(install.is_ours(c), c)
        for c in ("pollen hook codex", "echo claude", "", "unbalanced 'quote"):
            self.assertFalse(install.is_ours(c), c)

    def setUp(self):
        os.environ["TONK_POLLEN_LAUNCHER"] = "/opt/pollen-core-tonk/pollen.py"   # as pollen.py sets it

    def tearDown(self):
        os.environ.pop("TONK_POLLEN_LAUNCHER", None)

    def test_launcher_command(self):
        self.assertEqual(install.pollen_command().split()[1:],
                         ["-S", "/opt/pollen-core-tonk/pollen.py", "hook", "claude"])

    def test_enable_disable_round_trip_keeps_other_hooks(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, ".claude", "settings.local.json")
            os.makedirs(os.path.dirname(path))
            other = {"type": "command", "command": "echo hi"}
            with open(path, "w") as f:
                json.dump({"model": "x", "hooks": {"Stop": [{"hooks": [other]}]}}, f)
            with open(os.devnull, "w") as quiet:
                out, sys.stdout = sys.stdout, quiet
                try:
                    install.enable(d)
                    install.enable(d)                  # twice: replaces, never doubles
                    self.assertEqual(install.ours_in(path), set(install.EVENTS))
                    with open(path) as f:
                        stop = json.load(f)["hooks"]["Stop"]
                    self.assertEqual(sum(install.is_ours(h["command"]) for g in stop for h in g["hooks"]), 1)
                    install.disable(d)
                finally:
                    sys.stdout = out
            with open(path) as f:
                self.assertEqual(json.load(f), {"model": "x", "hooks": {"Stop": [{"hooks": [other]}]}})


if __name__ == "__main__":
    unittest.main()
