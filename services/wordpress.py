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

# Internal taxonomy list — will be auto-discovered from WP
_DISCOVERED_TAXONOMIES: list[tuple[str, str]] = []


def _url(path: str) -> str:
    return f"{config.WP_BASE_URL}/wp-json/{path}"


async def discover_taxonomies() -> list[tuple[str, str]]:
    """Dynamically fetch all public taxonomies from WP REST API."""
    global _DISCOVERED_TAXONOMIES
    async with aiohttp.ClientSession(headers=_HEADERS) as session:
        async with session.get(_url("wp/v2/taxonomies")) as resp:
            if resp.status != 200:
                # Fallback to empty if discovery fails
                return _DISCOVERED_TAXONOMIES
            data = await resp.json()
            taxonomies: list[tuple[str, str]] = []
            for slug, info in data.items():
                # Only include taxonomies that are public and have REST support
                if not info.get("_links", {}).get("collection"):
                    continue
                rest_base = info.get("rest_base", slug)
                taxonomies.append((slug, f"wp/v2/{rest_base}"))
            _DISCOVERED_TAXONOMIES = taxonomies
            return taxonomies


async def sync_taxonomies() -> dict[str, list[dict[str, Any]]]:
    """Fetch all taxonomy terms from WP and return them grouped by taxonomy."""
    result: dict[str, list[dict[str, Any]]] = {}
    endpoints = _DISCOVERED_TAXONOMIES or await discover_taxonomies()
    async with aiohttp.ClientSession(headers=_HEADERS) as session:
        for tax_key, endpoint in endpoints:
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
            data = await resp.json()
            if resp.status not in (200, 201):
                raise RuntimeError(f"Media upload failed: {resp.status} {json.dumps(data, ensure_ascii=False)[:500]}")
            if "id" not in data:
                raise RuntimeError(f"Media upload response missing 'id': {json.dumps(data, ensure_ascii=False)[:500]}")
            return int(data["id"])


async def get_media(media_id: int) -> dict[str, Any]:
    """Get media details from WP. Returns {"id": int, "url": str, "mime_type": str} or {}."""
    async with aiohttp.ClientSession(headers=_HEADERS) as session:
        async with session.get(_url(f"wp/v2/media/{media_id}")) as resp:
            if resp.status != 200:
                return {}
            data = await resp.json()
            return {
                "id": data.get("id", 0),
                "url": data.get("source_url", ""),
                "mime_type": data.get("mime_type", ""),
            }


# Category ID → Custom Post Type endpoint mapping
_CATEGORY_CPT_MAP: dict[int, str] = {
    98:   "novost",      # Новости
    128:  "sobytie",     # Событие
    249:  "analitika",   # Аналитика
    129:  "interesnoe",  # Интересное
    296:  "kompaniya-p", # Компания
    107:  "trends",      # Тренды
}
_DEFAULT_CPT = "novost"  # fallback


def _resolve_cpt_endpoint(categories: list[int]) -> str:
    """Determine CPT endpoint from first known category, fallback to novost."""
    for cat_id in categories:
        if cat_id in _CATEGORY_CPT_MAP:
            return _CATEGORY_CPT_MAP[cat_id]
    return _DEFAULT_CPT


async def create_post(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Create a WordPress post in the correct Custom Post Type.
    Determines CPT from selected category (default: novost).
    """
    categories = payload.get("categories", [])
    cpt_endpoint = _resolve_cpt_endpoint(categories)

    wp_payload: dict[str, Any] = {
        "title": payload["title"],
        "content": payload["content"],
        "excerpt": payload.get("excerpt", ""),
        "status": payload.get("status", "publish"),
    }

    featured_media = payload.get("featured_media", 0)
    if featured_media and featured_media > 0:
        wp_payload["featured_media"] = featured_media

    # Dynamically add non-empty taxonomy arrays
    endpoints = _DISCOVERED_TAXONOMIES or []
    for tax_slug, _ in endpoints:
        val = payload.get(tax_slug, [])
        if val:
            wp_payload[tax_slug] = val

    timeout = aiohttp.ClientTimeout(total=60, connect=10)
    async with aiohttp.ClientSession(headers=_HEADERS, timeout=timeout) as session:
        async with session.post(_url(f"wp/v2/{cpt_endpoint}"), json=wp_payload) as resp:
            data = await resp.json()
            if resp.status not in (200, 201):
                raise RuntimeError(
                    f"Post creation failed (CPT={cpt_endpoint}): {resp.status} "
                    f"{json.dumps(data, ensure_ascii=False)[:1000]}"
                )
            if "id" not in data:
                raise RuntimeError(
                    f"Post creation response missing 'id': "
                    f"{json.dumps(data, ensure_ascii=False)[:1000]}"
                )
            return {
                "id": data["id"],
                "url": data.get("link", ""),
                "slug": data.get("slug", ""),
            }
