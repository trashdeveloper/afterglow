"""Sign-off gate tests (Task D10-TRUE, plan item 40, Wave D -- LAST plan item).

`agent/trust/signoff.py` requires an approval token file before GREEN
is finalized: gate_green(bundle, token_path) returns
{signed_off, reason}. Missing/blank/tampered token files block;
a valid token (exact pinned content match) passes; non-dict bundles
are refused loudly. No API.

RED-first TDD: this file was written BEFORE agent/trust/signoff.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 -m unittest tests.test_trust_signoff -v (from repo root)
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.trust import signoff  # noqa: E402  (RED before implementation)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write(content):
    fd, path = tempfile.mkstemp(prefix="signoff-token-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
    except BaseException:
        os.unlink(path)
        raise
    return path


class TestSignoffGate(unittest.TestCase):
    def test_missing_token_blocks(self):
        missing = os.path.join(tempfile.gettempdir(),
                               "afterglow-signoff-missing-%d.tok" % os.getpid())
        if os.path.exists(missing):
            os.unlink(missing)
        res = signoff.gate_green({}, missing)
        self.assertFalse(res["signed_off"])
        self.assertIn("missing", res["reason"].lower())

    def test_none_token_path_blocks(self):
        res = signoff.gate_green({}, None)
        self.assertFalse(res["signed_off"])
        self.assertIn("missing", res["reason"].lower())

    def test_blank_token_blocks(self):
        for blank in ("", "   \n\t  "):
            path = _write(blank)
            try:
                res = signoff.gate_green({}, path)
            finally:
                os.unlink(path)
            with self.subTest(blank=repr(blank)):
                self.assertFalse(res["signed_off"])
                self.assertIn("blank", res["reason"].lower())

    def test_tampered_token_blocks(self):
        good = signoff.PINNED_TOKEN
        for bad in ("forged-token", good + "X", good + " ",
                    " " + good, good.upper() if good.lower() != good else good + "\n"):
            if bad == good:
                continue
            path = _write(bad)
            try:
                res = signoff.gate_green({}, path)
            finally:
                os.unlink(path)
            with self.subTest(bad=repr(bad)):
                self.assertFalse(res["signed_off"])
                reason = res["reason"].lower()
                self.assertTrue(
                    "mismatch" in reason or "tamper" in reason
                    or "invalid" in reason,
                    "tamper reason should name mismatch/tampered/invalid: %r"
                    % res["reason"])

    def test_valid_token_passes(self):
        path = _write(signoff.PINNED_TOKEN)
        try:
            res = signoff.gate_green({}, path)
        finally:
            os.unlink(path)
        self.assertTrue(res["signed_off"])
        self.assertIn("ok", res["reason"].lower())

    def test_directory_token_blocks(self):
        res = signoff.gate_green({}, tempfile.gettempdir())
        self.assertFalse(res["signed_off"])

    def test_result_shape(self):
        path = _write(signoff.PINNED_TOKEN)
        try:
            res = signoff.gate_green({}, path)
        finally:
            os.unlink(path)
        self.assertEqual(set(res), {"signed_off", "reason"})
        self.assertIsInstance(res["signed_off"], bool)
        self.assertIsInstance(res["reason"], str)
        self.assertTrue(res["reason"].strip())


class TestSignoffValidation(unittest.TestCase):
    def test_non_dict_bundle_refused(self):
        path = _write(signoff.PINNED_TOKEN)
        try:
            for bad in (None, [], "bundle", 42, ("a",)):
                with self.subTest(bad=repr(bad)):
                    with self.assertRaises(ValueError):
                        signoff.gate_green(bad, path)
        finally:
            os.unlink(path)

    def test_non_dict_bundle_refused_even_without_token(self):
        for bad in (None, [], "bundle"):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(ValueError):
                    signoff.gate_green(bad, None)

    def test_pinned_token_is_nonblank_str(self):
        self.assertIsInstance(signoff.PINNED_TOKEN, str)
        self.assertTrue(signoff.PINNED_TOKEN.strip())


class TestDefaultOff(unittest.TestCase):
    def test_no_loop_guard_wiring(self):
        for rel in ("agent/loop.py", "agent/guard.py"):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
                text = handle.read()
            self.assertNotIn("signoff", text, "%s must not wire signoff" % rel)
            self.assertNotIn("gate_green", text, "%s must not wire gate_green" % rel)

    def test_no_score_ci_docs_wiring(self):
        for rel in ("eval/score.py", "scripts/ci.sh"):
            path = os.path.join(ROOT, rel)
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertNotIn("signoff", text, "%s must not wire signoff" % rel)
            self.assertNotIn("gate_green", text, "%s must not wire gate_green" % rel)
        docs = os.path.join(ROOT, "docs")
        if os.path.isdir(docs):
            for name in os.listdir(docs):
                path = os.path.join(docs, name)
                if not os.path.isfile(path):
                    continue
                with open(path, encoding="utf-8", errors="ignore") as handle:
                    text = handle.read()
                self.assertNotIn("signoff", text, "docs/%s must not wire signoff" % name)

    def test_stdlib_and_repo_only(self):
        import ast
        with open(os.path.join(ROOT, "agent", "trust", "signoff.py"),
                  encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        allowed_stdlib = {"os", "pathlib", "sys"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    self.assertTrue(
                        top in allowed_stdlib or top == "agent",
                        "unexpected import: %s" % alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = (node.module or "")
                if node.level > 0:
                    continue
                top = mod.split(".")[0]
                self.assertTrue(
                    top in allowed_stdlib or top == "agent",
                    "unexpected from-import: %s" % mod)

    def test_no_api(self):
        with open(os.path.join(ROOT, "agent", "trust", "signoff.py"),
                  encoding="utf-8") as handle:
            src = handle.read()
        for token in ("urllib", "socket", "http.client", "requests",
                      "API_KEY", "api_key"):
            self.assertNotIn(token, src)


if __name__ == "__main__":
    unittest.main()
