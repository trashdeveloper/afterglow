//! JSONL event parsing + event→status mapping for the `afterglow run
//! --emit-events jsonl` stream.
//!
//! Backend contract (see `tests/test_emit_events.py`, `agent/loop.py`):
//! - `{"type": "progress", "phase": ..., "attempt": N, "total": M,
//!   "elapsed_s": S}` — one per phase tick; also
//!   `{"type": "progress", "done": true}` when the bar closes.
//! - `{"type": "attempt", "attempt": N, "phase": "start"|"end",
//!   "outcome": ...}` — attempt boundaries.
//! - `{"type": "verdict", "verdict": "GREEN"|"RED"|"TRIAGE",
//!   "run_id": ..., "patch_lines": P, "seconds": S}` — exactly one,
//!   terminal.
//!
//! The parser is total: blank lines, non-JSON bytes, and unknown shapes
//! all yield `None` and never break the render loop. `LineBuffer` handles
//! "partial lines": subprocess stdout arrives in arbitrary chunks, so
//! bytes are accumulated until a newline completes a line.

use serde::Deserialize;

/// One parsed backend event (only the fields the TUI renders).
#[derive(Debug, Clone, PartialEq)]
pub enum Event {
    Progress {
        phase: String,
        attempt: u32,
        total: u32,
        elapsed_s: u64,
        /// Estimated cost in dollars when a backend emitter carries it
        /// (`est_cost`, or legacy `cost`); `None` renders as `—`. The
        /// backend emits no cost today, so this is purely tolerant.
        est_cost: Option<f64>,
    },
    ProgressDone,
    Attempt {
        attempt: u32,
        starting: bool,
    },
    Verdict {
        verdict: String,
        patch_lines: u32,
        seconds: f64,
    },
}

/// Raw wire shape: every backend event carries `"type"`.
#[derive(Debug, Deserialize)]
struct Wire {
    #[serde(rename = "type")]
    kind: String,
    phase: Option<String>,
    attempt: Option<u32>,
    total: Option<u32>,
    elapsed_s: Option<u64>,
    done: Option<bool>,
    verdict: Option<String>,
    patch_lines: Option<u32>,
    seconds: Option<f64>,
    /// Future cost key; legacy `cost` accepted as a fallback.
    est_cost: Option<f64>,
    cost: Option<f64>,
}

/// Parse one stdout line. Returns `None` for blank / invalid / unknown
/// lines (the caller skips them; backend human text never shares stdout
/// with the event stream, but a stray line must not kill the UI).
pub fn parse_line(line: &str) -> Option<Event> {
    let line = line.trim();
    if line.is_empty() {
        return None;
    }
    let w: Wire = serde_json::from_str(line).ok()?;
    match w.kind.as_str() {
        "progress" => {
            if w.done == Some(true) {
                Some(Event::ProgressDone)
            } else {
                Some(Event::Progress {
                    phase: w.phase.unwrap_or_else(|| "working".to_string()),
                    attempt: w.attempt.unwrap_or(0),
                    total: w.total.unwrap_or(0),
                    elapsed_s: w.elapsed_s.unwrap_or(0),
                    est_cost: w.est_cost.or(w.cost),
                })
            }
        }
        "attempt" => {
            let attempt = w.attempt?;
            Some(Event::Attempt {
                attempt,
                starting: w.phase.as_deref() == Some("start"),
            })
        }
        "verdict" => {
            let verdict = w.verdict?;
            if !matches!(verdict.as_str(), "GREEN" | "RED" | "TRIAGE") {
                return None;
            }
            Some(Event::Verdict {
                verdict,
                patch_lines: w.patch_lines.unwrap_or(0),
                seconds: w.seconds.unwrap_or(0.0),
            })
        }
        _ => None,
    }
}

/// Byte accumulator for subprocess stdout: `feed` takes an arbitrary
/// chunk and returns every line completed by it (without newlines).
/// Any trailing partial line stays buffered.
#[derive(Debug, Default)]
pub struct LineBuffer {
    buf: String,
}

impl LineBuffer {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn feed(&mut self, chunk: &str) -> Vec<String> {
        self.buf.push_str(chunk);
        let mut out = Vec::new();
        while let Some(pos) = self.buf.find('\n') {
            let line: String = self.buf.drain(..=pos).collect();
            out.push(line.trim_end_matches(['\n', '\r']).to_string());
        }
        out
    }

    /// Bytes still waiting for a newline (partial line).
    #[allow(dead_code)]
    pub fn pending(&self) -> &str {
        &self.buf
    }
}

/// Live UI state derived from the event stream. No subprocess here —
/// tests drive it with synthetic lines, the binary with real stdout.
#[derive(Debug, Default)]
pub struct Mapper {
    pub phase: String,
    pub attempt: u32,
    pub total: u32,
    pub verdict: Option<String>,
    pub patch_lines: u32,
    pub seconds: f64,
    pub saw_progress_done: bool,
    /// Last cost seen on any progress event (`None` = never emitted).
    pub est_cost: Option<f64>,
}

