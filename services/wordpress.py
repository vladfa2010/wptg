"""
WordPress REST API async client.
Handles: taxonomy sync, media upload, post creation.
"""
import base64
import json
from typing import Any

import aiohttp

import config

_AUTH_HEADER = "Basic " + base64.b64encode(
    f"{config.WP_LOGIN}:{config.WP_PASSWORD}".encode()
).decode()

_HEADERS = {
    "Authorization": _AUTH_HEADER,
    "Content-Type": "application/json",
}

_MEDIA_HEADERS = {
    "Authorization": _AUTH_HEADER,
    # Content-Type set per-request for multipart
}

_TAXONOMY_ENDPOINTS: list[tuple[str, str]] = [
    ("categories", "wp/v2/categories"),
    ("tags", "wp/v2/tags"),
    ("industriya", "wp/v2/industriya"),
    ("kompaniya", "wp/v2/kompaniya"),
    ("tiker", "wp/v2/tiker"),
    ("trend", "wp/v2/trend"),
    ("strategiya-investirovaniya", "wp/v2/strategiya-investirovaniya"),
    ("stadiya-sdelki", "wp/v2/stadiya-sdelki"),
    ("stadiya-proekta", "wp/v2/stadiya-proekta"),
    ("etapy-sdelki", "wp/v2/etapy-sdelki"),
    ("klassifikaciya-po-rynkam", "wp/v2/klassifikaciya-po-rynkam"),
    ("obuchenie", "wp/v2/obuchenie"),
    ("partnyor", "wp/v2/partnyor"),
]


def _url(path: str) -> str:
    return f"{config.WP_BASE_URL}/wp-json/{path}"


async def sync_taxonomies() -> dict[str, list[dict[str, Any]]]:
    """Fetch all taxonomy terms from WP and return them grouped by taxonomy."""
    result: dict[str, list[dict[str, Any]]] = {}
    async with aiohttp.ClientSession(headers=_HEADERS) as session:
        for tax_key, endpoint in _TAXONOMY_ENDPOINTS:
            url = _url(f"{endpoint}?per_page=100")
            async with session.get(url) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json()
                terms = [
                    {"id": t["id"], "name": t["name"], "slug": t["slug"], "count": t.get("count", 0)}
                    for t in data
                ]
                result[tax_key] = terms
    return result


async def upload_media(image_data: bytes, filename: str = "featured.jpg", mime_type: str = "image/jpeg") -> int:
    """Upload image to WP Media Library. Returns media ID."""
    headers = {
        "Authorization": _AUTH_HEADER,
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": mime_type,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            _url("wp/v2/media"), headers=headers, data=image_data
        ) as resp:
            if resp.status not in (200, 201):
                text = await resp.text()
                raise RuntimeError(f"Media upload failed: {resp.status} {text[:200]}")
            data = await resp.json()
            return int(data["id"])


async def create_post(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Create a WordPress post.
    Payload must include: title, content, excerpt, status,
    plus all taxonomy fields (categories, tags, industriya, ...).
    """
    # Ensure proper field names for WP REST API
    wp_payload = {
        "title": payload["title"],
        "content": payload["content"],
        "excerpt": payload.get("excerpt", ""),
        "status": payload.get("status", "publish"),
        "featured_media": payload.get("featured_media", 0),
        "categories": payload.get("categories", []),
        "tags": payload.get("tags", []),
        "industriya": payload.get("industriya", []),
        "kompaniya": payload.get("kompaniya", []),
        "tiker": payload.get("tiker", []),
        "trend": payload.get("trend", []),
        "strategiya-investirovaniya": payload.get("strategiya_investirovaniya", []),
        "stadiya-sdelki": payload.get("stadiya_sdelki", []),
        "stadiya-proekta": payload.get("stadiya_proekta", []),
        "etapy-sdelki": payload.get("etapy_sdelki", []),
        "klassifikaciya-po-rynkam": payload.get("klassifikaciya_po_rynkam", []),
        "obuchenie": payload.get("obuchenie", []),
        "partnyor": payload.get("partnyor", []),
    }

    async with aiohttp.ClientSession(headers=_HEADERS) as session:
        async with session.post(_url("wp/v2/posts"), json=wp_payload) as resp:
            if resp.status not in (200, 201):
                text = await resp.text()
                raise RuntimeError(f"Post creation failed: {resp.status} {text[:500]}")
            data = await resp.json()
            return {
                "id": data["id"],
                "url": data["link"],
                "slug": data["slug"],
            }
