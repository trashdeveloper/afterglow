"""`afterglow init` environment checks + example-spec scaffold.

No live calls, no model, no sim builds. Every check returns a plain
``{"name", "status", "message"}`` dict -- status is ``"ok"`` or
``"FAIL"`` -- so the CLI can print a helpful fix instead of a
traceback on machines without Verilator, without a key, or without
pin files.
"""
from __future__ import annotations

import os
import shutil

KEY_VARS = ("MODEL_API_KEY", "SPARK_API_KEY", "META_API_KEY")

PIN_FILES = ("foundation.pin", "targets.pin")

EXAMPLE_NAME = "my-first-case.yaml"

EXAMPLE_TEMPLATE = """\
# Afterglow example CaseSpec (written by `afterglow init`).
# Same fault family as sim/targets/picosoc-uart-div.yaml: RX baud
# divisor 16x early. Rename this file and edit anchor/bug/files for
# your own RTL; every key below is required (see
# afterglow_core/verilator.py load_spec -- blank oracle_pass is refused).
project: "picorv32"
files: ["picosoc/simpleuart.v"]
top: "tb_picosoc_uart"
tb: "picosoc-uart-div_tb.sv"
anchor: "if (recv_divcnt > cfg_divider) begin"
bug: "if (recv_divcnt > (cfg_divider >> 4)) begin"
gate: "divider baud uart divisor"
oracle_pass: "tohost: PASS"
oracle_trap: "TRAP:"
"""


def check_verilator(which=None) -> dict:
    """Verilator present on PATH? Missing -> FAIL with install fix."""
    finder = which or shutil.which
    found = finder("verilator")
    if found:
        return {"name": "verilator", "status": "ok",
                "message": "ok: verilator found at %s" % found}
    return {"name": "verilator", "status": "FAIL",
            "message": "FAIL: verilator not found on PATH. Install it: "
                       "sudo apt install verilator (Debian/Ubuntu) or "
                       "brew install verilator (macOS), then re-run "
                       "`afterglow init`. Without Verilator, "
                       "`afterglow run` cannot build the sim."}


def read_dotenv(root: str) -> dict:
    """Parse KEY=VALUE lines from <root>/.env (missing -> {})."""
    path = os.path.join(root, ".env")
    out: dict = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip("\"'")
        if key:
            out.setdefault(key, val)
    return out


def check_api_key(env=None, dotenv=None) -> dict:
    """Model key in env or .env? Missing -> FAIL with export fix."""
    env = env if env is not None else os.environ
    dotenv = dotenv if dotenv is not None else {}
    for var in KEY_VARS:
        if str(env.get(var, "") or "").strip():
            return {"name": "api-key", "status": "ok",
                    "message": "ok: %s is set in the environment" % var}
    for var in KEY_VARS:
        if str(dotenv.get(var, "") or "").strip():
            return {"name": "api-key", "status": "ok",
                    "message": "ok: %s found in .env" % var}
    return {"name": "api-key", "status": "FAIL",
            "message": "FAIL: no model key found. Set one: "
                       "export MODEL_API_KEY=... (SPARK_API_KEY / "
                       "META_API_KEY also accepted) or add "
                       "MODEL_API_KEY=... to .env in the project root, "
                       "then re-run `afterglow init`. Without a key, "
                       "`afterglow run` refuses to invent a diagnosis "
                       "and exits 1."}


def check_pins(root: str) -> list:
    """foundation.pin + targets.pin readable and non-empty?"""
    results = []
    for name in PIN_FILES:
        path = os.path.join(root, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
            if text.strip():
                results.append({"name": name, "status": "ok",
                                "message": "ok: %s readable" % name})
            else:
                results.append({"name": name, "status": "FAIL",
                                "message": "FAIL: %s is empty at %s. "
                                           "Restore the pin file (it records "
                                           "the frozen Weft reference)."
                                           % (name, path)})
        except OSError:
            results.append({"name": name, "status": "FAIL",
                            "message": "FAIL: %s missing/unreadable at %s. "
                                       "Re-clone the project root or restore "
                                       "the pin file (it records the frozen "
                                       "Weft reference); `afterglow run` "
                                       "needs it to prove the split."
                                       % (name, path)})
    return results


def scaffold_example(root: str, force: bool = False) -> str:
    """Write the example CaseSpec YAML; return its path.

    Writes into sim/targets/ when <root>/sim/targets exists (normal
    checkout), else directly into <root> (bare directory). Never
    overwrites an existing file unless force=True.
    """
    targets = os.path.join(root, "sim", "targets")
    if os.path.isdir(targets):
        dest = os.path.join(targets, EXAMPLE_NAME)
    else:
        dest = os.path.join(root, EXAMPLE_NAME)
    if os.path.isfile(dest) and not force:
        return dest
    parent = os.path.dirname(dest)
    os.makedirs(parent, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(EXAMPLE_TEMPLATE)
    return dest
