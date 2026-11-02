//! `tui <spec> [args...]` — Afterglow inline terminal frontend.
//!
//! Spawns `<repo-root>/afterglow run <spec> --emit-events jsonl [args...]`
//! (repo root as cwd; the Python flag already exists), parses the JSONL
//! event stream (`progress` / `attempt` / `verdict`), and renders with
//! ratatui: header line (case name), full-width gradient sweep bar
//! (fractional eighth-blocks, truecolor gradient, travelling bright tip,
//! dim track, braille spinner), attempt counter, and a verdict screen.
//!
//! INLINE render only, by construction: frames are laid out by ratatui's
//! `Paragraph` widget into a headless buffer and painted to stdout with
//! plain cursor-up rewrites. The alternate screen (`?1049h`) can never be
//! emitted — no code path enables it — scrollback is preserved, and the
//! cursor is hidden during the run and restored on exit. (Headless paint
//! also avoids the cursor-position query that `script(1)` ptys cannot
//! answer, so forced-TTY captures work.) Backend human text (child
//! stderr) is buffered into a bounded tail ring and shown only when the
//! run fails — never streamed mid-frame, so it cannot duplicate our
//! header or tear the repaint.
//!
//! Exit codes: 2 = no TTY (or usage), 1 = backend failure
//! (spawn error, backend exit without `GREEN`, or no verdict event),
//! 0 = `GREEN`.

mod bar;
mod events;

use std::io::{BufReader, IsTerminal, Read, Write};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use crossterm::cursor;
use ratatui::prelude::*;
use ratatui::widgets::Paragraph;

/// Inline frame height: header + bar + status + footer.
const FRAME_HEIGHT: u16 = 4;
/// Frame cadence (matches the braille spinner step).
const FRAME_MS: u64 = 80;

/// Shared live state between the reader thread and the render loop.
#[derive(Debug, Default)]
struct Shared {
    mapper: events::Mapper,
    /// Backend stdout hit EOF (every emitted line already handled).
    eof: bool,
    /// Backend process has been reaped.
    child_done: bool,
}

/// Exit code for a finished run: only an explicit `GREEN` verdict is 0.
fn exit_for_verdict(verdict: Option<&str>) -> i32 {
    match verdict {
        Some("GREEN") => 0,
        _ => 1,
    }
}

/// Display name for the header: basename of the spec path.
fn spec_basename(spec: &str) -> &str {
    spec.rsplit('/').next().unwrap_or(spec)
}

/// Load KEY=VALUE lines from a `.env` file into this process, without
/// overriding already-exported vars. Skips comments/blank lines and a
/// leading `export `; strips one layer of matching single/double quotes.
/// Missing/unreadable file = silent no-op. Never prints values.
fn load_dotenv_file(path: &std::path::Path) {
    let text = match std::fs::read_to_string(path) {
        Ok(t) => t,
        Err(_) => return,
    };
    for raw in text.lines() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let line = line.strip_prefix("export ").unwrap_or(line).trim();
        let Some((k, v)) = line.split_once('=') else {
            continue;
        };
        let key = k.trim();
        if key.is_empty() || std::env::var_os(key).is_some() {
            continue;
        }
        let mut val = v.trim();
        if val.len() >= 2
            && ((val.starts_with('\'') && val.ends_with('\''))
                || (val.starts_with('"') && val.ends_with('"')))
        {
            val = &val[1..val.len() - 1];
        }
        std::env::set_var(key, val);
    }
}

/// Load KEY=VALUE lines from `<dir>/.env` (see [`load_dotenv_file`]).
#[allow(dead_code)]
fn load_dotenv(dir: &std::path::Path) {
    load_dotenv_file(&dir.join(".env"));
}

