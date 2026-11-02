"""Docs-vs-reality guide tests: every checkable claim in
docs/GUIDE-vs-REALITY.md must hold against the repo. Red-first: the
guide does not exist yet, so collection of THIS file must fail until
the doc lands (importorskip on the doc path)."""
import os
import re
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUIDE = os.path.join(REPO, "docs", "GUIDE-vs-REALITY.md")


def read_guide():
    with open(GUIDE) as fh:
        return fh.read()


class TestGuideExists(unittest.TestCase):
    def test_guide_exists(self):
        self.assertTrue(os.path.isfile(GUIDE),
                        "docs/GUIDE-vs-REALITY.md must exist")


class TestGuideClaims(unittest.TestCase):
    def test_gallery_path_claim(self):
        body = read_guide()
        m = re.search(r"gallery lives at `([^`]+)`", body)
        self.assertIsNotNone(m, "guide must state the gallery path")
        self.assertTrue(os.path.isfile(os.path.join(REPO, m.group(1))),
                        "claimed gallery path must exist")

    def test_quickstart_command_claim(self):
        body = read_guide()
        m = re.search(r"quickstart runs `([^`]+)`", body)
        self.assertIsNotNone(m, "guide must state the quickstart command")
        cmd = m.group(1).split()
        self.assertTrue(os.path.isfile(os.path.join(REPO, cmd[0])),
                        "quickstart entry point must exist")

    def test_demo_case_claim(self):
        body = read_guide()
        m = re.search(r"demo case: `([^`]+)`", body)
        self.assertIsNotNone(m, "guide must name the demo case")
        self.assertTrue(
            os.path.isfile(os.path.join(REPO, "sim", "targets",
                                        m.group(1) + ".yaml")),
            "demo case spec must exist")
        sys.path.insert(0, os.path.join(REPO, "agent"))
        import doctor
        self.assertEqual(m.group(1), doctor.DEMO_CASE,
                         "guide demo case must equal runtime pick")

    def test_no_mock_claim(self):
        body = read_guide()
        self.assertIn("no mocks", body.lower(),
                      "guide must state the no-mocks rule")
        import subprocess as _sp
        out = _sp.run(
            "cd %s && grep -rInE 'from unittest.mock import|import mock[^.]"
            "|[^_.a-zA-Z]Mock\\(' agent/ --include='*.py' | head -5" % REPO,
            shell=True, capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, "",
                         "non-test agent code must not use mock objects: %s"
                         % out)

    def test_key_handling_claim(self):
        body = read_guide()
        self.assertIn(".env", body,
                      "guide must document .env-based key handling")
        import subprocess as _sp2
        out = _sp2.run(
            "cd %s && git log --all -p -S 'sk-ant-' -- . "
            "':(exclude)tests/test_docs_guide.py' | head -5" % REPO,
            shell=True, capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, "", "no key material in history")

    def test_suite_count_claim(self):
        body = read_guide()
        m = re.search(r"suite: (\d+) tests", body)
        self.assertIsNotNone(m, "guide must state the suite count")
        if os.environ.get("AFTERGLOW_GUIDE_DEPTH"):
            self.skipTest("nested suite run (recursion guard)")
        import subprocess
        env = dict(os.environ, AFTERGLOW_GUIDE_DEPTH="1")
        out = subprocess.run(
            ["python3", "-m", "unittest", "discover", "-s", "tests"],
            capture_output=True, text=True, cwd=REPO, env=env).stderr
        last = [ln for ln in out.splitlines()
                if ln.startswith("Ran ")][-1]
        actual = int(last.split()[1])
        claimed = int(m.group(1))
        self.assertTrue(abs(actual - claimed) <= 25,
                        "suite count %d too far from actual %d"
                        % (claimed, actual))

    def test_locked_board_claim(self):
        body = read_guide()
        self.assertIn("locked 3/3", body,
                      "guide must state the locked-board result")
        gm = re.search(r"full (\d+/\d+)", body)
        self.assertIsNotNone(gm, "guide must state the full-board figure")
        with open(os.path.join(REPO, "eval", "results.md")) as fh:
            results = fh.read()
        rm = re.search(r"full: (\d+/\d+)", results)
        self.assertIsNotNone(rm, "results.md must state full figure")
        self.assertEqual(gm.group(1), rm.group(1),
                         "guide full-board figure must match results.md")

    def test_quarantine_claim(self):
        body = read_guide()
        self.assertIn("quarantine", body.lower(),
                      "guide must document stranger quarantine")
        self.assertTrue(os.path.isfile(
            os.path.join(REPO, "tests", "test_stranger3_quarantine.py"))
            or os.path.isfile(
                os.path.join(REPO, "tests", "test_stranger2_quarantine.py")),
            "at least one quarantine test must exist")

    def test_what_is_not_claim(self):
        body = read_guide()
        self.assertRegex(body, r"(?i)not(\s+\S+){0,6}\s+"
                               r"(yet|done|built|real|finished|product)",
                         "guide must state at least one not-yet boundary")

    def test_commit_hashes_verifiable(self):
        body = read_guide()
        hashes = re.findall(r"\b[0-9a-f]{7,40}\b", body)
        import subprocess
        for h in hashes:
            r = subprocess.run(["git", "cat-file", "-t", h],
                               capture_output=True, text=True, cwd=REPO)
            self.assertEqual(r.stdout.strip(), "commit",
                             "cited hash %s must exist" % h)

    def test_no_absolute_paths(self):
        body = read_guide()
        for ln in body.splitlines():
            if ln.strip().startswith(("http://", "https://")):
                continue
            self.assertNotIn("/home/tuncay", ln,
                             "guide must not hardcode machine paths")

    def test_headings_present(self):
        body = read_guide()
        for h in ("# ", "## "):
            self.assertIn(h, body, "guide needs headings")

    def test_error_paths_genuine(self):
        # The guide's own claims must be checkable, not tautological:
        # at least 3 claims must reference repo paths that exist.
        body = read_guide()
        paths = re.findall(r"`((?:agent|tests|sim|eval|docs|scripts)/[^`]+)`",
                           body)
        self.assertGreaterEqual(len(paths), 3,
                                "guide must cite >=3 repo paths")
        for p in paths:
            full = os.path.join(REPO, p.rstrip("/"))
            self.assertTrue(os.path.exists(full),
                            "cited path must exist: %s" % p)


if __name__ == "__main__":
    unittest.main()
