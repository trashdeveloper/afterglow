"""Task 2 tests: per-project guard policy (afterglow_core.policy.check_diff).

Run: python3 tests/test_policy.py -v   (from repo root)

check_diff(diff_text, spec, rtl_root) enforces the same rules as
agent/guard.py:check (allowlist subset, <=40 lines, whitespace/binary
rejection, forbidden patterns, per-file lint) with allowlist/lint taken
from the per-project CaseSpec, plus fail-closed on empty oracle_pass.
"""
import os
import re
import sys
import tempfile
import tokenize
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from afterglow_core import CaseSpec, decide_verdict
from afterglow_core.policy import check_diff

THIS_DIR = os.path.dirname(os.path.abspath(__file__))

GOOD_V = (
    "module simpleuart(input clk, output tx);\n"
    "assign tx = 1'b1;\n"
    "endmodule\n"
)


def _spec(**overrides):
    fields = {
        "case_id": "uart-tx-div",
        "project": "picosoc",
        "files": ["simpleuart.v"],
        "oracle_pass": "tohost: PASS",
        "oracle_trap": "TRAP",
        "budget_s": 600,
        "allowlist": ["simpleuart.v"],
        "lint_cmd": "",
    }
    fields.update(overrides)
    return CaseSpec(**fields)


def _diff(path="simpleuart.v", old="assign tx = 1'b0;", new="assign tx = 1'b1;"):
    return (
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,3 +1,3 @@\n"
        " module simpleuart(input clk, output tx);\n"
        f"-{old}\n"
        f"+{new}\n"
        " endmodule\n"
    )


def _rtl_root(files=None):
    tmp = tempfile.TemporaryDirectory()
    for name, content in (files or {"simpleuart.v": GOOD_V}).items():
        with open(os.path.join(tmp.name, name), "w", encoding="utf-8") as fh:
            fh.write(content)
    tmp_files = tmp  # keep handle alive via addCleanup in caller
    return tmp_files