/// Locate the repo-root `.env` for a backend binary path: `../.env`
/// relative to the resolved `afterglow` binary — i.e. the binary's
/// sibling `.env` (`parent/.env`; note a literal `backend.join("../.env")`
/// cannot work through a file path, it needs the parent dir), then each
/// ancestor dir up from the binary, then the canonicalized variant,
/// falling back to `./.env` in the cwd. Returns `None` when missing.
fn find_dotenv_for_backend(backend: &std::path::Path) -> Option<std::path::PathBuf> {
    if let Some(start) = backend.parent() {
        let mut cur: Option<&std::path::Path> = Some(start);
        while let Some(dir) = cur {
            let cand = dir.join(".env");
            if cand.is_file() {
                return Some(cand);
            }
            cur = dir.parent();
        }
        if let Ok(canon) = backend.canonicalize() {
            if let Some(cstart) = canon.parent() {
                let mut cur2: Option<&std::path::Path> = Some(cstart);
                while let Some(dir) = cur2 {
                    let cand = dir.join(".env");
                    if cand.is_file() {
                        return Some(cand);
                    }
                    cur2 = dir.parent();
                }
            }
        }
    }
    let cwd_dot = std::path::PathBuf::from("./.env");
    if cwd_dot.is_file() {
        return Some(cwd_dot);
    }
    if let Ok(cwd) = std::env::current_dir() {
        let cand = cwd.join(".env");
        if cand.is_file() {
            return Some(cand);
        }
    }
    None
}

/// Auto-load the repo-root `.env` (see [`find_dotenv_for_backend`]) into
/// this process before spawning the backend. Silent no-op when missing.
fn auto_load_dotenv(backend: &std::path::Path) {
    if let Some(p) = find_dotenv_for_backend(backend) {
        load_dotenv_file(&p);
    }
}

/// Bounded tail buffer for backend stderr: keeps the last `cap` bytes.
/// The TUI owns the display while running, so backend chatter must not
/// interleave mid-frame; the tail is dumped to our stderr only when the
/// run fails (or with --verbose passthrough).
struct StderrRing {
    buf: Vec<u8>,
    cap: usize,
}

impl StderrRing {
    fn new(cap: usize) -> Self {
        Self {
            buf: Vec::new(),
            cap,
        }
    }

    fn push(&mut self, chunk: &[u8]) {
        self.buf.extend_from_slice(chunk);
        if self.buf.len() > self.cap {
            let drop = self.buf.len() - self.cap;
            self.buf.drain(..drop);
        }
    }

    fn bytes(&self) -> &[u8] {
        &self.buf
    }

    fn is_empty(&self) -> bool {
        self.buf.is_empty()
    }
}

/// Usable frame width: a pty without a set winsize reports 0 columns
/// (notably under `script(1)`); a real terminal is never 0 wide.
fn pick_width(cols: u16) -> usize {
    if cols == 0 {
        80
    } else {
        cols as usize
    }
}

/// Locate the repo root: the nearest directory (starting from
/// `AFTERGLOW_ROOT`, then the executable's ancestors, then the cwd's
/// ancestors) that contains the `afterglow` entrypoint. Falls back to
/// the current directory.
fn find_repo_root() -> std::path::PathBuf {
    if let Some(dir) = std::env::var_os("AFTERGLOW_ROOT") {
        let dir = std::path::PathBuf::from(dir);
        if dir.join("afterglow").is_file() {
            return dir;
        }
    }
    let mut anchors = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        anchors.extend(exe.ancestors().skip(1).map(|p| p.to_path_buf()));
    }
    if let Ok(cwd) = std::env::current_dir() {
        anchors.push(cwd.clone());
        anchors.extend(cwd.ancestors().skip(1).map(|p| p.to_path_buf()));
    }
    for dir in anchors {
        if dir.join("afterglow").is_file() {
            return dir;
        }
    }
    std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from("."))
}

const USAGE: &str = "usage: tui <spec> [args...]\n  run afterglow with an inline gradient progress display";

fn main() {
    let code = run();
    std::process::exit(code);
}

