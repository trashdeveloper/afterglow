//! Full-width gradient sweep bar: fractional eighth-blocks + truecolor
//! gradient (blue `#5c9cf5` → purple `#9d7cd8` → peach `#fab283`) with a
//! travelling bright tip, dim track, and a braille spinner while running.
//!
//! All geometry here is pure (no terminal access) so `cargo test` pins it.

/// Gradient stops: blue → purple → peach.
pub const BLUE: (u8, u8, u8) = (0x5c, 0x9c, 0xf5);
pub const PURPLE: (u8, u8, u8) = (0x9d, 0x7c, 0xd8);
pub const PEACH: (u8, u8, u8) = (0xfa, 0xb2, 0x83);

/// Track colour for the unfilled portion (dim slate).
pub const TRACK: (u8, u8, u8) = (0x3a, 0x3a, 0x46);

/// Track glyph: a light horizontal rule per cell.
pub const TRACK_GLYPH: char = '─';

/// Left-eighth blocks: index = filled eighths of the cell (0..=8).
pub const EIGHTHS: [char; 9] = [' ', '▏', '▎', '▍', '▌', '▋', '▊', '▉', '█'];

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

/// Fill fraction from backend progress: verdict seen → full; no total
/// yet → indeterminate 0 (the travelling tip still animates); otherwise
/// `attempt / total`, clamped to [0, 1].
pub fn fill_fraction(attempt: u32, total: u32, verdict_seen: bool) -> f64 {
    if verdict_seen {
        return 1.0;
    }
    if total == 0 {
        return 0.0;
    }
    clamp01(attempt as f64 / total as f64)
}

/// Glyph for the fractional part of a cell (`frac` in 0..1 → 0..8
/// eighths, rounded; exactly 1.0 is the caller's full block).
pub fn fraction_glyph(frac: f64) -> char {
    let idx = (clamp01(frac) * 8.0).round() as usize;
    EIGHTHS[idx.min(8)]
}

/// Brighten `rgb` toward white by `amount` in 0..=1 (the sweep tip pop).
pub fn brighten(rgb: (u8, u8, u8), amount: f64) -> (u8, u8, u8) {
    let a = clamp01(amount);
    (
        lerp(rgb.0, 255, a),
        lerp(rgb.1, 255, a),
        lerp(rgb.2, 255, a),
    )
}

/// Travelling tip position in cell units. Loops every `period_s`
/// seconds across `width` cells (with overhang so it enters/exits
/// smoothly); `None` when there is nothing to animate across.
fn tip_position(elapsed_s: f64, width: usize) -> Option<f64> {
    if width == 0 {
        return None;
    }
    let period_s = 3.0;
    let span = width as f64 + 16.0;
    let x = (elapsed_s % period_s) / period_s * span - 8.0;
    Some(x)
}

/// Extra brightness for cell `x` given the tip at `tip_x`: a gaussian
/// falloff (σ ≈ 2.2 cells), 0 far from the tip.
fn tip_boost(x: f64, tip_x: f64) -> f64 {
    let d = (x - tip_x) / 2.2;
    (-d * d).exp() * 0.75
}

/// One rendered cell: glyph + truecolor foreground.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Cell {
    pub glyph: char,
    pub rgb: (u8, u8, u8),
}

/// Render the bar into exactly `width` cells: gradient-filled fractional
/// blocks up to `fill` (0..=1), dim track past it, and a travelling
/// bright tip so consecutive frames differ while running. Zero width
/// yields zero cells (never panics — narrow terminals truncate to this).
pub fn render_bar(width: usize, fill: f64, elapsed_s: f64) -> Vec<Cell> {
    let fill = clamp01(fill);
    let tip_x = tip_position(elapsed_s, width);
    let filled = fill * width as f64;
    (0..width)
        .map(|i| {
            let x = i as f64;
            // Base colour follows the gradient across the whole bar so
            // the sweep reads even on the dim side of the tip.
            let t = if width <= 1 {
                0.0
            } else {
                x / (width - 1) as f64
            };
            if x < filled.floor() {
                let boost = tip_x.map_or(0.0, |tx| tip_boost(x, tx));
                Cell {
                    glyph: '█',
                    rgb: brighten(gradient_at(t), boost),
                }
            } else if (x - filled).abs() < f64::EPSILON || (x < filled && filled - x < 1.0) {
                // Fractional edge cell (only when fill lands mid-cell).
                let frac = (filled - x).clamp(0.0, 1.0);
                if frac <= 0.0 {
                    Cell {
                        glyph: TRACK_GLYPH,
                        rgb: TRACK,
                    }
                } else {
                    let boost = tip_x.map_or(0.0, |tx| tip_boost(x, tx));
                    Cell {
                        glyph: fraction_glyph(frac),
                        rgb: brighten(gradient_at(t), boost.max(0.25)),
                    }
                }
            } else {
                let boost = tip_x.map_or(0.0, |tx| tip_boost(x, tx) * 0.5);
                Cell {
                    glyph: TRACK_GLYPH,
                    rgb: brighten(TRACK, boost),
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
    fn eighth_blocks_cover_all_fractions() {
        assert_eq!(fraction_glyph(0.0), ' ');
        assert_eq!(fraction_glyph(1.0), '█');
        assert_eq!(fraction_glyph(0.5), '▌');
        // Monotone across the eighths table.
        let mut last = 0;
        for i in 0..=8 {
            let g = fraction_glyph(i as f64 / 8.0);
            let pos = EIGHTHS.iter().position(|&c| c == g).unwrap();
            assert!(pos >= last, "i={i} g={g}");
            last = pos;
        }
    }

    #[test]
    fn bar_truncation_never_panics_and_keeps_width() {
        for w in [0, 1, 2, 5, 80] {
            for fill in [0.0, 0.33, 1.0] {
                let cells = render_bar(w, fill, 0.7);
                assert_eq!(cells.len(), w, "w={w} fill={fill}");
            }
        }
        // Full fill is all solid blocks; empty fill is all track.
        assert!(render_bar(10, 1.0, 0.0).iter().all(|c| c.glyph == '█'));
        assert!(
            render_bar(10, 0.0, 0.0)
                .iter()
                .all(|c| c.glyph == TRACK_GLYPH)
        );
        // Fractional fill lands a partial block on the edge cell.
        let cells = render_bar(10, 0.35, 0.0);
        assert_eq!(cells.len(), 10);
        assert_eq!(cells[0].glyph, '█');
        assert_eq!(cells[1].glyph, '█');
        assert_eq!(cells[2].glyph, '█');
        assert!(cells[3].glyph != '█' && cells[3].glyph != TRACK_GLYPH);
        assert_eq!(cells[9].glyph, TRACK_GLYPH);
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
}