impl Mapper {
    pub fn new() -> Self {
        Self {
            phase: "starting".to_string(),
            ..Self::default()
        }
    }

    pub fn handle_line(&mut self, line: &str) {
        let Some(ev) = parse_line(line) else {
            return;
        };
        self.handle(ev);
    }

    pub fn handle(&mut self, ev: Event) {
        match ev {
            Event::Progress {
                phase,
                attempt,
                total,
                est_cost,
                ..
            } => {
                self.phase = phase;
                if attempt > 0 {
                    self.attempt = attempt;
                }
                if total > self.total {
                    self.total = total;
                }
                if est_cost.is_some() {
                    self.est_cost = est_cost;
                }
            }
            Event::ProgressDone => {
                self.saw_progress_done = true;
            }
            Event::Attempt { attempt, starting } => {
                if starting {
                    if attempt > self.attempt {
                        self.attempt = attempt;
                    }
                    if attempt > self.total {
                        self.total = attempt;
                    }
                } else if attempt == self.attempt {
                    // end boundary: keep the count, phase text stays.
                }
            }
            Event::Verdict {
                verdict,
                patch_lines,
                seconds,
            } => {
                self.verdict = Some(verdict);
                self.patch_lines = patch_lines;
                self.seconds = seconds;
            }
        }
    }

    #[allow(dead_code)]
    pub fn finished(&self) -> bool {
        self.verdict.is_some()
    }

