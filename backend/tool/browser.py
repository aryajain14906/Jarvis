"""
tool/browser.py
=================
Phase 4.2D: basic browser interaction, using Playwright (sync API).

Requires (one-time setup):
    pip install playwright
    playwright install chromium

The browser/page are kept as module-level state so consecutive tool calls
(open_browser -> navigate -> click -> read_page) act on the same tab,
the way a person clicking through a browser would.
"""

import json

_playwright = None
_browser = None
_page = None


def _ensure_browser():
    global _playwright, _browser, _page
    if _page is not None:
        return
    from playwright.sync_api import sync_playwright
    _playwright = sync_playwright().start()
    _browser = _playwright.chromium.launch(headless=False)
    _page = _browser.new_page()


def open_browser(_: str = "") -> str:
    """Open a visible Chromium browser window. Input: ignored."""
    try:
        _ensure_browser()
        return "Browser opened."
    except Exception as e:
        return f"Error opening browser: {e}"


def navigate(url: str) -> str:
    """Navigate the browser to a URL. Input: a URL (with or without https://)."""
    try:
        _ensure_browser()
        url = url.strip()
        if not url.startswith("http"):
            url = "https://" + url
        _page.goto(url, timeout=20000)
        return f"Navigated to {url}."
    except Exception as e:
        return f"Error navigating: {e}"


def click(target: str) -> str:
    """Click an element by visible text, or by accessible label/role if no text matches. Input: the text or purpose (e.g. 'search') of the button/link to click."""
    try:
        _ensure_browser()
    except Exception as e:
        return f"Error opening browser: {e}"

    attempts = [
        lambda: _page.get_by_text(target, exact=False).first,
        lambda: _page.get_by_role("button", name=target, exact=False).first,
        lambda: _page.get_by_role("link", name=target, exact=False).first,
        lambda: _page.get_by_label(target, exact=False).first,
        lambda: _page.get_by_placeholder(target, exact=False).first,
        # Icon-only buttons (e.g. a magnifying-glass search icon) often have no
        # visible text or label at all -- fall back to a CSS attribute guess.
        lambda: _page.locator(f"[aria-label*='{target}' i], [title*='{target}' i]").first,
    ]

    last_error = None
    for get_locator in attempts:
        try:
            locator = get_locator()
            locator.click(timeout=4000)
            return f"Clicked '{target}'."
        except Exception as e:
            last_error = e
            continue

    return f"Error clicking '{target}': couldn't find a matching element ({last_error})."


def type_text(tool_input: str) -> str:
    """Type into an input field. Input: JSON with 'selector' (placeholder/label text) and 'text'."""
    try:
        data = json.loads(tool_input)
        selector, text = data["selector"], data["text"]
    except Exception as e:
        return f"Error: expected JSON with 'selector' and 'text'. {e}"

    try:
        _ensure_browser()
        _page.get_by_placeholder(selector).first.fill(text, timeout=8000)
        return f"Typed into '{selector}'."
    except Exception as e:
        return f"Error typing into '{selector}': {e}"


def read_page(_: str = "") -> str:
    """Read the visible text of the current page (capped). Input: ignored."""
    try:
        _ensure_browser()
        text = _page.inner_text("body")
        return text[:3000] if text else "(page has no visible text)"
    except Exception as e:
        return f"Error reading page: {e}"


def close_browser(_: str = "") -> str:
    """Close the browser. Input: ignored."""
    global _browser, _page, _playwright
    try:
        if _browser:
            _browser.close()
        if _playwright:
            _playwright.stop()
        _browser = _page = _playwright = None
        return "Browser closed."
    except Exception as e:
        return f"Error closing browser: {e}"