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
    """Search the web and return a short summary. Input: a search query."""
    try:
        resp = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        abstract = data.get("AbstractText")
        if abstract:
            return abstract

        topics = data.get("RelatedTopics", [])
        snippets = [t["Text"] for t in topics if isinstance(t, dict) and t.get("Text")]
        if snippets:
            return "\n".join(snippets[:3])

        return f"No quick summary found for '{query}'. Try fetch_url with a specific site instead."
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