//! Dashboard gauges run screen (fixed height, inline repaint).
//!
//! Replaces the old 4-row run frame: 1 header row (nerd-font icon +
//! case id + attempt n/N), 4 per-phase gauge rows for DIAGNOSE / PATCH /
//! RESIM / VERIFY, 1 live counters row (elapsed s, attempts used, est.
//! cost or `—`), and 1 verdict/status row (spinner + phase while
//! running; big █ block-art verdict + patch lines + seconds when done).
//! Always exactly [`DASHBOARD_HEIGHT`] rows — the inline painter's
//! cursor-up math depends on the fixed height.
//!
//! Gauge states are driven ONLY by the existing `--emit-events jsonl`
//! stream (`progress` / `attempt` / `verdict`, see `events.rs`) — no new
//! backend protocol:
//! - `progress` with a known phase → that gauge `Active`; every earlier
//!   gauge still `Pending`/`Active` becomes `Done`. Later gauges are
//!   untouched, EXCEPT an `Active` later gauge resets to `Pending` (a new
//!   attempt restarted at an earlier phase; `Done` means ever completed,
//!   so it persists across attempts).
//! - Any `verdict` event → every `Active` becomes `Done`, every still
//!   `Pending` becomes `Skipped` (visited = done, unvisited = skipped).
//! - Unknown/missing phases (`red`, `collect`, `starting`, `working`,
//!   future names, garbage) are ignored: gauges stay dim, never stall.
//!
//! Rendering per gauge (thin bar, no percentages/ETAs anywhere):
//! - Pending: dim track ([`bar::TRACK`]).
//! - Active: tab-pulse sweep band ([`bar::render_bar`]).
//! - Done: solid gradient ([`bar::gradient_at`] across the row).
//! - Skipped: dim `〜` wave dashes. `〜` (U+301C) is terminal-wide (2
//!   columns), so the row repeats it `width/2` times (+ a space pad on
//!   odd widths) to keep the row exactly one terminal row.
//!
//! Ambiguities flagged (resolved literally): est. cost has no backend
//! emitter today, so the counters row shows `—` unless a future event
//! carries `est_cost`/`cost` (parsed tolerantly in `events.rs`); the
//! block-art verdict is a single █-framed row (multi-line FIGlet would
//! break the fixed-height inline repaint); TRIAGE closes gauges like
//! GREEN/RED with a yellow verdict row.

use ratatui::prelude::*;
use ratatui::widgets::Paragraph;

use crate::{bar, events};

/// Dashboard phases, in pipeline order.
pub const PHASES: [&str; 4] = ["DIAGNOSE", "PATCH", "RESIM", "VERIFY"];

/// Fixed dashboard frame height: header + 4 gauges + counters + verdict.
pub const DASHBOARD_HEIGHT: u16 = 7;

/// One phase gauge state.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Gauge {
    #[default]
    Pending,
    Active,
    Done,
    Skipped,
}

/// Live gauge states fed by the JSONL event stream.
#[derive(Debug, Clone, Default)]
pub struct Dashboard {
    pub gauges: [Gauge; 4],
}

impl Dashboard {
    pub fn new() -> Self {
        Self::default()
    }

    /// Index into [`PHASES`] for a backend phase name (case-insensitive;
    /// the backend emits lowercase). Unknown → `None` (dim, never stall).
    pub fn phase_index(phase: &str) -> Option<usize> {
        let up = phase.trim().to_ascii_uppercase();
        PHASES.iter().position(|p| *p == up)
    }

    /// Advance the state machine for one observed backend phase.
    pub fn on_phase(&mut self, phase: &str) {
        let Some(idx) = Self::phase_index(phase) else {
            return;
        };
        for (i, g) in self.gauges.iter_mut().enumerate() {
            if i < idx && matches!(g, Gauge::Pending | Gauge::Active) {
                *g = Gauge::Done;
            } else if i == idx {
                *g = Gauge::Active;
            } else if i > idx && matches!(g, Gauge::Active) {
                // New attempt restarted at an earlier phase: abandon the
                // in-flight later gauge (`Done` = ever completed, persists).
                *g = Gauge::Pending;
            }
        }
    }