fn run() -> i32 {
    // Cursor must come back even on panic (raw bytes, no paint yet).
    let default_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        let _ = crossterm::execute!(std::io::stdout(), cursor::Show);
        default_hook(info);
    }));

    let mut argv: Vec<String> = std::env::args().skip(1).collect();
    if argv.iter().any(|a| a == "-h" || a == "--help") {
        println!("{USAGE}");
        return 0;
    }
    if argv.is_empty() {
        eprintln!("tui: missing <spec>\n{USAGE}");
        return 2;
    }
    let spec = argv.remove(0);
    let extra = argv;

    if !std::io::stdout().is_terminal() {
        eprintln!("tui: no TTY on stdout (inline render needs a terminal)");
        return 2;
    }

    let root = find_repo_root();
    let backend = root.join("afterglow");
    // The backend needs MODEL_API_KEY; users keep it in the repo .env.
    // Auto-load `../.env` relative to the resolved backend binary (its
    // sibling dir), falling back to `./.env` in the cwd — never
    // overriding already-exported vars, never printing values.
    auto_load_dotenv(&backend);
    let mut child = match Command::new(&backend)
        .arg("run")
        .arg(&spec)
        .arg("--emit-events")
        .arg("jsonl")
        .args(&extra)
        .current_dir(&root)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(c) => c,
        Err(e) => {
            eprintln!("tui: backend spawn failed ({}): {e}", backend.display());
            return 1;
        }
    };

    // Backend human text → bounded tail ring (NOT streamed: verbatim
    // forwarding interleaves mid-frame and duplicates our own header).
    // Dumped to our stderr only when the run fails.
    let err_ring = Arc::new(Mutex::new(StderrRing::new(65536)));
    if let Some(err) = child.stderr.take() {
        let ring = Arc::clone(&err_ring);
        std::thread::spawn(move || {
            let mut err = err;
            let mut buf = [0u8; 8192];
            loop {
                match err.read(&mut buf) {
                    Ok(0) => break,
                    Ok(n) => {
                        if let Ok(mut r) = ring.lock() {
                            r.push(&buf[..n]);
                        }
                    }
                    Err(_) => break,
                }
            }
        });
    }

    let shared = Arc::new(Mutex::new(Shared {
        mapper: events::Mapper::new(),
        ..Shared::default()
    }));

    // Backend JSONL stdout → mapper, on its own thread (handles partial
    // lines: the pipe yields arbitrary chunks, not lines).
    if let Some(out) = child.stdout.take() {
        let shared = Arc::clone(&shared);
        std::thread::spawn(move || {
            let mut reader = BufReader::new(out);
            let mut lb = events::LineBuffer::new();
            let mut buf = [0u8; 8192];
            loop {
                match reader.read(&mut buf) {
                    Ok(0) => break,
                    Ok(n) => {
                        let chunk = String::from_utf8_lossy(&buf[..n]).into_owned();
                        let lines = lb.feed(&chunk);
                        if let Ok(mut s) = shared.lock() {
                            for line in &lines {
                                s.mapper.handle_line(line);
                            }
                        }
                    }
                    Err(_) => break,
                }
            }
            if let Ok(mut s) = shared.lock() {
                s.eof = true;
            }
        });
    }

    let mut painter = Painter::new();
    let started = Instant::now();
    let title = spec_basename(&spec).to_string();
    let mut exit_code = 1;
    loop {
        let elapsed = started.elapsed();
        let snapshot = shared.lock().map(|s| {
            (
                s.mapper.phase.clone(),
                s.mapper.attempt,
                s.mapper.total,
                s.mapper.verdict.clone(),
                s.mapper.patch_lines,
                s.mapper.seconds,
                s.eof,
            )
        });
        let Ok((phase, attempt, total, verdict, patch_lines, seconds, eof)) = snapshot else {
            break;
        };

        // Reap the backend without blocking the animation.
        let mut done = false;
        match child.try_wait() {
            Ok(Some(_status)) => done = true,
            Ok(None) => {}
            Err(_) => done = true,
        }
        if let Ok(mut s) = shared.lock() {
            if done {
                s.child_done = true;
            }
            done = s.child_done;
        }

        let finished = verdict.is_some();
        // A pty without a set winsize reports 0 columns (notably under
        // `script(1)`); a real terminal is never 0 wide, so fall back.
        let width = pick_width(
            crossterm::terminal::size()
                .map(|(w, _)| w)
                .unwrap_or(80),
        );
        let rows = render_rows(
            width,
            &title,
            &phase,
            attempt,
            total,
            verdict.as_deref(),
            patch_lines,
            seconds,
            elapsed,
            finished,
        );
        painter.paint(&rows);

        if finished && done && eof {
            exit_code = exit_for_verdict(verdict.as_deref());
            break;
        }
        if done && eof {
            // Backend is gone and the stream is drained: if no verdict
            // event arrived (e.g. the FATAL spec-load path, which emits
            // no JSONL), paint the failure screen as the final frame so
            // it stays in scrollback, then fail loudly instead of hanging.
            if !finished {
                let rows = render_rows(
                    width, &title, &phase, attempt, total, None, 0, 0.0, elapsed, true,
                );
                painter.paint(&rows);
            }
            exit_code = exit_for_verdict(verdict.as_deref());
            break;
        }
        std::thread::sleep(Duration::from_millis(FRAME_MS));
    }

    // Final frame stays in scrollback; cursor back; prompt gets a fresh line.
    painter.finish();
    if exit_code != 0 {
        // Loud failure: show what the backend said (it was hidden
        // while we owned the display).
        if let Ok(r) = err_ring.lock() {
            if !r.is_empty() {
                let _ = std::io::stderr().write_all(b"--- backend stderr ---\n");
                let _ = std::io::stderr().write_all(r.bytes());
            }
        }
    }
    // Reap before reporting the code.
    let _ = child.wait();
    exit_code
}

