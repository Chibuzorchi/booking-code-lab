"""Client-side assets shared by the generated pages.

The clipboard helper lives once, in copy.js, and is *inlined* into every page
rather than linked: the live report and the distinct page are standalone files
opened straight from disk, where a <script src="..."> would not resolve. This
module is the only place that reads those assets, so there is one copy of the
helper and one way in.

The console's CSS and JavaScript also live in separate source files. Inlining
them keeps /app self-contained without adding public asset routes or a bundler.
"""
from __future__ import annotations

from pathlib import Path

PLACEHOLDER = "__COPY_JS__"


def _asset(name: str) -> str:
    return Path(__file__).with_name(name).read_text(encoding="utf-8")


def copy_js() -> str:
    """The shared clipboard helper, ready to inline inside a <script> element."""
    source = _asset("copy.js")
    if "</script" in source.lower():
        raise ValueError("copy.js cannot be inlined: it closes the script element")
    return source


def inline_copy_js(page: str) -> str:
    """Substitute a page's __COPY_JS__ placeholder; refuse a page without one."""
    if PLACEHOLDER not in page:
        raise ValueError("page template has no __COPY_JS__ placeholder")
    return page.replace(PLACEHOLDER, copy_js())


def app_page() -> str:
    """The 3-stage page: Stage 1 harvest, Stage 2 distinct, Stage 3 de-correlate.

    A static shell; every figure on it comes from the local F6 API at runtime,
    so the page holds no run data and is safe to serve before any scan exists.
    """
    page = _asset("app.html")
    for marker, name, element in (("__APP_CSS__", "app.css", "style"),
                                  ("__APP_JS__", "app.js", "script")):
        if page.count(marker) != 1:
            raise ValueError(f"app template must contain exactly one {marker}")
        source = _asset(name)
        if f"</{element}" in source.lower():
            raise ValueError(f"{name} cannot be inlined: it closes the {element} element")
        page = page.replace(marker, source)
    return inline_copy_js(page)
