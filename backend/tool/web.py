"""
tool/web.py
============
Lightweight web access -- separate from tool/browser.py.
Use this for "look something up" tasks that don't need a real browser
(no login, no clicking, no JS-heavy pages). browser.py is for when the
agent needs to actually interact with a page.

Requires: pip install requests beautifulsoup4
"""

import requests
from bs4 import BeautifulSoup


def web_search(query: str) -> str:
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
            return "\n".join(results)
        return f"No web results found for '{query}'."
    except Exception as e:
        return f"Error searching: {e}"


def fetch_url(url: str) -> str:
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
        return text[:3000] if text else "(no readable text found on page)"
    except Exception as e:
        return f"Error fetching {url}: {e}"