/// Lay out one frame with ratatui widgets into per-row ANSI strings
/// (no trailing newlines, no cursor motion — see [`Painter`]).
#[allow(clippy::too_many_arguments)]
fn render_rows(
    width: usize,
    title: &str,
    phase: &str,
    attempt: u32,
    total: u32,
    verdict: Option<&str>,
    patch_lines: u32,
    seconds: f64,
    elapsed: std::time::Duration,
    finished: bool,
) -> Vec<String> {
    let elapsed_s = elapsed.as_secs_f64();
    let elapsed_ms = elapsed.as_millis() as u64;
    let width_u16: u16 = width.clamp(1, u16::MAX as usize) as u16;
    let backend = ratatui::backend::TestBackend::new(width_u16, FRAME_HEIGHT);
    let mut terminal = ratatui::Terminal::new(backend).expect("headless terminal");
    terminal
        .draw(|frame| {
            let spinner = if finished { "●" } else { bar::spinner_at(elapsed_ms) };

            // Header: case name.
            let header = Line::from(vec![
                Span::styled("◈ ", Style::default().fg(Color::Rgb(0x8f, 0x8f, 0x96))),
                Span::styled(
                    title.to_string(),
                    Style::default()
                        .fg(Color::White)
                        .add_modifier(Modifier::BOLD),
                ),
            ]);

            // Full-width gradient sweep bar with spinner + fractional blocks.
            let bar_width = width.saturating_sub(2);
            let fill = bar::fill_fraction(attempt, total, finished);
            let cells = bar::render_bar(bar_width, fill, elapsed_s);
            let mut bar_spans = Vec::with_capacity(cells.len() + 2);
            let spinner_color = if finished {
                match verdict {
                    Some("GREEN") => Color::LightGreen,
                    Some("TRIAGE") => Color::Yellow,
                    _ => Color::LightRed,
                }
            } else {
                Color::Rgb(0xfa, 0xb2, 0x83) // peach
            };
            bar_spans.push(Span::styled(
                spinner.to_string(),
                Style::default()
                    .fg(spinner_color)
                    .add_modifier(Modifier::BOLD),
            ));
            bar_spans.push(Span::raw(" "));
            for c in &cells {
                bar_spans.push(Span::styled(
                    c.glyph.to_string(),
                    Style::default().fg(Color::Rgb(c.rgb.0, c.rgb.1, c.rgb.2)),
                ));
            }
            let bar_line = Line::from(bar_spans);

            // Status: attempt counter while running, verdict line when done.
            let status_text = if finished {
                match verdict {
                    Some("GREEN") => {
                        format!("GREEN: fixed ({patch_lines} patch lines, {seconds:.1}s)")
                    }
                    Some("TRIAGE") => {
                        format!("TRIAGE: diagnosis saved, patch withheld ({seconds:.1}s)")
                    }
                    Some("RED") => format!("RED: not fixed ({seconds:.1}s)"),
                    _ => "RED: backend failed (no verdict event)".to_string(),
                }
            } else if total > 0 {
                format!("{phase} attempt {}/{} ...", attempt.max(1), total)
            } else if attempt > 0 {
                format!("{phase} attempt {attempt} ...")
            } else {
                format!("{phase} ...")
            };
            let status_style = if finished {
                let color = match verdict {
                    Some("GREEN") => Color::LightGreen,
                    Some("TRIAGE") => Color::Yellow,
                    _ => Color::LightRed,
                };
                Style::default().fg(color).add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(Color::Rgb(0x8f, 0x8f, 0x96))
            };
            let status = Line::from(vec![Span::styled(status_text, status_style)]);

            // Footer: quiet while running (bar + status carry progress);
            // preservation note when done. No chatty `---------- elapsed`
            // line: end-of-run output is ONE verdict line + this note.
            let footer_text = if finished {
                "afterglow run finished — scrollback preserved".to_string()
            } else {
                String::new()
            };
            let footer = Line::from(vec![Span::styled(
                footer_text,
                Style::default().fg(Color::DarkGray),
            )]);

            let para = Paragraph::new(vec![header, bar_line, status, footer]);
            frame.render_widget(para, frame.area());
        })
        .expect("headless draw");
    buffer_to_ansi(terminal.backend().buffer())
}

