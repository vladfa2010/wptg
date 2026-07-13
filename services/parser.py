"""
URL parsing: extract title, main text, and OG image.
Uses trafilatura for content extraction.
"""
from typing import Any

import aiohttp
import trafilatura


async def fetch_url(url: str) -> str:
    """Fetch raw HTML from URL."""
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=15),
        headers={"User-Agent": "Mozilla/5.0 (compatible; TgBot/1.0)"},
    ) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.text()


def _extract_og(html: str, prop: str) -> str:
    """Quick-and-dirty OG meta extractor."""
    import re
    pattern = f'<meta[^>]+property="{re.escape(prop)}"[^>]+content="([^"]+)"'
    m = re.search(pattern, html, re.IGNORECASE)
    if m:
        return m.group(1)
    # Try name instead of property
    pattern = f'<meta[^>]+name="{re.escape(prop)}"[^>]+content="([^"]+)"'
    m = re.search(pattern, html, re.IGNORECASE)
    return m.group(1) if m else ""


def _extract_title(html: str) -> str:
    """Extract <title> or og:title."""
    title = _extract_og(html, "og:title")
    if title:
        return title
    import re
    m = re.search(r"<title>([^<]+)</title>", html, re.IGNORECASE)
    return m.group(1).strip() if m else ""


async def parse_url(url: str) -> dict[str, Any]:
    """
    Parse a URL and return structured data:
    {
        "title": str,
        "text": str,      # Clean extracted text
        "og_image": str,  # OG image URL or ""
        "source_url": str,
    }
    """
    html = await fetch_url(url)

    title = _extract_title(html)
    og_image = _extract_og(html, "og:image")

    # Trafilatura for main content
    extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
    text = extracted or ""

    return {
        "title": title,
        "text": text,
        "og_image": og_image,
        "source_url": url,
    }


async def download_image(url: str) -> bytes:
    """Download image bytes from URL."""
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=15),
        headers={"User-Agent": "Mozilla/5.0"},
    ) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.read()
