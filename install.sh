#!/bin/bash
# Afterglow one-shot setup: clone -> ./install.sh -> ./afterglow demo
# Idempotent: safe to re-run. Fails loud with the fix, never half-installs.
set -euo pipefail
cd "$(dirname "$0")"

say()  { printf 'install: %s\n' "$*"; }
fail() { printf 'install: FATAL: %s\n' "$*" >&2; exit 1; }

# 1. python3 ---------------------------------------------------------------
command -v python3 >/dev/null || fail "python3 not found. Install Python 3.10+ first: https://www.python.org/downloads/"
say "python3: $(python3 --version 2>&1)"

# 2. python deps (stdlib + pillow + rich; no venv needed, --user keeps it local)
if ! python3 -c "import PIL, rich" 2>/dev/null; then
  say "installing pillow + rich (pip --user, no sudo touched)..."
  python3 -m pip install --user --quiet pillow rich \
    || fail "pip install failed. Try: python3 -m pip install --user pillow rich"
else
  say "python deps ok (pillow, rich)"
fi

# 3. verilator (needed to actually run sims; doctor still passes without it for dry runs)
if command -v verilator >/dev/null; then
  say "verilator: $(verilator --version | head -1)"
else
  say "WARNING: verilator not found — sims won't build until you install it."
  say "  Ubuntu/Debian: sudo apt install verilator   (need >= 5.0: verilator --version)"
  say "  macOS:          brew install verilator"
fi

# 4. rust TUI (optional but default: without the binary, run/ falls back to plain text)
TUI_BIN="tui-rs/target/release/tui"
if command -v cargo >/dev/null; then
  if [ ! -x "$TUI_BIN" ]; then
    say "building the terminal UI (cargo, one time, ~1 min)..."
    (cd tui-rs && cargo build --release -q) \
      || fail "cargo build failed. Rust: https://rustup.rs, then re-run ./install.sh"
  else
    say "TUI binary present ($TUI_BIN)"
  fi
else
  say "WARNING: cargo not found — skipping TUI build. Everything still works as plain text."
  say "  To get the TUI later: https://rustup.rs, then: (cd tui-rs && cargo build --release)"
fi

# 5. API key file (never committed; doctor enforces it at run time)
if [ ! -f .env ]; then
  cp .env.example .env
  say "created .env from .env.example — put your Meta Model API key in it:"
  say "  MODEL_API_KEY=...   (get one, then re-run ./install.sh to verify)"
else
  say ".env present"
fi

# 6. proof ------------------------------------------------------------------
say "running ./afterglow doctor as proof..."
./afterglow doctor
say "done. Next: ./afterglow demo   (one triage model call, patch withheld)"