/// Serialize a ratatui buffer into per-row ANSI strings: truecolor SGR
/// on style change, `\x1b[0m` reset at each row start/end. Never emits
/// alternate-screen or cursor-position sequences.
fn buffer_to_ansi(buf: &ratatui::buffer::Buffer) -> Vec<String> {
    let area = buf.area;
    let mut rows = Vec::with_capacity(area.height as usize);
    for y in 0..area.height {
        let mut row = String::from("\x1b[0m");
        let mut last_fg = Color::Reset;
        let mut last_bold = false;
        for x in 0..area.width {
            let cell = &buf[(x, y)];
            if cell.skip {
                continue;
            }
            let bold = cell.modifier.contains(Modifier::BOLD);
            if cell.fg != last_fg || bold != last_bold {
                row.push_str(&sgr(cell.fg, bold));
                last_fg = cell.fg;
                last_bold = bold;
            }
            row.push_str(cell.symbol());
        }
        row.push_str("\x1b[0m");
        rows.push(row);
    }
    rows
}

/// SGR fragment selecting `fg` (+ bold). Foreground only — the bar
/// paints on the terminal's default background.
fn sgr(fg: Color, bold: bool) -> String {
    let mut s = String::from("\x1b[");
    if bold {
        s.push_str("1;");
    }
    match fg {
        Color::Reset => s.push_str("39"),
        Color::Black => s.push_str("30"),
        Color::Red => s.push_str("31"),
        Color::Green => s.push_str("32"),
        Color::Yellow => s.push_str("33"),
        Color::Blue => s.push_str("34"),
        Color::Magenta => s.push_str("35"),
        Color::Cyan => s.push_str("36"),
        Color::Gray => s.push_str("37"),
        Color::DarkGray => s.push_str("90"),
        Color::LightRed => s.push_str("91"),
        Color::LightGreen => s.push_str("92"),
        Color::LightYellow => s.push_str("93"),
        Color::LightBlue => s.push_str("94"),
        Color::LightMagenta => s.push_str("95"),
        Color::LightCyan => s.push_str("96"),
        Color::White => s.push_str("97"),
        Color::Rgb(r, g, b) => {
            s.push_str(&format!("38;2;{r};{g};{b}"));
        }
        Color::Indexed(i) => {
            s.push_str(&format!("38;5;{i}"));
        }
    }
    s.push('m');
    s
}

/// Inline painter: first frame prints rows; later frames rewrite them
/// in place with cursor-up; `finish` restores the cursor and leaves a
/// fresh line for the prompt. Emits no alternate-screen sequences.
struct Painter {
    out: std::io::Stdout,
    painted: bool,
    height: u16,
}

impl Painter {
    fn new() -> Self {
        let _ = crossterm::execute!(std::io::stdout(), cursor::Hide);
        Self {
            out: std::io::stdout(),
            painted: false,
            height: FRAME_HEIGHT,
        }
    }

