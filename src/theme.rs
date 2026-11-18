use crossterm::style::{Color, Colored, force_color_output};

// Highlighted rows need dark text even when the terminal's default text is light.
pub(crate) const HIGHLIGHT_FOREGROUND: Color = rgb(0, 0, 0);

pub(crate) const fn rgb(r: u8, g: u8, b: u8) -> Color {
    Color::Rgb { r, g, b }
}

pub(crate) struct InteractiveColors {
    previously_disabled: bool,
}

impl InteractiveColors {
    pub(crate) fn enter() -> Self {
        let previously_disabled = Colored::ansi_color_disabled_memoized();
        // Interactive highlights require their foreground/background pair.
        force_color_output(true);
        Self {
            previously_disabled,
        }
    }
}

impl Drop for InteractiveColors {
    fn drop(&mut self) {
        force_color_output(!self.previously_disabled);
    }
}
