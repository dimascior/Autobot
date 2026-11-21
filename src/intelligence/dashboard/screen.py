"""Double-buffered terminal screen with diff-only rendering.

Adapted from RAMVISION terminal_ui.screen_buffer — structured array
of (char, fg_color, bg_color, flags) cells, front/back swap, and
damage-rectangle ANSI output.

No numpy dependency: uses a flat list of tuples. Each cell is
(ch, fg, bg, bold). Diff compares back vs front and emits only
changed cells using relative cursor movement.
"""

import sys

RESET = "\033[0m"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"
HOME = "\033[H"
CLEAR_LINE = "\033[K"
CLEAR_BELOW = "\033[J"


def _fg256(n):
    return "\033[38;5;%dm" % n

def _bg256(n):
    return "\033[48;5;%dm" % n

def _fg24(r, g, b):
    return "\033[38;2;%d;%d;%dm" % (r, g, b)

def _bg24(r, g, b):
    return "\033[48;2;%d;%d;%dm" % (r, g, b)


class Screen:
    """Double-buffered character screen.

    write() paints into the back buffer. diff() compares back vs
    front and returns a list of (row, col, ansi_str) changes.
    swap() promotes the back buffer to front. The caller flushes
    the diff to stdout between diff() and swap().
    """

    EMPTY = (" ", 0, 0, 0)  # (char, fg_256, bg_256, flags)

    def __init__(self, width, height):
        self.w = width
        self.h = height
        size = width * height
        self.front = [self.EMPTY] * size
        self.back = [self.EMPTY] * size

    def clear(self):
        """Clear the back buffer."""
        self.back = [self.EMPTY] * (self.w * self.h)

    def put(self, x, y, ch, fg=7, bg=0, bold=False):
        """Write one cell to the back buffer (bounds-checked)."""
        if 0 <= x < self.w and 0 <= y < self.h:
            self.back[y * self.w + x] = (ch, fg, bg, 1 if bold else 0)

    def write(self, x, y, text, fg=7, bg=0, bold=False):
        """Write a string starting at (x, y)."""
        for i, ch in enumerate(text):
            self.put(x + i, y, ch, fg, bg, bold)

    def write_ansi(self, x, y, segments):
        """Write pre-colored segments: [(text, fg, bg, bold), ...]."""
        cx = x
        for text, fg, bg, bold in segments:
            for ch in text:
                self.put(cx, y, ch, fg, bg, bold)
                cx += 1

    def diff(self):
        """Compare back vs front. Returns list of (row, col, cell)."""
        changes = []
        for i in range(self.w * self.h):
            if self.back[i] != self.front[i]:
                row = i // self.w
                col = i % self.w
                changes.append((row, col, self.back[i]))
        return changes

    def swap(self):
        """Promote back to front."""
        self.front = list(self.back)

    def render_diff(self, changes):
        """Convert diff list to ANSI escape string."""
        if not changes:
            return ""
        parts = []
        last_row = -1
        last_col = -1
        last_fg = -1
        last_bg = -1
        last_bold = -1
        for row, col, (ch, fg, bg, bold) in changes:
            # Cursor positioning
            if row != last_row or col != last_col + 1:
                parts.append("\033[%d;%dH" % (row + 1, col + 1))
            # Style
            if fg != last_fg or bg != last_bg or bold != last_bold:
                style = ""
                if bold:
                    style += "\033[1m"
                if fg > 0:
                    style += _fg256(fg)
                if bg > 0:
                    style += _bg256(bg)
                if not bold and fg == 0 and bg == 0:
                    style += RESET
                parts.append(style)
                last_fg = fg
                last_bg = bg
                last_bold = bold
            parts.append(ch)
            last_row = row
            last_col = col
        parts.append(RESET)
        return "".join(parts)

    def flush(self, ansi_str):
        """Write ANSI string to stdout."""
        if ansi_str:
            sys.stdout.write(ansi_str)
            sys.stdout.flush()

    def full_render(self):
        """Render entire back buffer (for first frame)."""
        parts = [HOME]
        for y in range(self.h):
            for x in range(self.w):
                ch, fg, bg, bold = self.back[y * self.w + x]
                if bold:
                    parts.append("\033[1m")
                if fg > 0:
                    parts.append(_fg256(fg))
                if bg > 0:
                    parts.append(_bg256(bg))
                parts.append(ch)
                if fg > 0 or bg > 0 or bold:
                    parts.append(RESET)
            if y < self.h - 1:
                parts.append("\n")
        return "".join(parts)
