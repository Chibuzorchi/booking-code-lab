"""Client-side assets shared by the generated pages.

The clipboard helper lives once, in copy.js, and is *inlined* into every page
rather than linked: the live report and the distinct page are standalone files
opened straight from disk, where a <script src="..."> would not resolve. This
module is the only place that reads those assets, so there is one copy of the
helper and one way in.
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
    return inline_copy_js(_asset("app.html"))
