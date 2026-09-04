"""
tool/browser.py
=================
Basic browser interaction, using Playwright (sync API).

Phase 4.2E update: every function now returns (ok: bool, message: str)
instead of a bare string.

Requires (one-time setup):
    pip install playwright
    playwright install chromium
"""

import json

_playwright = None
_browser = None
_page = None


def _ensure_browser():
    global _playwright, _browser, _page

    if _page is not None:
        # A reference existing doesn't mean the connection is still alive --
        # the browser could have crashed or been closed since we last used
        # it. Probe it cheaply; if it's dead, discard the stale state and
        # fall through to create a fresh one instead of failing forever.
        try:
            _page.title()  # any lightweight call that requires a live connection
            return
        except Exception:
            print("[BROWSER] Stale browser connection detected -- reopening.")
            _playwright = _browser = _page = None

    from playwright.sync_api import sync_playwright
    _playwright = sync_playwright().start()
    _browser = _playwright.chromium.launch(headless=False)
    _page = _browser.new_page()


def open_browser(_: str = "") -> tuple[bool, str]:
    """Open a visible Chromium browser window. Input: ignored."""
    try:
        _ensure_browser()
        return True, "Browser opened."
    except Exception as e:
        return False, f"Error opening browser: {e}"


def navigate(url: str) -> tuple[bool, str]:
    """Navigate the browser to a URL. Input: a URL (with or without https://)."""
    try:
        _ensure_browser()
        url = url.strip()
        if not url.startswith("http"):
            url = "https://" + url
        _page.goto(url, timeout=20000)
        return True, f"Navigated to {url}."
    except Exception as e:
        return False, f"Error navigating: {e}"


def click(target: str) -> tuple[bool, str]:
    """Click an element by visible text, or by accessible label/role if no text matches. Input: the text or purpose (e.g. 'search') of the button/link to click."""
    try:
        _ensure_browser()
    except Exception as e:
        return False, f"Error opening browser: {e}"

    attempts = [
        lambda: _page.get_by_text(target, exact=False).first,
        lambda: _page.get_by_role("button", name=target, exact=False).first,
        lambda: _page.get_by_role("link", name=target, exact=False).first,
        lambda: _page.get_by_label(target, exact=False).first,
        lambda: _page.get_by_placeholder(target, exact=False).first,
        lambda: _page.locator(f"[aria-label*='{target}' i], [title*='{target}' i]").first,
    ]

    last_error = None
    for get_locator in attempts:
        try:
            locator = get_locator()
            locator.click(timeout=4000)
            return True, f"Clicked '{target}'."
        except Exception as e:
            last_error = e
            continue

    return False, f"Couldn't find a clickable element matching '{target}' ({last_error})."


def type_text(tool_input: str) -> tuple[bool, str]:
    """Type into an input field. Input: JSON with 'selector' (placeholder/label text) and 'text'."""
    try:
        data = json.loads(tool_input)
        selector, text = data["selector"], data["text"]
    except Exception as e:
        return False, f"Expected JSON with 'selector' and 'text'. {e}"

    try:
        _ensure_browser()
        _page.get_by_placeholder(selector).first.fill(text, timeout=8000)
        return True, f"Typed into '{selector}'."
    except Exception as e:
        return False, f"Error typing into '{selector}': {e}"


def read_page(_: str = "") -> tuple[bool, str]:
    """Read the visible text of the current page (capped). Input: ignored."""
    try:
        _ensure_browser()
        text = _page.inner_text("body")
        if not text:
            return False, "The page has no visible text."
        return True, text[:3000]
    except Exception as e:
        return False, f"Error reading page: {e}"


def close_browser(_: str = "") -> tuple[bool, str]:
    """Close the browser. Input: ignored."""
    global _browser, _page, _playwright
    try:
        if _browser:
            _browser.close()
        if _playwright:
            _playwright.stop()
        _browser = _page = _playwright = None
        return True, "Browser closed."
    except Exception as e:
        return False, f"Error closing browser: {e}"