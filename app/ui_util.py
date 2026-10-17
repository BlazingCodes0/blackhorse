"""Tiny Qt helpers shared by the BlackHorse GUI pages.

The GUI has several timer-driven panes (10s dashboard / trade / log, 5s agent
orders and decisions). A bare setPlainText/setHtml/setRowCount rebuild resets a
scrollable widget back to the top, so reading a long pane meant fighting the
refresh. These helpers repaint while holding the reader's scroll position.
"""
from contextlib import contextmanager


def _vbar(w):
    return w.verticalScrollBar() if hasattr(w, "verticalScrollBar") else None


@contextmanager
def keep_scroll(*widgets, stick_bottom=False):
    """Repaint scrollable widget(s) without losing the reader's place.

    stick_bottom=True keeps a pane pinned to the end only if it was already at
    the end (a live log tail); otherwise it holds the position like the rest.
    """
    saved = []
    for w in widgets:
        b = _vbar(w)
        if b is not None:
            saved.append((b, b.value(), b.value() >= b.maximum() - 4))
    try:
        yield
    finally:
        for b, pos, was_bottom in saved:
            b.setValue(b.maximum() if (stick_bottom and was_bottom) else min(pos, b.maximum()))


def set_text_keep_scroll(view, text):
    """setPlainText only when the text actually changed; keep the position."""
    if view.toPlainText() == text:
        return
    with keep_scroll(view):
        view.setPlainText(text)


def set_html_keep_scroll(view, html):
    """setHtml without losing the reader's place; skip an identical repaint.

    Qt normalises html on the way in, so compare against the source we last
    handed it (stashed on the widget) rather than view.toHtml().
    """
    if getattr(view, "_bh_last_html", None) == html:
        return
    view._bh_last_html = html
    with keep_scroll(view):
        view.setHtml(html)
