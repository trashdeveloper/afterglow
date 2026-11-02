//! `tui doctor` line parser + checklist renderer.
//!
//! Backend contract: the TUI spawns `<repo-root>/afterglow doctor` with
//! `AFTERGLOW_NO_TUI=1` in the child's environment (so the wrapper takes
//! the plain-text path and no TUI→backend→TUI exec cycle is possible) and
//! parses its stdout live. Today's plain format (see
//! `agent/doctor.py::format_line` + `afterglow::cmd_doctor`) is
//! `STATUS: detail` per check — NOT the `name: STATUS -- message` shape
//! the task brief sketches. Flagged ambiguity, resolved against the
//! observed backend (fixture: `tests/fixtures/doctor_plain_stdout.txt`):
//! no emitted line carries a `name:` prefix, so names are never parsed
//! from check rows. Observed shapes:
//! - `PASS: ...` / `FAIL: ...` / `SKIP: ...` → one checklist row each.
//! - `ok: ...` → PASS row (the api-key presence line carries no `PASS:`
//!   prefix: `ok: MODEL_API_KEY found in .env ...`).
//! - `  fix (name): one-command fix` (printed only after a FAIL verdict,
//!   in results order) → one-line hint attached, in arrival order, under
//!   each FAIL row (FAIL row *i* gets fix line *i*; extras ignored).
//! - `doctor: ok ...` / `doctor: FAIL ...` → final verdict.
//! - `afterglow doctor: checking ...` header + `fix the line(s) ...`
//!   trailers → ignored (counted, see below).
//!
//! The parser is total: blank lines yield `Blank` (uncounted); anything
//! else unrecognized yields `Ignored` and bumps `DoctorState::ignored` —
//! malformed lines never break the render loop, and the miss is
//! observable (the "ignored loudly" half: an explicit counter plus tests
//! pinning it, mirroring `events.rs`' total-parser precedent).
//!
//! Rendering is minimal by contract: 1 header row, 1 row per check, the
//! fix hints, 1 verdict row. No percentages anywhere, and the braille
//! spinner exists only while the backend is still running (it resolves
//! into the verdict line — no spinner survives completion).

use ratatui::prelude::*;
use ratatui::widgets::Paragraph;

use crate::bar;

/// One parsed backend stdout line.
#[derive(Debug, Clone, PartialEq)]
pub enum DoctorEvent {
    /// `PASS:` / `FAIL:` / `SKIP:` / `ok:` check row.
    Check(Check),
    /// `  fix (name): hint` one-line fix hint (arrival order).
    Fix { name: String, hint: String },
    /// `doctor: ok ...` (`true`) / `doctor: FAIL ...` (`false`).
    Verdict { ok: bool, line: String },
    /// `afterglow doctor: checking ...` banner (row-equivalent, skipped).
    Header,
    /// Blank line (skipped, uncounted).
    Blank,
    /// Anything else: skipped, counted in [`DoctorState::ignored`].
    Ignored,
}

/// One checklist row.
#[derive(Debug, Clone, PartialEq)]
pub struct Check {
    pub pass: Option<bool>,
    pub message: String,
}

/// Parse one backend stdout line. Total: never panics, never errors —
/// unparseable lines are `Ignored`/`Blank`, never a stall.
pub fn parse_doctor_line(line: &str) -> DoctorEvent {
    let t = line.trim();
    if t.is_empty() {
        return DoctorEvent::Blank;
    }
    if let Some(rest) = t.strip_prefix("PASS:") {
        return DoctorEvent::Check(Check {
            pass: Some(true),
            message: format!("PASS:{}", rest),
        });
    }
    if let Some(rest) = t.strip_prefix("FAIL:") {
        return DoctorEvent::Check(Check {
            pass: Some(false),
            message: format!("FAIL:{}", rest),
        });
    }
    if let Some(rest) = t.strip_prefix("SKIP:") {
        return DoctorEvent::Check(Check {
            pass: None,
            message: format!("SKIP:{}", rest),
        });
    }
    // Observed api-key presence line has no `PASS:` prefix.
    if t.starts_with("ok:") {
        return DoctorEvent::Check(Check {
            pass: Some(true),
            message: t.to_string(),
        });
    }
    // `  fix (name): hint` — printed aggregated after a FAIL verdict.
    if let Some(rest) = t.strip_prefix("fix (") {
        if let Some(end) = rest.find("):") {
            let name = rest[..end].trim().to_string();
            let hint = rest[end + 2..].trim().to_string();
            if !name.is_empty() && !hint.is_empty() {
                return DoctorEvent::Fix { name, hint };
            }
        }
        return DoctorEvent::Ignored;
    }
    if t.starts_with("doctor: ok") {
        return DoctorEvent::Verdict {
            ok: true,
            line: t.to_string(),
        };
    }
    if t.starts_with("doctor: FAIL") {
        return DoctorEvent::Verdict {
            ok: false,
            line: t.to_string(),
        };
    }
    if t.starts_with("afterglow doctor:") {
        return DoctorEvent::Header;
    }
    DoctorEvent::Ignored
}

