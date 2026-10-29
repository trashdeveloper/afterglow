"""Task B3 (Git-blame context, plan item 13) red-first tests:
agent/evidence/blame_ctx.py.

- For fault lines (file + line list passed by caller), reports the
  last-commit hash + subject touching each line via repo-local git
  (`git blame -L <line>,<line> --porcelain -- <file>`).
- Pure function over an injected runner (default: subprocess git), so
  tests use a FIXTURE repo (tmp git repo with pinned commits), never
  the real repo.
- Anchor test: fixture repo with 2 commits touching a line returns
  the pinned later commit hash + subject; untouched (out-of-range)
  line returns None; non-git dir raises loudly (never invents).
- Default-off: only runs on explicit call (enable_blame=True); the
  existing loop behavior never changes (no loop edits).
- Stdlib-only; no API, no keys.

Run: python3 tests/test_evidence_blame_ctx.py -v   (from repo root)
"""
import ast
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

from evidence import blame_ctx  # noqa: E402  (RED before implementation)


def _git(cwd, *args):
    proc = subprocess.run(
        ["git"] + list(args), cwd=cwd, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError("fixture git %r failed: %s"
                           % (args, proc.stderr[-500:]))
    return proc.stdout.strip()


def _make_fixture_repo():
    """Tmp git repo with 2 pinned commits; returns (tmpdir, first, second).

    a.txt v1: 3 lines. Commit 1 message "first commit".
    Then line 2 rewritten. Commit 2 message "second commit touches line two".
    """
    tmp = tempfile.mkdtemp(prefix="blame_ctx_fixture_")
    _git(tmp, "init", "-q")
    _git(tmp, "config", "user.email", "fixture@example.com")
    _git(tmp, "config", "user.name", "Fixture")
    with open(os.path.join(tmp, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("alpha\nbeta\nGamma\n")
    _git(tmp, "add", "a.txt")
    _git(tmp, "commit", "-q", "-m", "first commit")
    first = _git(tmp, "rev-parse", "HEAD")
    with open(os.path.join(tmp, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("alpha\nBETA-two\nGamma\n")
    _git(tmp, "add", "a.txt")
    _git(tmp, "commit", "-q", "-m", "second commit touches line two")
    second = _git(tmp, "rev-parse", "HEAD")
    assert first != second
    return tmp, first, second


class TestBlameOnFixtureRepo(unittest.TestCase):
    def test_touched_line_returns_pinned_later_commit(self):
        repo, _first, second = _make_fixture_repo()
        got = blame_ctx.blame_lines(repo, "a.txt", [2])
        self.assertIn(2, got)
        entry = got[2]
        self.assertIsNotNone(entry)
        self.assertEqual(entry["commit"], second)
        self.assertEqual(entry["subject"],
                         "second commit touches line two")

    def test_untouched_line_returns_first_commit(self):
        # Line 1 was written by commit 1 and never retouched: blame
        # honestly attributes it to the FIRST commit, not the latest.
        repo, first, _second = _make_fixture_repo()
        got = blame_ctx.blame_lines(repo, "a.txt", [1])
        self.assertEqual(got[1]["commit"], first)
        self.assertEqual(got[1]["subject"], "first commit")

    def test_out_of_range_line_returns_none_never_invents(self):
        repo, _first, _second = _make_fixture_repo()
        got = blame_ctx.blame_lines(repo, "a.txt", [99])
        self.assertIn(99, got)
        self.assertIsNone(got[99])

    def test_mixed_lines_pin_per_line(self):
        repo, first, second = _make_fixture_repo()
        got = blame_ctx.blame_lines(repo, "a.txt", [1, 2, 99])
        self.assertEqual(got[1]["commit"], first)
        self.assertEqual(got[2]["commit"], second)
        self.assertIsNone(got[99])

    def test_non_git_dir_raises_loudly(self):
        # A directory that is not a git repo: refuse, never invent.
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                blame_ctx.blame_lines(tmp, "a.txt", [1])

    def test_missing_repo_root_raises_loudly(self):
        with self.assertRaises(ValueError):
            blame_ctx.blame_lines("/nonexistent-root-xyz", "a.txt", [1])

    def test_bad_line_numbers_raise_loudly(self):
        repo, _first, _second = _make_fixture_repo()
        with self.assertRaises(ValueError):
            blame_ctx.blame_lines(repo, "a.txt", [0])
        with self.assertRaises(ValueError):
            blame_ctx.blame_lines(repo, "a.txt", [-3])


class TestRunnerInjection(unittest.TestCase):
    def test_canned_porcelain_parses_without_subprocess(self):
        # Pure function over the injected runner: a fake runner's
        # canned porcelain output yields the pinned hash + subject
        # with zero git subprocesses spawned.
        calls = []

        class _Proc:
            returncode = 0
            stderr = ""

            def __init__(self, stdout):
                self.stdout = stdout

        porcelain = (
            "abc123abc123abc123abc123abc123abc123ab12 1 2 1\n"
            "author Fixture\n"
            "author-time 1761780000\n"
            "summary canned subject line\n"
            "filename a.txt\n"
            "\tBETA-two\n")
        revparse = "true\n"

        def fake_runner(cmd, cwd):
            calls.append((list(cmd), cwd))
            if cmd[:2] == ["git", "rev-parse"]:
                return _Proc(revparse)
            return _Proc(porcelain)

        with tempfile.TemporaryDirectory() as tmp:
            got = blame_ctx.blame_lines(
                tmp, "a.txt", [2], runner=fake_runner)
        self.assertEqual(
            got[2]["commit"],
            "abc123abc123abc123abc123abc123abc123ab12")
        self.assertEqual(got[2]["subject"], "canned subject line")
        self.assertTrue(any("rev-parse" in c for (c, _w) in calls))
        self.assertTrue(any("blame" in c for (c, _w) in calls))

    def test_blame_failure_for_a_line_yields_none(self):
        class _Proc:
            stderr = "file a.txt has only 3 lines"

            def __init__(self, rc, stdout=""):
                self.returncode = rc
                self.stdout = stdout

        def fake_runner(cmd, cwd):
            if cmd[:2] == ["git", "rev-parse"]:
                return _Proc(0, "true\n")
            return _Proc(128)

        with tempfile.TemporaryDirectory() as tmp:
            got = blame_ctx.blame_lines(
                tmp, "a.txt", [99], runner=fake_runner)
        self.assertIsNone(got[99])

    def test_rev_parse_failure_means_not_a_repo(self):
        class _Proc:
            returncode = 128
            stdout = ""
            stderr = "not a git repository"

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                blame_ctx.blame_lines(
                    tmp, "a.txt", [1],
                    runner=lambda cmd, cwd: _Proc())


class TestDefaultOff(unittest.TestCase):
    def test_pack_defaults_to_disabled_without_touching_disk(self):
        pack = blame_ctx.blame_for_prompt(
            faults=[{"file": "a.txt", "line": 1}],
            repo_root="/nonexistent-root")
        self.assertFalse(pack["blame_enabled"])
        self.assertEqual(pack["blame"], [])
        self.assertEqual(pack["prompt_section"], "")

    def test_enabled_path_needs_faults_loudly(self):
        repo, _first, _second = _make_fixture_repo()
        with self.assertRaises(ValueError):
            blame_ctx.blame_for_prompt(
                faults=[], repo_root=repo, enable_blame=True)

    def test_enabled_path_reports_pinned_commit(self):
        repo, _first, second = _make_fixture_repo()
        pack = blame_ctx.blame_for_prompt(
            faults=[{"file": "a.txt", "line": 2}],
            repo_root=repo, enable_blame=True)
        self.assertTrue(pack["blame_enabled"])
        self.assertEqual(len(pack["blame"]), 1)
        self.assertEqual(pack["blame"][0]["commit"], second)
        self.assertIn(second, pack["prompt_section"])

    def test_loop_untouched_by_evidence_wiring(self):
        # B3 is additive + default-off: loop.py must not import the
        # evidence package (existing loop behavior byte-identical).
        with open(os.path.join(ROOT, "agent", "loop.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        self.assertIsNone(re.search(r"^\s*(import|from)\s+\S*evidence",
                                    src, re.M))

    def test_module_is_stdlib_only(self):
        # No sockets, no HTTP, no API clients: repo-local git only.
        with open(os.path.join(ROOT, "agent", "evidence",
                               "blame_ctx.py"),
                  encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imported.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        forbidden = {"requests", "urllib", "socket", "http",
                     "openai", "anthropic"}
        self.assertTrue(imported.isdisjoint(forbidden),
                        "non-stdlib/API imports: %s"
                        % (imported & forbidden))


if __name__ == "__main__":
    unittest.main()
