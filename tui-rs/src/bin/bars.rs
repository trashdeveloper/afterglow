//! BAR SHOOTOUT demo: 10 variants, same tick, one screen.
//! `cargo run -p tui --bin bars` (Ctrl-C quits; non-TTY = one static frame).
use std::io::Write;
use std::time::Duration;

#[path = "../bars.rs"]
mod bars;

const NAMES: [&str; 10] = [
    "thin-rounded",
    "thick-block",
    "braille-sweep",
    "shimmer-text",
    "tab-pulse",
    "rich-classic",
    "spinner-track",
    "chevron",
    "eighths",
    "firefly",
];

fn render(w: usize, tick: u64) -> String {
    let fns = [
        bars::thin_rounded as fn(usize, u64) -> String,
        bars::thick_block,
        bars::braille_sweep,
        bars::shimmer_text,
        bars::tab_pulse,
        bars::rich_classic,
        bars::spinner_track,
        bars::chevron,
        bars::eighths,
        bars::firefly,
    ];
    let mut out = String::new();
    for (i, (name, f)) in NAMES.iter().zip(fns.iter()).enumerate() {
        out.push_str(&format!("{:>2} {:<14} {}\n", i + 1, name, f(w, tick)));
    }
    out
}

fn main() {
    let is_tty = std::io::IsTerminal::is_terminal(&std::io::stdout());
    let w = terminal_width().saturating_sub(20).max(10).min(80);
    if !is_tty {
        print!("{}", render(w, 8));
        return;
    }
    print!("\x1b[?25l");
    let _ = std::io::stdout().flush();
    let mut tick = 0u64;
    loop {
        print!("\x1b[H{}", render(w, tick));
        let _ = std::io::stdout().flush();
        std::thread::sleep(Duration::from_millis(66));
        tick += 1;
        if tick > 10_000_000 {
            break;
        }
    }
    print!("\x1b[?25h\n");
    let _ = std::io::stdout().flush();
}

fn terminal_width() -> usize {
    std::env::var("COLUMNS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(80)
}