/// Live checklist state fed by backend stdout lines.
#[derive(Debug, Default)]
pub struct DoctorState {
    pub checks: Vec<Check>,
    pub fixes: Vec<(String, String)>,
    pub verdict: Option<(bool, String)>,
    /// Malformed (non-blank, unrecognized) lines seen so far.
    pub ignored: u64,
}

impl DoctorState {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn handle_line(&mut self, line: &str) {
        match parse_doctor_line(line) {
            DoctorEvent::Check(c) => self.checks.push(c),
            DoctorEvent::Fix { name, hint } => self.fixes.push((name, hint)),
            DoctorEvent::Verdict { ok, line } => {
                self.verdict = Some((ok, line));
            }
            DoctorEvent::Header | DoctorEvent::Blank => {}
            DoctorEvent::Ignored => {
                self.ignored += 1;
            }
        }
    }

    pub fn finished(&self) -> bool {
        self.verdict.is_some()
    }
}

/// Truncate to `max` chars (never splits mid-char; no wrapping — every
/// logical row stays exactly one terminal row so the inline painter's
/// cursor-up math holds).
fn truncate_to(s: &str, max: usize) -> String {
    let chars: Vec<char> = s.chars().collect();
    if chars.len() <= max {
        return s.to_string();
    }
    chars[..max.max(1) - 1].iter().collect::<String>() + "…"
}

