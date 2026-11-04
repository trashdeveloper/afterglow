#!/bin/bash
# Afterglow first-run setup: checks the toolchain, builds the TUI,
# and prints next steps. Never writes secrets; never commits anything.
set -u
fail=0
need() {
  if command -v "$1" >/dev/null 2>&1; then
    echo "ok: $1 $("$1" --version 2>/dev/null | head -1)"
  else
    echo "MISSING: $1 ($2)"; fail=1
  fi
}
need python3 "https://www.python.org/downloads/"
need verilator "https://www.veripool.org/verilator/ (need 5.x)"
need cargo "https://rustup.rs (Rust toolchain for the TUI)"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "created .env from .env.example -- edit it and add your MODEL_API_KEY"
else
  echo "ok: .env exists"
fi
if [ $fail -eq 0 ] && command -v cargo >/dev/null 2>&1; then
  echo "building TUI (release)..."
  (cd tui-rs && cargo build --release) || fail=1
fi
if [ $fail -eq 0 ]; then
  echo "next: ./afterglow doctor  (checks key + toolchain)"
  echo "then: ./afterglow demo    (one triage run, patch withheld)"
else
  echo "fix the MISSING items above, then re-run: bash scripts/install.sh"
  exit 1
fi
