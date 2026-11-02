//! Tab-pulse single-row bar for nerd-font terminals (shootout winner,
//! user-picked): a dim thin track with a travelling gradient band
//! (blue `#5c9cf5` → purple `#9d7cd8` → peach `#fab283`), no caps —
//! indeterminate like opencode, plus a braille spinner while running.
//!
//! All geometry here is pure (no terminal access) so `cargo test` pins it.

/// Gradient stops: blue → purple → peach.
pub const BLUE: (u8, u8, u8) = (0x5c, 0x9c, 0xf5);
pub const PURPLE: (u8, u8, u8) = (0x9d, 0x7c, 0xd8);
pub const PEACH: (u8, u8, u8) = (0xfa, 0xb2, 0x83);

/// Track colour for the unfilled portion (dim slate).
pub const TRACK: (u8, u8, u8) = (0x3a, 0x3a, 0x46);

/// Bar height: exactly 1 terminal row. The bar is a single [`Cell`] row
/// of `width` cells — caps plus one thin line — never a multi-row block.
/// Pinned at compile time (`_BAR_IS_SINGLE_ROW`) and by `cargo test`.
pub const BAR_HEIGHT: usize = 1;
const _: () = assert!(BAR_HEIGHT == 1);

/// Track glyph: a light horizontal rule per cell. Track and band share
/// it; colour tells them apart (dim [`TRACK`] vs the [`gradient_at`]
/// sweep across the band).
pub const TRACK_GLYPH: char = '─';

// NOTE: the caps era (U+E0B6/U+E0B4 `LEFT_CAP`/`RIGHT_CAP`, the thin
// `FILL_GLYPH` fill, and the gaussian travelling tip) was retired when
// the user picked tab-pulse in the 10-variant shootout: no full-width
// fill and no caps leave this path — one thin rule glyph everywhere,
// colour carries the band.

/// Braille spinner frames (same 10-frame set nerd-font terminals love),
/// stepped every [`SPINNER_MS`] ms.
pub const SPINNER: [&str; 10] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

/// Spinner cadence in milliseconds.
pub const SPINNER_MS: u64 = 80;

/// Frame for an elapsed millisecond count (wraps every 10 frames).
pub fn spinner_at(elapsed_ms: u64) -> &'static str {
    SPINNER[(elapsed_ms / SPINNER_MS) as usize % SPINNER.len()]
}

fn clamp01(t: f64) -> f64 {
    if t.is_nan() {
        return 0.0;
    }
    t.clamp(0.0, 1.0)
}

fn lerp(a: u8, b: u8, t: f64) -> u8 {
    (a as f64 + (b as f64 - a as f64) * t).round().clamp(0.0, 255.0) as u8
}

fn mix(a: (u8, u8, u8), b: (u8, u8, u8), t: f64) -> (u8, u8, u8) {
    (lerp(a.0, b.0, t), lerp(a.1, b.1, t), lerp(a.2, b.2, t))
}

/// Gradient colour at position `t` in 0..=1: blue → purple on [0, 0.5],
/// purple → peach on [0.5, 1]. Out-of-range inputs clamp.
pub fn gradient_at(t: f64) -> (u8, u8, u8) {
    let t = clamp01(t);
    if t <= 0.5 {
        mix(BLUE, PURPLE, t * 2.0)
    } else {
        mix(PURPLE, PEACH, (t - 0.5) * 2.0)
    }
}

/// Fill fraction from backend progress (kept for the attempt counter
/// in `main.rs` even though the tab-pulse render itself is
/// indeterminate and ignores it).
pub fn fill_fraction(attempt: u32, total: u32, verdict_seen: bool) -> f64 {
    if verdict_seen {
        return 1.0;
    }
    if total == 0 {
        return 0.0;
    }
    clamp01(attempt as f64 / total as f64)
}

/// One rendered cell: glyph + truecolor foreground.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Cell {
    pub glyph: char,
    pub rgb: (u8, u8, u8),
}

/// Band width in cells: matches the shootout winner's proportions.
fn band_width(width: usize) -> usize {
    6.max(width / 6).min(width.max(1))
}