    /// Close out the gauges on any terminal verdict.
    pub fn on_verdict(&mut self) {
        for g in self.gauges.iter_mut() {
            match g {
                Gauge::Active => *g = Gauge::Done,
                Gauge::Pending => *g = Gauge::Skipped,
                Gauge::Done | Gauge::Skipped => {}
            }
        }
    }

    /// Feed one stdout line. Total: unknown shapes are ignored.
    pub fn handle_line(&mut self, line: &str) {
        match events::parse_line(line) {
            Some(events::Event::Progress { phase, .. }) => self.on_phase(&phase),
            Some(events::Event::Verdict { .. }) => self.on_verdict(),
            _ => {}
        }
    }
}

/// Label style per gauge state (the bar itself carries the state too).
fn label_style(gauge: Gauge) -> Style {
    match gauge {
        Gauge::Pending => Style::default().fg(Color::DarkGray),
        Gauge::Active => Style::default()
            .fg(Color::White)
            .add_modifier(Modifier::BOLD),
        Gauge::Done => Style::default().fg(Color::LightGreen),
        Gauge::Skipped => Style::default().fg(Color::DarkGray),
    }
}

/// Bar cells for the narrow-glyph gauges (Pending/Active/Done). Skipped
/// uses the wide `〜` and needs custom width math (see [`gauge_spans`]).
fn gauge_cells(gauge: Gauge, width: usize, elapsed_s: f64) -> Vec<bar::Cell> {
    match gauge {
        Gauge::Pending | Gauge::Skipped => vec![
            bar::Cell {
                glyph: bar::TRACK_GLYPH,
                rgb: bar::TRACK,
            };
            width
        ],
        Gauge::Active => bar::render_bar(width, 0.0, elapsed_s),
        Gauge::Done => (0..width)
            .map(|i| {
                let t = if width <= 1 {
                    1.0
                } else {
                    i as f64 / (width - 1) as f64
                };
                bar::Cell {
                    glyph: bar::TRACK_GLYPH,
                    rgb: bar::gradient_at(t),
                }
            })
            .collect(),
    }
}

/// Bar spans covering exactly `bar_w` terminal columns.
fn gauge_spans(gauge: Gauge, bar_w: usize, elapsed_s: f64) -> Vec<Span<'static>> {
    if gauge == Gauge::Skipped {
        // `〜` is 2 columns wide: halve the repeats (+ space pad on odd).
        let mut s = "〜".repeat(bar_w / 2);
        if bar_w % 2 == 1 {
            s.push(' ');
        }
        return vec![Span::styled(s, Style::default().fg(Color::DarkGray))];
    }
    gauge_cells(gauge, bar_w, elapsed_s)
        .iter()
        .map(|c| {
            Span::styled(
                c.glyph.to_string(),
                Style::default().fg(Color::Rgb(c.rgb.0, c.rgb.1, c.rgb.2)),
            )
        })
        .collect()
}

/// (verdict row text, color) for the final row: big █ block-art +
/// patch lines + seconds, never percentages/ETAs.
fn verdict_line(verdict: Option<&str>, patch_lines: u32, seconds: f64) -> (String, Color) {
    match verdict {
        Some("GREEN") => (
            format!("████ GREEN ████ · fixed · {patch_lines} patch lines · {seconds:.1}s"),
            Color::LightGreen,
        ),
        Some("TRIAGE") => (
            format!("████ TRIAGE ████ · diagnosis saved, patch withheld · {seconds:.1}s"),
            Color::Yellow,
        ),
        Some("RED") => (
            format!("████ RED ████ · not fixed · {seconds:.1}s"),
            Color::LightRed,
        ),
        _ => (
            "████ RED ████ · backend failed (no verdict event)".to_string(),
            Color::LightRed,
        ),
    }
}

/// Truncate to `max` chars (never splits mid-char; every logical row
/// stays exactly one terminal row for the inline painter).
fn truncate_to(s: &str, max: usize) -> String {
    let chars: Vec<char> = s.chars().collect();
    if chars.len() <= max {
        return s.to_string();
    }
    if max == 0 {
        return String::new();
    }
    chars[..max - 1].iter().collect::<String>() + "…"
}

