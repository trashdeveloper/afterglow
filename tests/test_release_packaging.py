"""Release packaging contract: .env.example keys match doctor's accepted
key names; Makefile exposes the documented targets."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestEnvExample(unittest.TestCase):
    def test_env_example_exists_and_names_accepted_keys(self):
        path = os.path.join(ROOT, ".env.example")
        self.assertTrue(os.path.isfile(path), ".env.example missing")
        body = open(path).read()
        # doctor.py accepts MODEL_API_KEY (+ SPARK_API_KEY / META_API_KEY).
        for key in ("MODEL_API_KEY", "SPARK_API_KEY", "META_API_KEY"):
            self.assertIn(key, body, "%s not documented" % key)
        # No real secrets: values must be placeholders, not key-like.
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            _, _, value = line.partition("=")
            value = value.strip().strip("\"'")
            lowered = value.lower()
            if any(mark in lowered for mark in ("your-", "here", "example",
                                                "changeme", "xxx")):
                continue  # documented placeholder, not a secret
            self.assertFalse(
                re.fullmatch(r"[A-Za-z0-9_\-]{20,}", value or ""),
                "looks like a real secret: %r" % line)


class TestMakefile(unittest.TestCase):
    def test_makefile_targets(self):
        path = os.path.join(ROOT, "Makefile")
        self.assertTrue(os.path.isfile(path), "Makefile missing")
        body = open(path).read()
        for target in ("doctor", "demo", "test", "ci", "tui"):
            self.assertRegex(
                body, r"(?m)^%s\s*:" % target,
                "Makefile target missing: %s" % target)


class TestLicense(unittest.TestCase):
    def test_gpl_v3_present(self):
        path = os.path.join(ROOT, "LICENSE")
        self.assertTrue(os.path.isfile(path), "LICENSE missing")
        body = open(path).read()
        self.assertIn("GNU GENERAL PUBLIC LICENSE", body)
        self.assertIn("Version 3", body)


class TestWorkflows(unittest.TestCase):
    def test_ci_workflow(self):
        import yaml  # noqa
        path = os.path.join(ROOT, ".github", "workflows", "ci.yml")
        self.assertTrue(os.path.isfile(path), "ci workflow missing")
        with open(path) as fh:
            cfg = yaml.safe_load(fh)
        steps = [
            s.get("run", "")
            for job in cfg.get("jobs", {}).values()
            for s in job.get("steps", [])
        ]
        blob = "\n".join(steps)
        self.assertIn("scripts/ci.sh", blob, "workflow must run ci.sh")
        self.assertIn("unittest", blob, "workflow must run test suite")


if __name__ == "__main__":
    unittest.main()
