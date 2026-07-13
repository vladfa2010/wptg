"""
Async SQLite layer: drafts, publications, taxonomy cache.
"""
import json
from datetime import datetime
from typing import Any

import aiosqlite

import config


# ─── Schema ───────────────────────────────────────────────
_INIT_SQL = """
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_user_id INTEGER NOT NULL,
    source_url TEXT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    excerpt TEXT,
    featured_media_id INTEGER,
    taxonomies_json TEXT DEFAULT '{}',
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER REFERENCES drafts(id),
    tg_message_id INTEGER,
    wp_post_id INTEGER,
    wp_post_url TEXT,
    source_url TEXT,
    title TEXT,
    taxonomies_json TEXT,
    status TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS taxonomy_cache (
    taxonomy TEXT NOT NULL,
    term_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    count INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (taxonomy, term_id)
);
"""


async def init_db() -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.executescript(_INIT_SQL)
        await db.commit()


# ─── Drafts ───────────────────────────────────────────────
async def create_draft(
    tg_user_id: int,
    title: str,
    content: str,
    excerpt: str = "",
    source_url: str = "",
    taxonomies: dict[str, list[int]] | None = None,
    featured_media_id: int = 0,
) -> int:
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO drafts (tg_user_id, source_url, title, content, excerpt,
                                featured_media_id, taxonomies_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tg_user_id,
                source_url,
                title,
                content,
                excerpt,
                featured_media_id,
                json.dumps(taxonomies or {}, ensure_ascii=False),
            ),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]


async def update_draft(
    draft_id: int,
    title: str | None = None,
    content: str | None = None,
    excerpt: str | None = None,
    taxonomies: dict[str, list[int]] | None = None,
    featured_media_id: int | None = None,
) -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        fields: list[str] = []
        values: list[Any] = []
        if title is not None:
            fields.append("title = ?")
            values.append(title)
        if content is not None:
            fields.append("content = ?")
            values.append(content)
        if excerpt is not None:
            fields.append("excerpt = ?")
            values.append(excerpt)
        if taxonomies is not None:
            fields.append("taxonomies_json = ?")
            values.append(json.dumps(taxonomies, ensure_ascii=False))
        if featured_media_id is not None:
            fields.append("featured_media_id = ?")
            values.append(featured_media_id)

        if not fields:
            return

        fields.append("updated_at = CURRENT_TIMESTAMP")
        values.append(draft_id)

        await db.execute(
            f"UPDATE drafts SET {', '.join(fields)} WHERE id = ?",  # noqa: S608
            values,
        )
        await db.commit()


async def get_draft(draft_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            data = dict(row)
            data["taxonomies"] = json.loads(data.get("taxonomies_json") or "{}")
            return data


async def delete_draft(draft_id: int) -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))
        await db.commit()


# ─── Publications ─────────────────────────────────────────
async def log_publication(
    draft_id: int,
    wp_post_id: int,
    wp_post_url: str,
    title: str,
    taxonomies: dict[str, list[int]],
    source_url: str = "",
    status: str = "published",
) -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO publications
                (draft_id, wp_post_id, wp_post_url, source_url, title,
                 taxonomies_json, status, published_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                draft_id,
                wp_post_id,
                wp_post_url,
                source_url,
                title,
                json.dumps(taxonomies, ensure_ascii=False),
                status,
                datetime.utcnow().isoformat(),
            ),
        )
        await db.commit()


async def get_publication_by_source(source_url: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM publications WHERE source_url = ? LIMIT 1", (source_url,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


# ─── Taxonomy Cache ───────────────────────────────────────
async def upsert_taxonomy_terms(taxonomy: str, terms: list[dict[str, Any]]) -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        for term in terms:
            await db.execute(
                """
                INSERT INTO taxonomy_cache (taxonomy, term_id, name, slug, count, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(taxonomy, term_id) DO UPDATE SET
                    name = excluded.name,
                    slug = excluded.slug,
                    count = excluded.count,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (taxonomy, term["id"], term["name"], term["slug"], term.get("count", 0)),
            )
        await db.commit()


async def get_taxonomy_terms(taxonomy: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT term_id, name, slug, count FROM taxonomy_cache WHERE taxonomy = ? ORDER BY name",
            (taxonomy,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_all_active_taxonomies() -> dict[str, list[dict[str, Any]]]:
    """Return all taxonomies with count > 0 terms."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT DISTINCT taxonomy FROM taxonomy_cache WHERE count > 0 ORDER BY taxonomy"
        ) as cur:
            taxonomies = [r["taxonomy"] for r in await cur.fetchall()]

        result: dict[str, list[dict[str, Any]]] = {}
        for tax in taxonomies:
            async with db.execute(
                "SELECT term_id, name, slug, count FROM taxonomy_cache "
                "WHERE taxonomy = ? AND count > 0 ORDER BY count DESC, name",
                (tax,),
            ) as cur:
                result[tax] = [dict(r) for r in await cur.fetchall()]
        return result


async def clear_taxonomy_cache() -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("DELETE FROM taxonomy_cache")
        await db.commit()