/// Lay out one checklist frame with ratatui widgets into per-row ANSI
/// strings (no trailing newlines, no cursor motion — same headless
/// pattern as the run-mode renderer). Row budget: exactly
/// `1 + checks.len() + min(fail_count, fixes.len()) + 1`.
pub fn render_doctor_rows(
    width: usize,
    state: &DoctorState,
    finished: bool,
    elapsed_ms: u64,
) -> Vec<String> {
    let mut lines: Vec<Line> = Vec::new();

    // Header (exactly one).
    lines.push(Line::from(vec![
        Span::styled("◈ ", Style::default().fg(Color::Rgb(0x8f, 0x8f, 0x96))),
        Span::styled(
            "afterglow doctor",
            Style::default()
                .fg(Color::White)
                .add_modifier(Modifier::BOLD),
        ),
    ]));

    // One row per check; fix hint (arrival order) under each FAIL.
    let mut fail_idx = 0usize;
    for c in &state.checks {
        let (glyph, color) = match c.pass {
            Some(true) => ("✓ ", Color::LightGreen),
            Some(false) => ("✗ ", Color::LightRed),
            None => ("○ ", Color::DarkGray),
        };
        let text = truncate_to(&format!("{glyph}{}", c.message), width);
        let style = if c.pass == Some(false) {
            Style::default().fg(color).add_modifier(Modifier::BOLD)
        } else {
            Style::default().fg(color)
        };
        lines.push(Line::from(vec![Span::styled(text, style)]));
        if c.pass == Some(false) {
            if let Some((name, hint)) = state.fixes.get(fail_idx) {
                let fix_text =
                    truncate_to(&format!("  → fix ({name}): {hint}"), width);
                lines.push(Line::from(vec![Span::styled(
                    fix_text,
                    Style::default().fg(Color::Yellow),
                )]));
            }
            fail_idx += 1;
        }
    }

    // Verdict row while running: spinner that MUST resolve on completion.
    // When finished: the backend's own verdict line, color-coded.
    if finished {
        match state.verdict.as_ref() {
            Some((true, line)) => lines.push(Line::from(vec![Span::styled(
                truncate_to(line, width),
                Style::default()
                    .fg(Color::LightGreen)
                    .add_modifier(Modifier::BOLD),
            )])),
            Some((false, line)) => lines.push(Line::from(vec![Span::styled(
                truncate_to(line, width),
                Style::default()
                    .fg(Color::LightRed)
                    .add_modifier(Modifier::BOLD),
            )])),
            None => lines.push(Line::from(vec![Span::styled(
                truncate_to("doctor: FAIL (backend exited, no verdict line)", width),
                Style::default()
                    .fg(Color::LightRed)
                    .add_modifier(Modifier::BOLD),
            )])),
        }
    } else {
        let spinner = bar::spinner_at(elapsed_ms);
        lines.push(Line::from(vec![Span::styled(
            truncate_to(&format!("{spinner} checking ..."), width),
            Style::default().fg(Color::Rgb(0x8f, 0x8f, 0x96)),
        )]));
    }

    let height: u16 = lines.len().clamp(1, u16::MAX as usize) as u16;
    let width_u16: u16 = width.clamp(1, u16::MAX as usize) as u16;
    let backend = ratatui::backend::TestBackend::new(width_u16, height);
    let mut terminal = ratatui::Terminal::new(backend).expect("headless terminal");
    terminal
        .draw(|frame| {
            let para = Paragraph::new(lines.clone());
            frame.render_widget(para, frame.area());
        })
        .expect("headless draw");
    crate::buffer_to_ansi(terminal.backend().buffer())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Strip CSI sequences (`\x1b[...m`, `\x1b[K`, `\x1b[<n>A`, …) and
    /// `\r` so tests count visible text, not paint bytes.
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

    fn stripped(rows: &[String]) -> Vec<String> {
        rows.iter().map(|r| strip_ansi(r)).collect()
    }

    #[test]
    fn parses_pass_fail_skip_check_rows() {
        assert_eq!(
            parse_doctor_line("PASS: python 3.12.3 (>= 3.10)"),
            DoctorEvent::Check(Check {
                pass: Some(true),
                message: "PASS: python 3.12.3 (>= 3.10)".to_string(),
            })
        );
        assert_eq!(
            parse_doctor_line("FAIL: verilator not found on PATH"),
            DoctorEvent::Check(Check {
                pass: Some(false),
                message: "FAIL: verilator not found on PATH".to_string(),
            })
        );
        assert_eq!(
            parse_doctor_line("SKIP: suite smoke not run (1917 tests are slow) -- run `python3 -m unittest discover -s tests` for the full gate"),
            DoctorEvent::Check(Check {
                pass: None,
                message: "SKIP: suite smoke not run (1917 tests are slow) -- run `python3 -m unittest discover -s tests` for the full gate".to_string(),
            })
        );
    }

    #[test]
    fn parses_ok_presence_line_as_pass() {
        // The api-key PASS line carries no `PASS:` prefix (observed).
        let ev = parse_doctor_line(
            "ok: MODEL_API_KEY found in .env (presence only, value never shown)",
        );
        assert_eq!(
            ev,
            DoctorEvent::Check(Check {
                pass: Some(true),
                message: "ok: MODEL_API_KEY found in .env (presence only, value never shown)"
                    .to_string(),
            })
        );
    }

    #[test]
    fn parses_fix_hint_with_name() {
        assert_eq!(
            parse_doctor_line("  fix (python): install Python >= 3.10"),
            DoctorEvent::Fix {
                name: "python".to_string(),
                hint: "install Python >= 3.10".to_string(),
            }
        );
    }

    #[test]
    fn parses_verdict_lines() {
        assert!(matches!(
            parse_doctor_line("doctor: ok -- this machine can attempt a first run."),
            DoctorEvent::Verdict { ok: true, .. }
        ));
        assert!(matches!(
            parse_doctor_line("doctor: FAIL -- one command per miss:"),
            DoctorEvent::Verdict { ok: false, .. }
        ));
    }

    #[test]
    fn malformed_lines_are_ignored_loudly() {
        // Blank lines: skipped, uncounted.
        assert_eq!(parse_doctor_line(""), DoctorEvent::Blank);
        assert_eq!(parse_doctor_line("   "), DoctorEvent::Blank);
        // Everything else unrecognized: Ignored (counted in state).
        for junk in [
            "hello world",
            "[1,2,3]",
            "{\"type\":\"progress\"}",
            "fix (): empty name",
            "fix (name):",
            "doctor: maybe later",
            "afterglow doctor",
        ] {
            assert_eq!(parse_doctor_line(junk), DoctorEvent::Ignored, "{junk:?}");
        }
        let mut st = DoctorState::new();
        st.handle_line("PASS: python 3.12.3 (>= 3.10)");
        st.handle_line("hello world");
        st.handle_line("");
        st.handle_line("afterglow doctor: checking this machine");
        assert_eq!(st.checks.len(), 1);
        assert_eq!(st.ignored, 1, "exactly the malformed line is counted");
        assert!(!st.finished());
    }

    #[test]
    fn pending_frame_has_spinner_and_no_verdict() {
        let mut st = DoctorState::new();
        st.handle_line("PASS: python 3.12.3 (>= 3.10)");
        st.handle_line("FAIL: verilator not found on PATH");
        let rows = render_doctor_rows(80, &st, false, 400);
        let body = stripped(&rows).join("\n");
        // Header exactly once.
        assert_eq!(
            stripped(&rows)
                .iter()
                .filter(|l| l.contains("afterglow doctor"))
                .count(),
            1,
            "one header: {body:?}"
        );
        // Both check rows present with glyphs.
        assert!(body.contains("✓ PASS: python"), "{body:?}");
        assert!(body.contains("✗ FAIL: verilator"), "{body:?}");
        // Pending: braille spinner visible, no verdict line yet.
        assert!(
            bar::SPINNER.iter().any(|g| body.contains(g)),
            "pending frame must spin: {body:?}"
        );
        assert!(!body.contains("doctor: ok"), "{body:?}");
        assert!(!body.contains("doctor: FAIL"), "{body:?}");
        // Inline-only: no alternate-screen, no percentages.
        for forbidden in ["1049h", "1049l", "?25l", "6n"] {
            assert!(!rows.join("\n").contains(forbidden), "{forbidden}");
        }
        assert!(!body.contains('%'), "no percentages: {body:?}");
    }

    #[test]
    fn done_frame_resolves_spinner_and_pins_verdict() {
        let mut st = DoctorState::new();
        st.handle_line("PASS: python 3.12.3 (>= 3.10)");
        st.handle_line("FAIL: verilator not found on PATH");
        st.handle_line("doctor: FAIL -- one command per miss:");
        st.handle_line("  fix (verilator): `sudo apt install verilator`");
        st.handle_line("fix the line(s) above, then re-run `afterglow doctor`.");
        assert!(st.finished());
        // Trailer prose is ignored loudly, not a row.
        assert_eq!(st.ignored, 1);
        let rows = render_doctor_rows(80, &st, true, 400);
        let plain = stripped(&rows);
        let body = plain.join("\n");
        // 1 header + 2 check rows + 1 fix hint + 1 verdict.
        assert_eq!(rows.len(), 5, "{plain:?}");
        assert_eq!(
            plain.iter().filter(|l| l.contains("◈")).count(),
            1,
            "exactly one header: {plain:?}"
        );
        assert_eq!(
            plain
                .iter()
                .filter(|l| l.contains("doctor: FAIL"))
                .count(),
            1,
            "exactly one verdict line: {plain:?}"
        );
        // Fix hint sits under its FAIL row, one line.
        let fail_pos = plain.iter().position(|l| l.contains("✗ FAIL:")).unwrap();
        assert!(
            plain[fail_pos + 1].contains("→ fix (verilator):"),
            "hint directly under FAIL: {plain:?}"
        );
        // Spinner resolved: no braille survives completion.
        assert!(
            !bar::SPINNER.iter().any(|g| body.contains(g)),
            "no spinner after done: {body:?}"
        );
        for forbidden in ["1049h", "1049l", "?25l", "6n"] {
            assert!(!rows.join("\n").contains(forbidden), "{forbidden}");
        }
    }

    #[test]
    fn ok_verdict_frame_is_green_single_verdict() {
        let mut st = DoctorState::new();
        st.handle_line("PASS: python 3.12.3 (>= 3.10)");
        st.handle_line(
            "ok: MODEL_API_KEY found in .env (presence only, value never shown)",
        );
        st.handle_line("SKIP: suite smoke not run (1917 tests are slow)");
        st.handle_line("doctor: ok -- this machine can attempt a first run.");
        let rows = render_doctor_rows(80, &st, true, 0);
        let plain = stripped(&rows);
        let body = plain.join("\n");
        assert_eq!(rows.len(), 5, "{plain:?}");
        assert!(body.contains("✓ PASS: python"), "{body:?}");
        assert!(body.contains("✓ ok: MODEL_API_KEY"), "{body:?}");
        assert!(body.contains("○ SKIP:"), "{body:?}");
        assert_eq!(
            plain.iter().filter(|l| l.contains("doctor:")).count(),
            1,
            "exactly one verdict line: {plain:?}"
        );
        assert!(!bar::SPINNER.iter().any(|g| body.contains(g)));
    }

    #[test]
    fn long_lines_never_wrap_rows() {
        let mut st = DoctorState::new();
        st.handle_line(&format!("PASS: {}", "x".repeat(500)));
        st.handle_line(&format!("  fix (python): {}", "y".repeat(500)));
        // FAIL row unmatched by order → no hint; add a FAIL to pin one.
        st.handle_line(&format!("FAIL: {}", "z".repeat(500)));
        st.handle_line("doctor: FAIL -- one command per miss:");
        let rows = render_doctor_rows(40, &st, true, 0);
        // 1 header + 2 checks + 1 hint (first fix → the single FAIL) + 1 verdict.
        assert_eq!(rows.len(), 5);
        for r in &rows {
            assert!(
                strip_ansi(r).chars().count() <= 41,
                "row stays one visual line: {:?}…",
                &strip_ansi(r)[..40.min(strip_ansi(r).len())]
            );
        }
    }
}