/// Render the bar into exactly `width` cells on exactly [`BAR_HEIGHT`]
/// row: a dim thin [`TRACK`] line with a travelling gradient band
/// ([`gradient_at`] blue edge → peach edge across the band, looping
/// every 3 s). Indeterminate — `fill` is accepted for signature
/// compatibility and ignored. Zero width yields zero cells.
pub fn render_bar(width: usize, _fill: f64, elapsed_s: f64) -> Vec<Cell> {
    if width == 0 {
        return Vec::new();
    }
    let band = band_width(width);
    let span = width + band;
    let head = if span == 0 {
        0
    } else {
        ((elapsed_s.max(0.0) / 3.0) * span as f64) as usize % span
    };
    (0..width)
        .map(|i| {
            let d = head.wrapping_sub(i);
            if d < band {
                let t = if band <= 1 {
                    1.0
                } else {
                    1.0 - d as f64 / band as f64
                };
                Cell {
                    glyph: TRACK_GLYPH,
                    rgb: gradient_at(t),
                }
            } else {
                Cell {
                    glyph: TRACK_GLYPH,
                    rgb: TRACK,
                }
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gradient_stops_match_spec_hex() {
        assert_eq!(gradient_at(0.0), BLUE);
        assert_eq!(gradient_at(0.0), (0x5c, 0x9c, 0xf5));
        assert_eq!(gradient_at(0.5), PURPLE);
        assert_eq!(gradient_at(0.5), (0x9d, 0x7c, 0xd8));
        assert_eq!(gradient_at(1.0), PEACH);
        assert_eq!(gradient_at(1.0), (0xfa, 0xb2, 0x83));
    }

    #[test]
    fn gradient_midpoints_blend_monotonically() {
        let q = gradient_at(0.25);
        // Blue → purple: red rises, blue falls.
        assert!(q.0 > BLUE.0 && q.0 < PURPLE.0, "q={q:?}");
        assert!(q.2 < BLUE.2 && q.2 > PURPLE.2, "q={q:?}");
        let q3 = gradient_at(0.75);
        // Purple → peach: red and green rise toward peach.
        assert!(q3.0 > PURPLE.0 && q3.0 < PEACH.0, "q3={q3:?}");
        assert!(q3.1 > PURPLE.1 && q3.1 < PEACH.1, "q3={q3:?}");
        // Clamps instead of panicking.
        assert_eq!(gradient_at(-2.0), BLUE);
        assert_eq!(gradient_at(99.0), PEACH);
    }

    #[test]
    fn fill_fraction_math() {
        assert_eq!(fill_fraction(0, 0, false), 0.0); // indeterminate
        assert_eq!(fill_fraction(0, 3, false), 0.0);
        assert!((fill_fraction(1, 3, false) - 1.0 / 3.0).abs() < 1e-9);
        assert_eq!(fill_fraction(3, 3, false), 1.0);
        assert_eq!(fill_fraction(9, 3, false), 1.0); // clamp, never > 1
        assert_eq!(fill_fraction(1, 3, true), 1.0); // verdict → full
        assert_eq!(fill_fraction(0, 0, true), 1.0);
    }

    #[test]
    fn bar_truncation_never_panics_and_keeps_width() {
        for w in [0, 1, 2, 5, 80] {
            for fill in [0.0, 0.33, 1.0] {
                let cells = render_bar(w, fill, 0.7);
                assert_eq!(cells.len(), w, "w={w} fill={fill}");
            }
        }
        // Tab-pulse: every cell is the thin track glyph; fill does not
        // change the render (indeterminate); never a block.
        let full = render_bar(10, 1.0, 0.0);
        assert!(full.iter().all(|c| c.glyph == TRACK_GLYPH));
        assert!(full.iter().all(|c| c.glyph != '█'));
        // Empty fill renders identically (fill ignored).
        let empty = render_bar(10, 0.0, 0.0);
        assert_eq!(empty, full, "fill ignored at same timestamp");
        // Partial fill: identical too.
        let cells = render_bar(10, 0.35, 0.0);
        assert_eq!(cells.len(), 10);
        assert_eq!(cells, full);
    }

    #[test]
    fn sweep_frames_vary_over_time() {
        // The travelling tip must repaint colours as time advances
        // (this is what the forced-TTY capture checks on the wire).
        let a = render_bar(40, 0.4, 0.0);
        let b = render_bar(40, 0.4, 0.4);
        let c = render_bar(40, 0.4, 1.2);
        assert_ne!(a, b, "frames 0.0s vs 0.4s must differ");
        assert_ne!(b, c, "frames 0.4s vs 1.2s must differ");
        // ... while geometry (glyphs) stays stable for a fixed fill.
        let glyphs = |v: Vec<Cell>| v.into_iter().map(|c| c.glyph).collect::<String>();
        assert_eq!(glyphs(a), glyphs(b));
    }

    #[test]
    fn spinner_cycles_every_frame() {
        assert_eq!(spinner_at(0), "⠋");
        assert_eq!(spinner_at(79), "⠋");
        assert_eq!(spinner_at(80), "⠙");
        let all: Vec<_> = (0..10).map(|i| spinner_at(i * 80)).collect();
        assert_eq!(all, SPINNER);
        assert_eq!(spinner_at(800), spinner_at(0)); // wraps
    }

    // --- Tab-pulse default (user-picked shootout #5) ---

    #[test]
    fn tab_pulse_is_single_row_without_caps() {
        // Exactly 1 terminal row tall, by construction; NO caps — the
        // track runs the full width.
        assert_eq!(BAR_HEIGHT, 1);
        for w in [2, 5, 40, 80] {
            for fill in [0.0, 0.4, 1.0] {
                let cells = render_bar(w, fill, 0.7);
                assert_eq!(cells.len(), w, "single row of exactly w={w} cells");
                assert!(
                    cells.iter().all(|c| c.glyph == TRACK_GLYPH),
                    "tab-pulse uses the thin track glyph everywhere w={w}"
                );
            }
        }
    }

    #[test]
    fn tab_pulse_band_travels_with_gradient_across_it() {
        // A contiguous bright band rides the dim track; across the band
        // the colour runs the spec gradient (blue edge → peach edge).
        for t in [0.7, 1.2, 1.8, 2.4] {
            let cells = render_bar(40, 0.4, t);
            let bright: Vec<usize> = cells
                .iter()
                .enumerate()
                .filter(|(_, c)| c.rgb != TRACK)
                .map(|(i, _)| i)
                .collect();
            let band = 6.max(40 / 6);
            assert_eq!(bright.len(), band, "band width t={t}");
            // Contiguous run.
            for (a, b) in bright.iter().zip(bright.iter().skip(1)) {
                assert_eq!(*b, a + 1, "contiguous t={t}");
            }
            // Gradient direction across the band.
            let first = cells[bright[0]].rgb;
            let last = cells[bright[bright.len() - 1]].rgb;
            assert!(first.0 < last.0, "blue edge -> peach edge t={t}");
        }
    }

    #[test]
    fn no_full_block_anywhere_in_thin_render() {
        // The thick-block era is over: no '█' may leave the new path.
        for w in [0, 1, 2, 5, 40, 80] {
            for fill in [0.0, 0.13, 0.35, 0.5, 0.87, 1.0] {
                for t in [0.0, 0.4, 1.2, 2.5] {
                    let cells = render_bar(w, fill, t);
                    assert!(
                        cells.iter().all(|c| c.glyph != '█'),
                        "w={w} fill={fill} t={t}: {cells:?}"
                    );
                }
            }
        }
    }

    #[test]
    fn tab_pulse_fill_is_ignored_track_stays_dim() {
        // Tab-pulse is indeterminate (opencode-style): fill does not
        // change the render; every non-band cell is the dim track.
        let a = render_bar(40, 0.0, 0.7);
        let b = render_bar(40, 1.0, 0.7);
        assert_eq!(a, b, "fill must not affect tab-pulse");
        let dim = a.iter().filter(|c| c.rgb == TRACK).count();
        assert_eq!(a.len() - dim, 6.max(40 / 6), "only the band is bright");
    }

    #[test]
    fn tab_pulse_track_uses_spec_gradient_in_band() {
        // Band cells ride the EXISTING blue → purple → peach gradient
        // (endpoints exact); everything else is the dim thin track.
        let cells = render_bar(40, 0.0, 1.5); // band fully on-bar mid-sweep
        let bright: Vec<&Cell> = cells.iter().filter(|c| c.rgb != TRACK).collect();
        assert_eq!(bright.len(), 6.max(40 / 6));
        let first = bright[0].rgb;
        let last = bright[bright.len() - 1].rgb;
        assert!(first.0 <= BLUE.0 + 30, "band starts blue-ish {first:?}");
        assert!(last.0 >= PEACH.0 - 8, "band ends peach-ish {last:?}");
        assert!(cells.iter().all(|c| c.glyph == TRACK_GLYPH));
    }

    #[test]
    fn tab_pulse_band_travels_without_block_cursor() {
        // Band position moves with time; glyphs stay stable (thin track
        // everywhere, no full-block cursor).
        let a = render_bar(40, 0.4, 0.0);
        let b = render_bar(40, 0.4, 0.4);
        assert_ne!(a, b, "band must repaint position over time");
        let glyphs = |v: Vec<Cell>| v.into_iter().map(|c| c.glyph).collect::<String>();
        assert_eq!(glyphs(a.clone()), glyphs(b));
        // Peak brightness travels: argmax rgb-sum moves with time.
        let peak = |v: Vec<Cell>| {
            v.iter()
                .enumerate()
                .max_by_key(|(_, c)| c.rgb.0 as u32 + c.rgb.1 as u32 + c.rgb.2 as u32)
                .map(|(i, _)| i)
                .unwrap()
        };
        let c = render_bar(40, 0.4, 1.2);
        assert_ne!(peak(a), peak(c), "bright band cell must move");
    }
}