    fn paint(&mut self, rows: &[String]) {
        debug_assert_eq!(rows.len(), self.height as usize);
        if !self.painted {
            for (i, row) in rows.iter().enumerate() {
                if i > 0 {
                    let _ = self.out.write_all(b"\n");
                }
                let _ = self.out.write_all(row.as_bytes());
                let _ = self.out.write_all(b"\x1b[K");
            }
            self.painted = true;
        } else {
            // Cursor sits at the end of the last row: move up height-1 to
            // reach the first row. Up `height` overshoots onto the prompt
            // line and leaves a stale duplicate header / stray bullet row.
            let _ = write!(self.out, "\x1b[{}A", self.height.saturating_sub(1));
            for (i, row) in rows.iter().enumerate() {
                let _ = self.out.write_all(b"\r");
                let _ = self.out.write_all(row.as_bytes());
                let _ = self.out.write_all(b"\x1b[K");
                if i + 1 < rows.len() {
                    let _ = self.out.write_all(b"\n");
                }
            }
        }
        let _ = self.out.flush();
    }

    fn finish(&mut self) {
        let _ = self.out.write_all(b"\n");
        let _ = self.out.flush();
        let _ = crossterm::execute!(self.out, cursor::Show);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_green_exits_zero() {
        assert_eq!(exit_for_verdict(Some("GREEN")), 0);
        assert_eq!(exit_for_verdict(Some("RED")), 1);
        assert_eq!(exit_for_verdict(Some("TRIAGE")), 1);
        assert_eq!(exit_for_verdict(None), 1);
    }

    #[test]
    fn zero_width_pty_falls_back_to_80() {
        assert_eq!(pick_width(0), 80);
        assert_eq!(pick_width(80), 80);
        assert_eq!(pick_width(40), 40);
    }

    #[test]
    fn header_uses_spec_basename() {        assert_eq!(
            spec_basename("sim/targets/uart-start-half.yaml"),
            "uart-start-half.yaml"
        );
        assert_eq!(spec_basename("case.yaml"), "case.yaml");
        assert_eq!(spec_basename(""), "");
    }

    #[test]
    fn rows_carry_truecolor_gradient_and_never_alternate_screen() {
        let rows = render_rows(
            40,
            "demo.yaml",
            "diagnose",
            1,
            3,
            None,
            0,
            0.0,
            Duration::from_millis(400),
            false,
        );
        assert_eq!(rows.len(), FRAME_HEIGHT as usize);
        let body = rows.join("\n");
        // Truecolor gradient present (spec blue #5c9cf5 → ...).
        assert!(body.contains("38;2;"), "expected truecolor SGR");
        // Spinner + header survive the headless render.
        assert!(body.contains("demo.yaml"));
        // Inline-only: no alternate-screen, no cursor-position query.
        for forbidden in ["1049h", "1049l", "?25l", "6n"] {
            assert!(
                !body.contains(forbidden),
                "inline frame must not contain {forbidden}"
            );
        }
    }

    #[test]
    fn verdict_rows_show_patch_lines_and_seconds() {
        let rows = render_rows(
            60,
            "demo.yaml",
            "verify",
            3,
            3,
            Some("GREEN"),
            12,
            14.2,
            Duration::from_secs(15),
            true,
        );
        let body = rows.join("\n");
        assert!(body.contains("GREEN: fixed (12 patch lines, 14.2s)"));
    }

    #[test]
    fn dotenv_loads_pairs_without_overriding_exported() {
        let dir = std::env::temp_dir().join("tui_dotenv_test");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join(".env"),
            "# comment\n\nexport TUI_TEST_A=1\nTUI_TEST_B='two words'\nTUI_TEST_C=\"quoted\"\nTUI_TEST_D=plain\n",
        )
        .unwrap();
        load_dotenv(&dir);
        assert_eq!(std::env::var("TUI_TEST_A").unwrap(), "1");
        assert_eq!(std::env::var("TUI_TEST_B").unwrap(), "two words");
        assert_eq!(std::env::var("TUI_TEST_C").unwrap(), "quoted");
        assert_eq!(std::env::var("TUI_TEST_D").unwrap(), "plain");
        // Missing file is a silent no-op.
        load_dotenv(&dir.join("nonexistent"));
        let _ = std::fs::remove_dir_all(&dir);
        std::env::remove_var("TUI_TEST_A");
        std::env::remove_var("TUI_TEST_B");
        std::env::remove_var("TUI_TEST_C");
        std::env::remove_var("TUI_TEST_D");
    }

