#!/usr/bin/env python3
"""Dispatcher CLI for the Afterglow sim board. Calls vbackend.run_case."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vbackend import run_case


def main(argv=None):
    ap = argparse.ArgumentParser(description="Afterglow sim dispatcher")
    ap.add_argument("--case", required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--boot", default="/tmp/boot.log")
    ap.add_argument("--verdict", default="/tmp/v.json")
    a = ap.parse_args(argv)
    return run_case(a.case, seed=a.seed, boot=a.boot, verdict=a.verdict)


if __name__ == "__main__":
    sys.exit(main())