    /// Human status line for the row under the bar.
    #[allow(dead_code)]
    pub fn status_line(&self) -> String {
        match self.verdict.as_deref() {
            Some("GREEN") => format!(
                "GREEN: fixed ({} patch lines, {:.1}s)",
                self.patch_lines, self.seconds
            ),
            Some("TRIAGE") => format!(
                "TRIAGE: diagnosis saved, patch withheld ({:.1}s)",
                self.seconds
            ),
            Some("RED") => format!("RED: not fixed ({:.1}s)", self.seconds),
            _ => {
                if self.total > 0 {
                    format!("{} attempt {}/{} ...", self.phase, self.attempt.max(1), self.total)
                } else if self.attempt > 0 {
                    format!("{} attempt {} ...", self.phase, self.attempt)
                } else {
                    format!("{} ...", self.phase)
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_progress_event() {
        let ev = parse_line(
            r#"{"type": "progress", "phase": "diagnose", "attempt": 2, "total": 3, "elapsed_s": 11}"#,
        );
        assert_eq!(
            ev,
            Some(Event::Progress {
                phase: "diagnose".to_string(),
                attempt: 2,
                total: 3,
                elapsed_s: 11,
                est_cost: None,
            })
        );
    }

    #[test]
    fn parses_est_cost_tolerantly() {
        // `est_cost` key (future backend emitters).
        let ev = parse_line(
            r#"{"type": "progress", "phase": "patch", "attempt": 1, "total": 2, "elapsed_s": 4, "est_cost": 1.25}"#,
        );
        assert_eq!(
            ev,
            Some(Event::Progress {
                phase: "patch".to_string(),
                attempt: 1,
                total: 2,
                elapsed_s: 4,
                est_cost: Some(1.25),
            })
        );
        // Legacy `cost` key is accepted as a fallback.
        let ev = parse_line(
            r#"{"type": "progress", "phase": "patch", "attempt": 1, "total": 2, "elapsed_s": 4, "cost": 0.5}"#,
        );
        assert!(matches!(
            ev,
            Some(Event::Progress {
                est_cost: Some(c),
                ..
            }) if (c - 0.5).abs() < 1e-9
        ));
        // `est_cost` wins when both keys are present.
        let ev = parse_line(
            r#"{"type": "progress", "phase": "patch", "attempt": 1, "total": 2, "elapsed_s": 4, "est_cost": 2.0, "cost": 0.5}"#,
        );
        assert!(matches!(
            ev,
            Some(Event::Progress {
                est_cost: Some(c),
                ..
            }) if (c - 2.0).abs() < 1e-9
        ));
        // Non-numeric cost poisons nothing: the tolerant parser drops the
        // line instead of stalling the render loop.
        assert_eq!(
            parse_line(
                r#"{"type": "progress", "phase": "patch", "est_cost": "lots"}"#
            ),
            None
        );
    }

    #[test]
    fn parses_progress_done_event() {
        assert_eq!(
            parse_line(r#"{"type": "progress", "done": true}"#),
            Some(Event::ProgressDone)
        );
    }

    #[test]
    fn parses_attempt_boundaries() {
        assert_eq!(
            parse_line(r#"{"type": "attempt", "attempt": 1, "phase": "start"}"#),
            Some(Event::Attempt {
                attempt: 1,
                starting: true
            })
        );
        assert_eq!(
            parse_line(
                r#"{"type": "attempt", "attempt": 1, "phase": "end", "outcome": "continued"}"#
            ),
            Some(Event::Attempt {
                attempt: 1,
                starting: false
            })
        );
    }

    #[test]
    fn parses_verdict_event() {
        assert_eq!(
            parse_line(
                r#"{"type": "verdict", "verdict": "GREEN", "run_id": "r1", "patch_lines": 7, "seconds": 12.5}"#
            ),
            Some(Event::Verdict {
                verdict: "GREEN".to_string(),
                patch_lines: 7,
                seconds: 12.5,
            })
        );
    }

    #[test]
    fn rejects_invalid_lines() {
        assert_eq!(parse_line(""), None);
        assert_eq!(parse_line("   "), None);
        assert_eq!(parse_line("not json at all"), None);
        assert_eq!(parse_line("[1,2,3]"), None);
        assert_eq!(parse_line(r#"{"nope": true}"#), None);
        assert_eq!(parse_line(r#"{"type": "bogus"}"#), None);
        // Unknown verdict values are not trusted.
        assert_eq!(
            parse_line(r#"{"type": "verdict", "verdict": "MAYBE"}"#),
            None
        );
        // Attempt without a number carries no information.
        assert_eq!(
            parse_line(r#"{"type": "attempt", "phase": "start"}"#),
            None
        );
        // Trailing whitespace is fine (subprocess lines end with \n).
        assert!(parse_line("{\"type\": \"progress\", \"done\": true}  \n").is_some());
    }

    #[test]
    fn line_buffer_splits_complete_lines_and_holds_partial() {
        let mut lb = LineBuffer::new();
        // One chunk, two full lines + a partial tail.
        let got = lb.feed("{\"a\":1}\n{\"b\":2}\n{\"c\":3");
        assert_eq!(got, vec!["{\"a\":1}".to_string(), "{\"b\":2}".to_string()]);
        assert_eq!(lb.pending(), "{\"c\":3");
        // Completing the tail flushes it; CRLF is stripped too.
        let got = lb.feed("}\r\n");
        assert_eq!(got, vec!["{\"c\":3}".to_string()]);
        assert_eq!(lb.pending(), "");
    }

    #[test]
    fn line_buffer_handles_byte_dribble() {
        let mut lb = LineBuffer::new();
        let mut got = Vec::new();
        for ch in ["{", "\"t", "ype", "\"", ":1", "}\n"] {
            got.extend(lb.feed(ch));
        }
        assert_eq!(got, vec!["{\"type\":1}".to_string()]);
    }

    #[test]
    fn mapper_tracks_progress_attempts_and_verdict() {
        let mut m = Mapper::new();
        assert_eq!(m.status_line(), "starting ...");
        m.handle_line(
            r#"{"type": "progress", "phase": "diagnose", "attempt": 1, "total": 3, "elapsed_s": 4}"#,
        );
        assert_eq!(m.attempt, 1);
        assert_eq!(m.total, 3);
        assert_eq!(m.status_line(), "diagnose attempt 1/3 ...");
        m.handle_line(r#"{"type": "attempt", "attempt": 2, "phase": "start"}"#);
        assert_eq!(m.attempt, 2);
        m.handle_line(
            r#"{"type": "verdict", "verdict": "GREEN", "run_id": "r", "patch_lines": 7, "seconds": 12.5}"#,
        );
        assert!(m.finished());
        assert_eq!(m.status_line(), "GREEN: fixed (7 patch lines, 12.5s)");
    }

    #[test]
    fn mapper_carries_last_seen_est_cost() {
        let mut m = Mapper::new();
        assert_eq!(m.est_cost, None);
        m.handle_line(
            r#"{"type": "progress", "phase": "diagnose", "attempt": 1, "total": 2, "elapsed_s": 1}"#,
        );
        assert_eq!(m.est_cost, None);
        m.handle_line(
            r#"{"type": "progress", "phase": "patch", "attempt": 1, "total": 2, "elapsed_s": 2, "est_cost": 1.25}"#,
        );
        assert_eq!(m.est_cost, Some(1.25));
        // A later event without cost never clears the last known value.
        m.handle_line(
            r#"{"type": "progress", "phase": "verify", "attempt": 1, "total": 2, "elapsed_s": 3}"#,
        );
        assert_eq!(m.est_cost, Some(1.25));
    }

    #[test]
    fn mapper_verdict_lines_cover_red_and_triage() {
        let mut m = Mapper::new();
        m.handle_line(
            r#"{"type": "verdict", "verdict": "RED", "run_id": "r", "patch_lines": 0, "seconds": 3.0}"#,
        );
        assert_eq!(m.status_line(), "RED: not fixed (3.0s)");
        let mut m = Mapper::new();
        m.handle_line(
            r#"{"type": "verdict", "verdict": "TRIAGE", "run_id": "r", "patch_lines": 0, "seconds": 3.0}"#,
        );
        assert_eq!(
            m.status_line(),
            "TRIAGE: diagnosis saved, patch withheld (3.0s)"
        );
    }

    #[test]
    fn mapper_ignores_garbage_without_subprocess() {
        let mut m = Mapper::new();
        for junk in ["", "hello", "[1]", "{\"type\":\"nope\"}"] {
            m.handle_line(junk);
        }
        assert!(!m.finished());
        assert_eq!(m.status_line(), "starting ...");
    }
}