    #[test]
    fn dotenv_never_overrides_exported_vars() {
        let dir = std::env::temp_dir().join("tui_dotenv_keep_test");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join(".env"), "TUI_TEST_KEEP=nope\n").unwrap();
        std::env::set_var("TUI_TEST_KEEP", "orig");
        load_dotenv(&dir);
        assert_eq!(std::env::var("TUI_TEST_KEEP").unwrap(), "orig");
        let _ = std::fs::remove_dir_all(&dir);
        std::env::remove_var("TUI_TEST_KEEP");
    }

    #[test]
    fn stderr_ring_keeps_tail_under_cap() {
        let mut ring = StderrRing::new(16);
        ring.push(b"0123456789abcdef");
        ring.push(b"GHIJ");
        assert_eq!(ring.bytes(), b"456789abcdefGHIJ");
    }

    /// Strip CSI sequences (`\x1b[...m`, `\x1b[K`, `\x1b[<n>A`, …) and `\r`
    /// so tests count visible text, not paint bytes.
    fn strip_ansi(s: &str) -> String {
        let mut out = String::with_capacity(s.len());
        let mut chars = s.chars().peekable();
        while let Some(c) = chars.next() {
            if c == '\x1b' {
                if matches!(chars.peek(), Some('[')) {
                    chars.next();
                    for c2 in chars.by_ref() {
                        if c2.is_ascii_alphabetic() {
                            break;
                        }
                    }
                }
                continue;
            }
            if c == '\r' {
                continue;
            }
            out.push(c);
        }
        out
    }

    #[test]
    fn frame_has_single_header_and_four_rows() {
        for finished in [false, true] {
            let rows = render_rows(
                60,
                "demo.yaml",
                "diagnose",
                1,
                3,
                if finished { Some("GREEN") } else { None },
                if finished { 7 } else { 0 },
                if finished { 12.5 } else { 0.0 },
                Duration::from_millis(400),
                finished,
            );
            assert_eq!(rows.len(), FRAME_HEIGHT as usize);
            let stripped: Vec<String> = rows.iter().map(|r| strip_ansi(r)).collect();
            // Header (spec name) renders EXACTLY once.
            assert_eq!(
                stripped.iter().filter(|l| l.contains("demo.yaml")).count(),
                1,
                "header must render exactly once (finished={finished})"
            );
            assert_eq!(
                stripped.iter().filter(|l| l.contains("◈")).count(),
                1,
                "exactly one ◈ header line (finished={finished})"
            );
            // No stray line consisting solely of a bullet.
            for l in &stripped {
                assert_ne!(
                    l.trim(),
                    "●",
                    "no line consisting solely of a bullet (finished={finished}): {stripped:?}"
                );
            }
        }
    }

    #[test]
    fn finished_footer_is_quiet_verdict_plus_note() {
        for (verdict, expect) in [
            (Some("GREEN"), "GREEN:"),
            (Some("RED"), "RED:"),
            (None, "RED: backend failed"),
        ] {
            let rows = render_rows(
                60,
                "demo.yaml",
                "verify",
                3,
                3,
                verdict,
                12,
                14.2,
                Duration::from_secs(15),
                true,
            );
            let stripped: Vec<String> = rows.iter().map(|r| strip_ansi(r)).collect();
            // ONE verdict line …
            assert_eq!(
                stripped
                    .iter()
                    .filter(|l| l.contains("GREEN:") || l.contains("RED:") || l.contains("TRIAGE:"))
                    .count(),
                1,
                "exactly one verdict line for {verdict:?}: {stripped:?}"
            );
            assert!(
                stripped.iter().any(|l| l.contains(expect)),
                "finished frame must contain {expect:?}: {stripped:?}"
            );
            // … plus the existing scrollback note, nothing chatty.
            assert_eq!(
                stripped.iter().filter(|l| l.contains("scrollback preserved")).count(),
                1,
                "scrollback note stays: {stripped:?}"
            );
            for l in &stripped {
                assert!(
                    !l.contains("elapsed"),
                    "no chatty elapsed footer in end-of-run output: {stripped:?}"
                );
                assert!(
                    !l.contains("----------"),
                    "no chatty dashed footer in end-of-run output: {stripped:?}"
                );
            }
            let footerish = stripped
                .iter()
                .filter(|l| {
                    l.contains("GREEN:")
                        || l.contains("RED:")
                        || l.contains("TRIAGE:")
                        || l.contains("scrollback")
                        || l.contains("afterglow run")
                })
                .count();
            assert!(
                footerish <= 2,
                "footer line count <= 2 (verdict + note), got {footerish}: {stripped:?}"
            );
        }
        // Running frame is quiet too: no elapsed chatter, still one header.
        let rows = render_rows(
            60,
            "demo.yaml",
            "diagnose",
            1,
            3,
            None,
            0,
            0.0,
            Duration::from_millis(400),
            false,
        );
        let stripped: Vec<String> = rows.iter().map(|r| strip_ansi(r)).collect();
        for l in &stripped {
            assert!(!l.contains("elapsed"), "running footer is quiet: {stripped:?}");
            assert!(!l.contains("----------"), "running footer is quiet: {stripped:?}");
        }
        assert_eq!(
            stripped.iter().filter(|l| l.contains("demo.yaml")).count(),
            1,
            "running header exactly once: {stripped:?}"
        );
    }

    #[test]
    fn dotenv_finds_env_by_walking_up_from_backend_binary() {
        use std::sync::atomic::{AtomicU64, Ordering};
        static CTR: AtomicU64 = AtomicU64::new(0);
        let n = CTR.fetch_add(1, Ordering::SeqCst);
        let base = std::env::temp_dir().join(format!(
            "tui_dotenv_find_{}_{n}",
            std::process::id()
        ));
        let nested = base.join("a").join("b");
        std::fs::create_dir_all(&nested).unwrap();
        // `.env` lives at the repo root; backend binary is nested below it.
        std::fs::write(base.join(".env"), "TUI_FIND_WALK_UP=1\n").unwrap();
        let backend = nested.join("afterglow");
        std::fs::write(&backend, "#!/bin/sh\nexit 0\n").unwrap();
        let found = find_dotenv_for_backend(&backend).expect("must find walked-up .env");
        assert!(
            found.is_file(),
            "finder must return an existing file, got {}",
            found.display()
        );
        assert_eq!(
            found.file_name().unwrap(),
            ".env",
            "finder must return a .env file"
        );
        // The found file must be the root one (sibling `../.env` walk-up).
        let canonical_found = found.canonicalize().unwrap();
        let canonical_want = base.join(".env").canonicalize().unwrap();
        assert_eq!(canonical_found, canonical_want);
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn dotenv_sibling_dotdot_and_missing_are_silent_noops() {
        use std::sync::atomic::{AtomicU64, Ordering};
        static CTR2: AtomicU64 = AtomicU64::new(0);
        let n = CTR2.fetch_add(1, Ordering::SeqCst);
        let dir = std::env::temp_dir().join(format!(
            "tui_dotenv_sib_{}_{n}",
            std::process::id()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join(".env"), "TUI_SIB_DOTDOT=sibval\n").unwrap();
        let backend = dir.join("afterglow");
        std::fs::write(&backend, "#!/bin/sh\nexit 0\n").unwrap();
        // `../.env` relative to the backend binary == sibling `.env`
        // (`parent/.env`; a literal `join("../.env")` through the file
        // path hits ENOTDIR, so the finder uses the parent dir).
        let sibling = backend.parent().unwrap().join(".env");
        assert!(sibling.is_file(), "sibling .env must exist");
        let found = find_dotenv_for_backend(&backend).expect("sibling .env must be found");
        assert!(found.is_file());
        // auto_load picks it up without overriding and without printing.
        std::env::remove_var("TUI_SIB_DOTDOT");
        auto_load_dotenv(&backend);
        assert_eq!(std::env::var("TUI_SIB_DOTDOT").unwrap(), "sibval");
        std::env::remove_var("TUI_SIB_DOTDOT");
        let _ = std::fs::remove_dir_all(&dir);
        // Missing file is a silent no-op (no panic, no env change).
        let missing = std::path::PathBuf::from("/definitely/missing/tui_no_env_987654321/.env");
        assert!(!missing.is_file());
        load_dotenv_file(&missing);
        assert!(std::env::var_os("TUI_SIB_DOTDOT").is_none());
    }
}