class TestPolicy(unittest.TestCase):
    def test_custom_oracle_markers(self):
        # A foreign project's log uses its own marker; the spec-declared
        # oracle_pass decides the Verdict, not the exit-code-looking text.
        # NOTE: `pass` is read via getattr (keyword, see types.Verdict).
        spec = _spec(oracle_pass="RESULT: OK")
        good_log = "run finished, exit code 0\nRESULT: OK\ncycles: 42\n"
        bad_log = "run finished, exit code 0\nRESULT: FAIL\ncycles: 42\n"
        good = decide_verdict(good_log, spec.oracle_pass)
        bad = decide_verdict(bad_log, spec.oracle_pass)
        self.assertIs(getattr(good, "pass"), True)
        self.assertIs(getattr(bad, "pass"), False)
        self.assertIsInstance(good.evidence, dict)

    def test_allowlist_enforced(self):
        # A diff touching a non-allowlisted file must be rejected.
        tmp = _rtl_root()
        self.addCleanup(tmp.cleanup)
        spec = _spec(allowlist=["simpleuart.v"])
        ok, reasons, _info = check_diff(_diff(path="evil.v"), spec, tmp.name)
        self.assertIs(ok, False)
        self.assertTrue(
            any("allowlist" in r for r in reasons),
            f"expected allowlist reason, got: {reasons}",
        )

    def test_empty_oracle_pass_rejected(self):
        # An empty marker matches everything -> fail closed, even for an
        # otherwise perfectly valid diff.
        tmp = _rtl_root()
        self.addCleanup(tmp.cleanup)
        spec = _spec(oracle_pass="")
        ok, reasons, _info = check_diff(_diff(), spec, tmp.name)
        self.assertIs(ok, False)
        self.assertTrue(
            any("empty oracle_pass" in r for r in reasons),
            f"expected 'empty oracle_pass' reason, got: {reasons}",
        )

    def test_whitespace_oracle_pass_rejected(self):
        # A blank marker ("   ") is as good as empty for matching purposes
        # -> fail closed exactly like the empty marker.
        tmp = _rtl_root()
        self.addCleanup(tmp.cleanup)
        for marker in ("   ", "\t\n "):
            spec = _spec(oracle_pass=marker)
            ok, reasons, _info = check_diff(_diff(), spec, tmp.name)
            self.assertIs(ok, False)
            self.assertTrue(
                any("empty oracle_pass" in r for r in reasons),
                f"marker {marker!r}: expected 'empty oracle_pass', got: {reasons}",
            )

    def test_valid_diff_accepted(self):        # Happy path: allowlisted file, 2 lines, lint-clean, no lint_cmd.
        tmp = _rtl_root()
        self.addCleanup(tmp.cleanup)
        ok, reasons, info = check_diff(_diff(), _spec(), tmp.name)
        self.assertIs(ok, True)
        self.assertEqual(reasons, [])
        self.assertEqual(info["files"], ["simpleuart.v"])
        self.assertEqual(info["patch_lines"], 2)

    def test_oversized_diff_rejected(self):
        # Default budget is <=40 changed lines (guard.py MAX_PATCH_LINES).
        tmp = _rtl_root()
        self.addCleanup(tmp.cleanup)
        body = "".join(f"-old line {i}\n+new line {i}\n" for i in range(21))
        big = (
            "--- a/simpleuart.v\n"
            "+++ b/simpleuart.v\n"
            "@@ -1 +1 @@\n" + body
        )
        ok, reasons, info = check_diff(big, _spec(), tmp.name)
        self.assertIs(ok, False)
        self.assertGreater(info["patch_lines"], 40)
        self.assertTrue(any("too many" in r for r in reasons))

    def test_forbidden_pattern_rejected(self):
        # tohost force-pass smuggled in a patch must be rejected.
        tmp = _rtl_root()
        self.addCleanup(tmp.cleanup)
        bad = _diff(old="assign tx = 1'b0;", new="assign tohost = 1; // ok")
        ok, reasons, _info = check_diff(bad, _spec(), tmp.name)
        self.assertIs(ok, False)
        self.assertTrue(any("forbidden" in r for r in reasons))

    def test_lint_cmd_failure_rejected(self):
        # A failing per-file spec.lint_cmd vetoes an otherwise valid diff.
        tmp = _rtl_root()
        self.addCleanup(tmp.cleanup)
        fail_cmd = sys.executable + ' -c "import sys; sys.exit(3)"'
        ok, reasons, _info = check_diff(_diff(), _spec(lint_cmd=fail_cmd), tmp.name)
        self.assertIs(ok, False)
        self.assertTrue(any("lint_cmd" in r for r in reasons))

    def test_lint_cmd_runs_per_file_with_cwd(self):
        # lint_cmd runs with cwd=rtl_root and {file} expands to the touched
        # diff path; both are real exec behavior, not mocks.
        tmp = _rtl_root()
        self.addCleanup(tmp.cleanup)
        ok, reasons, info = check_diff(
            _diff(), _spec(lint_cmd="test -f simpleuart.v"), tmp.name
        )
        self.assertIs(ok, True)
        self.assertEqual(reasons, [])
        self.assertEqual(info["lint_cmd"], {"simpleuart.v": True})
        ok2, reasons2, _info2 = check_diff(
            _diff(), _spec(lint_cmd="test {file} = simpleuart.v"), tmp.name
        )
        self.assertIs(ok2, True)
        self.assertEqual(reasons2, [])

    def test_guard_verdict_honored(self):
        # check_diff must honor guard.check's own verdict, not recompute it
        # from len(reasons): a (False, [], info) guard verdict stays False,
        # and a (True, [...]) verdict stays True with reasons preserved.
        # Simulated via stdlib mock because the real guard is self-consistent
        # and cannot produce a divergent pair.
        tmp = _rtl_root()
        self.addCleanup(tmp.cleanup)

        def _shape():
            return {
                "files": [],
                "patch_lines": 0,
                "added": 0,
                "removed": 0,
                "allowlist": [],
                "lint": {},
            }

        with mock.patch("agent.guard.check", return_value=(False, [], _shape())):
            ok, _reasons, _info = check_diff(_diff(), _spec(), tmp.name)
            self.assertIs(ok, False)
        with mock.patch(
            "agent.guard.check", return_value=(True, ["injected"], _shape())
        ):
            ok2, reasons2, _info2 = check_diff(_diff(), _spec(), tmp.name)
            self.assertIs(ok2, True)
            self.assertEqual(reasons2, ["injected"])

    def test_early_return_info_shape_matches_guard(self):
        # The fail-closed early return must carry exactly guard.check's info
        # keys plus lint_cmd, so Task 3's loop consumes either path uniformly.
        from agent import guard as guard_mod

        tmp = _rtl_root()
        self.addCleanup(tmp.cleanup)
        _ok, _reasons, guard_info = guard_mod.check(
            _diff(), _spec().allowlist, tmp.name
        )
        _ok2, _reasons2, early_info = check_diff(
            _diff(), _spec(oracle_pass=""), tmp.name
        )
        self.assertEqual(set(early_info), set(guard_info) | {"lint_cmd"})

    def test_verdict_keyword_convention(self):
        # `pass` is a Python keyword: anywhere a Verdict is built or read,
        # only Verdict(**{"pass": ..., "evidence": ...}) and
        # getattr(v, "pass") are allowed -- never positional construction
        # (True would land in `evidence`) or attribute access (SyntaxError
        # or wrong field). Static check over this task's files.
        for name in ("test_policy.py", "../afterglow_core/policy.py"):
            path = os.path.join(THIS_DIR, name)
            with open(path, "rb") as fh:
                toks = list(tokenize.tokenize(fh.readline))
            code = " ".join(
                t.string
                for t in toks
                # STRING covers plain/doc strings; FSTRING_MIDDLE covers
                # the literal spans of f-strings (their {...} expressions
                # stay as real code tokens and are still scanned).
                if t.type not in (
                    tokenize.COMMENT,
                    tokenize.STRING,
                    tokenize.FSTRING_START,
                    tokenize.FSTRING_MIDDLE,
                    tokenize.FSTRING_END,
                    tokenize.ENCODING,
                    tokenize.NL,
                    tokenize.NEWLINE,
                    tokenize.INDENT,
                    tokenize.DEDENT,
                    tokenize.ENDMARKER,
                )
            )
            self.assertIsNone(
                re.search(r"Verdict\s*\((?!\s*\*\*)", code),
                f"{name}: positional Verdict(...) construction found",
            )
            self.assertIsNone(
                re.search(r"\.\s*pass\b", code),
                f"{name}: attribute-style .pass access found",
            )


if __name__ == "__main__":
    unittest.main()
