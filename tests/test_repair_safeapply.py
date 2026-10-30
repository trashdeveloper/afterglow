"""Task G9 (safe auto-apply, plan item 79) tests:
agent/repair/safeapply.py.

Interface (plan text, verbatim):

  Produces: apply_logged(tree, patch, resim_fn) -> {green, rolled_back}
  (RED -> byte-identical rollback; every step appended to a log list).
  Honors D10: real-tree apply requires a sign-off token argument;
  fixture-copy proof requires none.

Interpretation of the plan text (flagged, plan is terse):

- ``tree`` is a directory path (same convention as
  ``agent/repair/lintgate.py``).
- ``patch`` is ``{relpath: new_content}`` (str/bytes full-file
  replacements; every target must already exist as a file).
- ``resim_fn(tree)`` returns truthy on GREEN, falsy on RED.
- ``log`` is a caller-provided list; every step is appended as a
  ``{"step": ...}`` dict (``apply`` -> ``resim`` -> ``rollback``).
- ``signoff_token_path`` is the D10 sign-off token argument: trees
  under the system temp dir are fixture copies (no token needed);
  any other (real) tree without a valid token raises PermissionError;
  a valid token is verified with ``agent/trust/signoff.py``
  (exact ``PINNED_TOKEN`` match, same as D10).

Contract:

- Failing resim triggers rollback: tree bytes identical before/after,
  result ``{"green": False, "rolled_back": True}``, log steps
  ``apply -> resim -> rollback``.
- Passing resim keeps the patch: ``{"green": True,
  "rolled_back": False}``, log steps ``apply -> resim`` (no rollback).
- A resim exception rolls back then propagates (tree never left
  half-patched).
- Vacuous input raises loudly before any verdict: blank tree,
  non-dict/empty patch, non-string relpath, missing target file,
  non-callable resim_fn -- never an invented verdict.
- Real-tree apply without a sign-off token raises PermissionError
  (fail closed, D10 honored); fixture-copy proof requires none.
- Deterministic, stdlib + repo imports only (os + agent/trust/signoff);
  no API, no keys, no network. Default-off: explicit call only;
  agent/loop.py and agent/guard.py untouched (no safeapply wiring).

RED-first TDD: this file was written BEFORE
agent/repair/safeapply.py existed, so the first run must FAIL on the
missing module (proving the test exercises real code, not a stub).

Run: python3 tests/test_repair_safeapply.py -v   (from repo root)
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent.repair import safeapply  # noqa: E402  (RED before implementation)
from agent.trust import signoff  # noqa: E402  D10 token authority


def _make_tree():
    """Create a fixture-copy tree (under the temp dir) with two files."""
    tree = tempfile.mkdtemp(prefix="safeapply-fixture-")
    with open(os.path.join(tree, "a.v"), "w", encoding="utf-8") as handle:
        handle.write("module a;\nwire x;\nendmodule\n")
    with open(os.path.join(tree, "b.v"), "w", encoding="utf-8") as handle:
        handle.write("module b;\nwire y;\nendmodule\n")
    return tree


def _snapshot(tree):
    """Map every file under tree (relpath -> bytes)."""
    snap = {}
    for dirpath, _dirnames, filenames in os.walk(tree):
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, tree)
            with open(full, "rb") as handle:
                snap[rel] = handle.read()
    return snap


PATCH = {"a.v": "module a;\nwire x_fixed;\nendmodule\n"}


class TestRedRollback(unittest.TestCase):
    def test_failing_resim_rolls_back_byte_identical(self):
        tree = _make_tree()
        before = _snapshot(tree)
        log = []
        res = safeapply.apply_logged(tree, PATCH, lambda t: False, log=log)
        self.assertEqual(res, {"green": False, "rolled_back": True})
        self.assertEqual(_snapshot(tree), before)
        self.assertEqual([e["step"] for e in log],
                         ["apply", "resim", "rollback"])

    def test_failing_resim_dict_verdict_rolls_back(self):
        tree = _make_tree()
        before = _snapshot(tree)
        log = []
        res = safeapply.apply_logged(
            tree, PATCH, lambda t: {"passed": False}, log=log)
        self.assertEqual(res, {"green": False, "rolled_back": True})
        self.assertEqual(_snapshot(tree), before)
        self.assertEqual([e["step"] for e in log],
                         ["apply", "resim", "rollback"])


class TestGreenKeepsPatch(unittest.TestCase):
    def test_passing_resim_keeps_patch_no_rollback(self):
        tree = _make_tree()
        log = []
        seen = {}

        def _resim(t):
            seen["tree"] = t
            with open(os.path.join(t, "a.v"), encoding="utf-8") as handle:
                seen["content"] = handle.read()
            return True

        res = safeapply.apply_logged(tree, PATCH, _resim, log=log)
        self.assertEqual(res, {"green": True, "rolled_back": False})
        self.assertEqual(seen["tree"], tree)
        self.assertEqual(seen["content"], PATCH["a.v"])
        with open(os.path.join(tree, "a.v"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), PATCH["a.v"])
        self.assertEqual([e["step"] for e in log], ["apply", "resim"])


class TestResimError(unittest.TestCase):
    def test_resim_exception_rolls_back_and_propagates(self):
        tree = _make_tree()
        before = _snapshot(tree)
        log = []

        def _boom(t):
            raise RuntimeError("sim exploded")

        with self.assertRaises(RuntimeError):
            safeapply.apply_logged(tree, PATCH, _boom, log=log)
        self.assertEqual(_snapshot(tree), before)
        self.assertEqual([e["step"] for e in log],
                         ["apply", "resim", "rollback"])


class TestVacuousInput(unittest.TestCase):
    def test_blank_tree_refused(self):
        for bad in ("", "   ", None, 42):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises((ValueError, TypeError)):
                    safeapply.apply_logged(bad, PATCH, lambda t: True,
                                           log=[])

    def test_bad_patch_refused(self):
        tree = _make_tree()
        for bad in (None, [], "patch", {}, {"a.v": None}, {"": "x"}):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises((ValueError, TypeError,
                                        FileNotFoundError)):
                    safeapply.apply_logged(tree, bad, lambda t: True,
                                           log=[])
        self.assertEqual(
            _snapshot(tree),
            {rel: content for rel, content in _snapshot(tree).items()})

    def test_missing_target_file_refused_tree_untouched(self):
        tree = _make_tree()
        before = _snapshot(tree)
        with self.assertRaises(FileNotFoundError):
            safeapply.apply_logged(tree, {"nope.v": "x"}, lambda t: True,
                                   log=[])
        self.assertEqual(_snapshot(tree), before)

    def test_non_callable_resim_refused(self):
        tree = _make_tree()
        before = _snapshot(tree)
        for bad in (None, "resim", 42):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises((ValueError, TypeError)):
                    safeapply.apply_logged(tree, PATCH, bad, log=[])
        self.assertEqual(_snapshot(tree), before)

    def test_missing_tree_dir_refused(self):
        missing = os.path.join(tempfile.gettempdir(),
                               "safeapply-no-such-dir-%d" % os.getpid())
        with self.assertRaises((ValueError, FileNotFoundError,
                                NotADirectoryError)):
            safeapply.apply_logged(missing, PATCH, lambda t: True, log=[])


class TestSignoffD10(unittest.TestCase):
    def test_fixture_copy_needs_no_token(self):
        tree = _make_tree()  # under the temp dir => fixture copy
        log = []
        res = safeapply.apply_logged(tree, PATCH, lambda t: True, log=log)
        self.assertEqual(res, {"green": True, "rolled_back": False})

    def test_real_tree_without_token_refused(self):
        from unittest import mock
        tree = _make_tree()
        before = _snapshot(tree)
        with mock.patch.object(tempfile, "gettempdir",
                               return_value="/nonexistent-real-root"):
            with self.assertRaises(PermissionError):
                safeapply.apply_logged(tree, PATCH, lambda t: True, log=[])
        self.assertEqual(_snapshot(tree), before)

    def test_real_tree_with_valid_token_proceeds(self):
        from unittest import mock
        tree = _make_tree()
        fd, path = tempfile.mkstemp(prefix="safeapply-signoff-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(signoff.PINNED_TOKEN)
            with mock.patch.object(tempfile, "gettempdir",
                                   return_value="/nonexistent-real-root"):
                log = []
                res = safeapply.apply_logged(
                    tree, PATCH, lambda t: True, log=log,
                    signoff_token_path=path)
            self.assertEqual(res, {"green": True, "rolled_back": False})
            self.assertEqual([e["step"] for e in log], ["apply", "resim"])
        finally:
            os.unlink(path)

    def test_real_tree_with_bad_token_refused(self):
        from unittest import mock
        tree = _make_tree()
        before = _snapshot(tree)
        fd, path = tempfile.mkstemp(prefix="safeapply-signoff-bad-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("tampered-token")
            with mock.patch.object(tempfile, "gettempdir",
                                   return_value="/nonexistent-real-root"):
                with self.assertRaises(PermissionError):
                    safeapply.apply_logged(
                        tree, PATCH, lambda t: True, log=[],
                        signoff_token_path=path)
            self.assertEqual(_snapshot(tree), before)
        finally:
            os.unlink(path)


class TestDefaultOff(unittest.TestCase):
    def test_no_loop_guard_wiring(self):
        for rel in ("agent/loop.py", "agent/guard.py"):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
                text = handle.read()
            self.assertNotIn("safeapply", text,
                             "%s must not wire safeapply" % rel)
            self.assertNotIn("apply_logged", text,
                             "%s must not wire apply_logged" % rel)

    def test_stdlib_and_repo_only(self):
        import ast
        with open(os.path.join(ROOT, "agent", "repair", "safeapply.py"),
                  encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        allowed_stdlib = {"os", "sys", "tempfile", "pathlib"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    self.assertIn(top, allowed_stdlib,
                                  "safeapply imports non-stdlib %r" % alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level and not node.module:
                    continue
                top = (node.module or "").split(".")[0]
                self.assertIn(top, ("agent",) + tuple(allowed_stdlib),
                              "safeapply imports %r" % node.module)


if __name__ == "__main__":
    unittest.main()