/// Lay out one dashboard frame with ratatui widgets into per-row ANSI
/// strings (no trailing newlines, no cursor motion — same headless
/// pattern as the doctor checklist renderer). Always exactly
/// [`DASHBOARD_HEIGHT`] rows.
#[allow(clippy::too_many_arguments)]
pub fn render_dashboard_rows(
    width: usize,
    title: &str,
    phase: &str,
    attempt: u32,
    total: u32,
    gauges: &[Gauge; 4],
    est_cost: Option<f64>,
    elapsed: std::time::Duration,
    verdict: Option<&str>,
    patch_lines: u32,
    seconds: f64,
    finished: bool,
) -> Vec<String> {
    let elapsed_s = elapsed.as_secs_f64();
    let elapsed_ms = elapsed.as_millis() as u64;
    let mut lines: Vec<Line> = Vec::with_capacity(DASHBOARD_HEIGHT as usize);

    // Header: nerd-font icon + case id + attempt n/N.
    let attempt_text = if total > 0 {
        format!("{}/{}", attempt.max(1), total)
    } else if attempt > 0 {
        format!("{attempt}")
    } else {
        "—".to_string()
    };
    let title_budget = width.saturating_sub(2 + 1 + attempt_text.chars().count());
    let shown_title = truncate_to(title, title_budget);
    lines.push(Line::from(vec![
        Span::styled("◈ ", Style::default().fg(Color::Rgb(0x8f, 0x8f, 0x96))),
        Span::styled(
            format!("{shown_title} {attempt_text}"),
            Style::default()
                .fg(Color::White)
                .add_modifier(Modifier::BOLD),
        ),
    ]));

    // Per-phase gauge rows: padded label + thin bar covering the rest.
    let bar_w = width.saturating_sub(8 + 1);
    for (i, label) in PHASES.iter().enumerate() {
        let mut spans = vec![Span::styled(
            format!("{label:<8} "),
            label_style(gauges[i]),
        )];
        spans.extend(gauge_spans(gauges[i], bar_w, elapsed_s));
        lines.push(Line::from(spans));
    }

    // Live counters row: elapsed s, attempts used, est. cost or `—`.
    let cost_text = match est_cost {
        Some(c) => format!("${c:.2}"),
        None => "—".to_string(),
    };
    lines.push(Line::from(vec![Span::styled(
        truncate_to(
            &format!(
                "elapsed {elapsed_s:.1}s · attempts {attempt} · est. cost {cost_text}"
            ),
            width,
        ),
        Style::default().fg(Color::Rgb(0x8f, 0x8f, 0x96)),
    )]));

    // Verdict/status row: spinner + phase while running, block-art when done.
    if finished {
        let (text, color) = verdict_line(verdict, patch_lines, seconds);
        lines.push(Line::from(vec![Span::styled(
            truncate_to(&text, width),
            Style::default().fg(color).add_modifier(Modifier::BOLD),
        )]));
    } else {
        let spinner = bar::spinner_at(elapsed_ms);
        lines.push(Line::from(vec![Span::styled(
            truncate_to(&format!("{spinner} {phase} ..."), width),
            Style::default().fg(Color::Rgb(0x8f, 0x8f, 0x96)),
        )]));
    }

    debug_assert_eq!(lines.len(), DASHBOARD_HEIGHT as usize);
    let width_u16: u16 = width.clamp(1, u16::MAX as usize) as u16;
    let backend = ratatui::backend::TestBackend::new(width_u16, DASHBOARD_HEIGHT);
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
    /// `\r` so tests count visible text, not paint bytes (same helper
    /// shape as `main.rs` / `doctor.rs`).
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

    fn progress(phase: &str) -> String {
        format!(
            "{{\"type\": \"progress\", \"phase\": \"{phase}\", \"attempt\": 1, \"total\": 2, \"elapsed_s\": 4}}"
        )
    }

    #[test]
    fn phase_index_matches_backend_names_case_insensitively() {
        assert_eq!(Dashboard::phase_index("diagnose"), Some(0));
        assert_eq!(Dashboard::phase_index("PATCH"), Some(1));
        assert_eq!(Dashboard::phase_index(" Resim "), Some(2));
        assert_eq!(Dashboard::phase_index("verify"), Some(3));
        for unknown in ["", "red", "collect", "starting", "working", "GREEN", "bogus"] {
            assert_eq!(Dashboard::phase_index(unknown), None, "{unknown:?}");
        }
    }

    #[test]
    fn gauge_state_machine_pending_active_done_per_event_sequence() {
        let mut d = Dashboard::new();
        assert!(d.gauges.iter().all(|g| *g == Gauge::Pending));
        d.handle_line(&progress("diagnose"));
        assert_eq!(
            d.gauges,
            [Gauge::Active, Gauge::Pending, Gauge::Pending, Gauge::Pending]
        );
        d.handle_line(&progress("patch"));
        assert_eq!(
            d.gauges,
            [Gauge::Done, Gauge::Active, Gauge::Pending, Gauge::Pending]
        );
        d.handle_line(&progress("resim"));
        assert_eq!(
            d.gauges,
            [Gauge::Done, Gauge::Done, Gauge::Active, Gauge::Pending]
        );
        d.handle_line(&progress("verify"));
        assert_eq!(
            d.gauges,
            [Gauge::Done, Gauge::Done, Gauge::Done, Gauge::Active]
        );
        d.handle_line(
            r#"{"type": "verdict", "verdict": "GREEN", "run_id": "r", "patch_lines": 7, "seconds": 12.5}"#,
        );
        assert_eq!(d.gauges, [Gauge::Done; 4]);
    }

    #[test]
    fn unknown_and_missing_phases_stay_dim_never_stall() {
        let mut d = Dashboard::new();
        for junk in [
            "",
            "   ",
            "hello",
            "[1,2,3]",
            "{\"type\":\"nope\"}",
            "{\"type\": \"attempt\", \"attempt\": 1, \"phase\": \"start\"}",
            "{\"type\": \"progress\", \"done\": true}",
            &progress("red"),
            &progress("collect"),
            &progress("starting"),
            &progress("working"),
            &progress("future-phase"),
            "{\"type\": \"verdict\", \"verdict\": \"MAYBE\"}",
        ] {
            d.handle_line(junk);
        }
        // Nothing known seen: every gauge still dim Pending.
        assert!(d.gauges.iter().all(|g| *g == Gauge::Pending));
        // Renders a full dim frame without stalling.
        let rows = render_dashboard_rows(
            60,
            "demo.yaml",
            "starting",
            0,
            0,
            &d.gauges,
            None,
            std::time::Duration::from_millis(400),
            None,
            0,
            0.0,
            false,
        );
        assert_eq!(rows.len(), DASHBOARD_HEIGHT as usize);
        let plain = stripped(&rows).join("\n");
        for label in PHASES {
            assert!(plain.contains(label), "missing gauge row {label}: {plain:?}");
        }
    }

    #[test]
    fn verdict_with_missing_phases_marks_them_skipped() {
        let mut d = Dashboard::new();
        d.handle_line(&progress("diagnose"));
        d.handle_line(
            r#"{"type": "verdict", "verdict": "RED", "run_id": "r", "patch_lines": 0, "seconds": 3.0}"#,
        );
        assert_eq!(
            d.gauges,
            [Gauge::Done, Gauge::Skipped, Gauge::Skipped, Gauge::Skipped]
        );
        // Skipped rows render the dim 〜 marker.
        let rows = render_dashboard_rows(
            60,
            "demo.yaml",
            "diagnose",
            1,
            2,
            &d.gauges,
            None,
            std::time::Duration::from_secs(3),
            Some("RED"),
            0,
            3.0,
            true,
        );
        let plain = stripped(&rows).join("\n");
        assert!(plain.contains('〜'), "skipped gauges must show 〜: {plain:?}");
    }

    #[test]
    fn backward_phase_jump_restarts_cycle_for_new_attempt() {
        let mut d = Dashboard::new();
        d.handle_line(&progress("diagnose"));
        d.handle_line(&progress("patch"));
        d.handle_line(&progress("resim"));
        assert_eq!(
            d.gauges,
            [Gauge::Done, Gauge::Done, Gauge::Active, Gauge::Pending]
        );
        // Attempt 2 restarts at DIAGNOSE: in-flight RESIM abandoned,
        // ever-completed PATCH stays Done.
        d.handle_line(&progress("diagnose"));
        assert_eq!(
            d.gauges,
            [Gauge::Active, Gauge::Done, Gauge::Pending, Gauge::Pending]
        );
    }

    #[test]
    fn frame_row_count_is_fixed_seven_running_and_finished() {
        let running = [Gauge::Pending, Gauge::Active, Gauge::Pending, Gauge::Pending];
        let rows = render_dashboard_rows(
            60,
            "demo.yaml",
            "patch",
            1,
            3,
            &running,
            None,
            std::time::Duration::from_millis(400),
            None,
            0,
            0.0,
            false,
        );
        assert_eq!(rows.len(), DASHBOARD_HEIGHT as usize);
        assert_eq!(DASHBOARD_HEIGHT, 7);
        let done = [Gauge::Done; 4];
        let rows = render_dashboard_rows(
            60,
            "demo.yaml",
            "verify",
            3,
            3,
            &done,
            None,
            std::time::Duration::from_secs(15),
            Some("GREEN"),
            7,
            12.5,
            true,
        );
        assert_eq!(rows.len(), DASHBOARD_HEIGHT as usize);
        // Narrow widths never change the row count either.
        for w in [10, 20, 40, 120] {
            for finished in [false, true] {
                let rows = render_dashboard_rows(
                    w,
                    "demo.yaml",
                    "patch",
                    1,
                    3,
                    &running,
                    None,
                    std::time::Duration::from_millis(400),
                    finished.then_some("GREEN"),
                    7,
                    12.5,
                    finished,
                );
                assert_eq!(rows.len(), 7, "w={w} finished={finished}");
            }
        }
    }

    #[test]
    fn canned_three_event_stream_has_no_alt_screen_bytes() {
        // Spec proof: drive gauges from a canned 3-event stream, render
        // the finished frame, assert inline-only bytes.
        let mut d = Dashboard::new();
        d.handle_line(&progress("diagnose"));
        d.handle_line(&progress("patch"));
        d.handle_line(
            r#"{"type": "verdict", "verdict": "GREEN", "run_id": "r", "patch_lines": 7, "seconds": 12.5}"#,
        );
        assert_eq!(
            d.gauges,
            [Gauge::Done, Gauge::Done, Gauge::Skipped, Gauge::Skipped]
        );
        let rows = render_dashboard_rows(
            60,
            "demo.yaml",
            "patch",
            1,
            2,
            &d.gauges,
            None,
            std::time::Duration::from_secs(13),
            Some("GREEN"),
            7,
            12.5,
            true,
        );
        assert_eq!(rows.len(), 7);
        let body = rows.join("\n");
        // Truecolor gradient present (done gauges are solid gradient).
        assert!(body.contains("38;2;"), "expected truecolor SGR");
        // Inline-only: no alternate-screen, no cursor hide, no CUP query.
        for forbidden in ["1049h", "1049l", "?25l", "6n"] {
            assert!(
                !body.contains(forbidden),
                "dashboard frame must not contain {forbidden}"
            );
        }
    }

    #[test]
    fn verdict_row_is_block_art_with_patch_lines_and_seconds() {
        for (verdict, word, patch_lines, seconds, detail) in [
            (Some("GREEN"), "GREEN", 12, 14.2, "12 patch lines"),
            (Some("RED"), "RED", 0, 3.0, "3.0s"),
            (Some("TRIAGE"), "TRIAGE", 0, 3.0, "3.0s"),
            (None, "RED", 0, 0.0, "no verdict event"),
        ] {
            let rows = render_dashboard_rows(
                70,
                "demo.yaml",
                "verify",
                3,
                3,
                &[Gauge::Done; 4],
                None,
                std::time::Duration::from_secs(15),
                verdict,
                patch_lines,
                seconds,
                true,
            );
            let plain = stripped(&rows).join("\n");
            assert!(plain.contains('█'), "block-art for {verdict:?}: {plain:?}");
            assert!(plain.contains(word), "verdict word for {verdict:?}: {plain:?}");
            assert!(
                plain.contains(detail),
                "detail {detail:?} for {verdict:?}: {plain:?}"
            );
        }
    }

    #[test]
    fn no_percentages_or_etas_anywhere() {
        let states: [[Gauge; 4]; 3] = [
            [Gauge::Pending; 4],
            [Gauge::Active, Gauge::Pending, Gauge::Pending, Gauge::Pending],
            [Gauge::Done, Gauge::Done, Gauge::Skipped, Gauge::Skipped],
        ];
        for gauges in states {
            for finished in [false, true] {
                let rows = render_dashboard_rows(
                    70,
                    "demo.yaml",
                    "patch",
                    1,
                    3,
                    &gauges,
                    Some(1.25),
                    std::time::Duration::from_millis(4400),
                    finished.then_some("GREEN"),
                    7,
                    12.5,
                    finished,
                );
                for row in rows.iter().map(|r| strip_ansi(r)) {
                    assert!(!row.contains('%'), "no percentages: {row:?}");
                    assert!(
                        !row.to_lowercase().contains("eta"),
                        "no ETAs: {row:?}"
                    );
                }
            }
        }
    }

    #[test]
    fn header_carries_case_id_and_attempt_n_of_n() {
        let rows = render_dashboard_rows(
            60,
            "uart-start-half.yaml",
            "diagnose",
            1,
            3,
            &[Gauge::Pending; 4],
            None,
            std::time::Duration::from_millis(400),
            None,
            0,
            0.0,
            false,
        );
        let plain = stripped(&rows);
        assert_eq!(
            plain.iter().filter(|l| l.contains("uart-start-half.yaml")).count(),
            1,
            "header case id exactly once: {plain:?}"
        );
        assert!(
            plain[0].contains("1/3"),
            "header shows attempt n/N: {plain:?}"
        );
        assert_eq!(
            plain.iter().filter(|l| l.contains('◈')).count(),
            1,
            "exactly one header icon: {plain:?}"
        );
        // Each phase gauge row renders exactly once.
        let body = plain.join("\n");
        for label in PHASES {
            assert_eq!(
                body.matches(label).count(),
                1,
                "gauge row {label} exactly once: {plain:?}"
            );
        }
    }

    #[test]
    fn est_cost_shows_dash_when_absent_and_dollars_when_present() {
        let render = |cost: Option<f64>| {
            stripped(&render_dashboard_rows(
                70,
                "demo.yaml",
                "patch",
                1,
                3,
                &[Gauge::Pending; 4],
                cost,
                std::time::Duration::from_millis(400),
                None,
                0,
                0.0,
                false,
            ))
            .join("\n")
        };
        assert!(render(None).contains("est. cost —"), "dash when absent");
        assert!(render(Some(1.5)).contains("est. cost $1.50"), "dollars when present");
    }

    #[test]
    fn active_gauge_sweeps_with_time_done_is_solid() {
        let gauges = [Gauge::Done, Gauge::Active, Gauge::Pending, Gauge::Pending];
        let at = |ms: u64| {
            render_dashboard_rows(
                60,
                "demo.yaml",
                "patch",
                1,
                3,
                &gauges,
                None,
                std::time::Duration::from_millis(ms),
                None,
                0,
                0.0,
                false,
            )
            .join("\n")
        };
        // Active tab-pulse band repaints with time.
        assert_ne!(at(0), at(400), "active sweep must repaint");
        // The all-Done frame still animates as a whole (counters row shows
        // elapsed time, running verdict row spins) — but the four gauge
        // rows themselves (rows 1..5) are a static solid gradient.
        let gauges_only = |ms: u64| {
            let rows = render_dashboard_rows(
                60,
                "demo.yaml",
                "verify",
                1,
                1,
                &[Gauge::Done; 4],
                None,
                std::time::Duration::from_millis(ms),
                None,
                0,
                0.0,
                false,
            );
            rows[1..5].join("\n")
        };
        assert_eq!(
            gauges_only(0),
            gauges_only(400),
            "solid done gradient is static"
        );
    }
}
