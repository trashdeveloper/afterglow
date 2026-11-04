# Installing Afterglow

Prereqs: Python 3.10+, Verilator 5.x, Rust (cargo) for the TUI.
No API key is needed to install; one is needed to run the loop.

```bash
git clone <your-fork-url> afterglow && cd afterglow
cp .env.example .env        # then edit .env, add MODEL_API_KEY
bash scripts/install.sh     # checks toolchain, builds the TUI
./afterglow doctor           # verifies key + toolchain
./afterglow demo              # one triage run, patch withheld
```

What each step proves:

- `doctor` fails loud on a missing key or toolchain (exit nonzero,
  fix printed). No silent defaults.
- `demo` runs the fastest locked case end to end and withholds the
  patch — diagnosis saved, nothing applied.
- `make test` / `make ci` reproduce the gates (unittest suite +
  `scripts/ci.sh`).
- The TUI (`tui-rs/target/release/tui`) only renders on a real
  terminal; pipes fall back to plain text (CI-safe).

Keys: `MODEL_API_KEY` (or `SPARK_API_KEY` / `META_API_KEY`) in `.env`,
which is gitignored. Never commit `.env`.
