"""
tool/web.py
============
Lightweight web access -- separate from tool/browser.py.

Phase 4.2E update: every function now returns (ok: bool, message: str)
instead of a bare string.

Requires: pip install requests beautifulsoup4
"""

import requests
from bs4 import BeautifulSoup


def web_search(query: str) -> tuple[bool, str]:
    """Search the web and return the top results. Input: a search query."""
    try:
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        results = []
        for result in soup.select(".result__body")[:3]:
            title_el = result.select_one(".result__title")
            snippet_el = result.select_one(".result__snippet")
            title = title_el.get_text(strip=True) if title_el else ""
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            if title or snippet:
                results.append(f"{title}: {snippet}" if snippet else title)

        if results:
            return True, "\n".join(results)
        return False, f"No web results found for '{query}'."
    except Exception as e:
        return False, f"Error searching: {e}"


def fetch_url(url: str) -> tuple[bool, str]:
    """Fetch a webpage and return its visible text (capped). Input: a URL."""
    try:
        url = url.strip()
        if not url.startswith("http"):
            url = "https://" + url
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())
        if not text:
            return False, "No readable text found on that page."
        return True, text[:3000]
    except Exception as e:
        return False, f"Error fetching {url}: {e}"