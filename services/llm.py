"""
Moonshot API integration: article rewrite + taxonomy categorization.
"""
import json
from typing import Any

import aiohttp

import config


async def _chat(messages: list[dict[str, str]], max_tokens: int = 4096) -> str:
    """Send chat request to Moonshot API."""
    payload = {
        "model": config.MOONSHOT_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": config.REWRITE_TEMPERATURE,
    }
    headers = {
        "Authorization": f"Bearer {config.MOONSHOT_API_KEY}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=120)
    ) as session:
        async with session.post(
            f"{config.MOONSHOT_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Moonshot API error: {resp.status} {text[:300]}")
            data = await resp.json()
            return data["choices"][0]["message"]["content"]


async def rewrite(text: str, original_title: str = "") -> dict[str, str]:
    """
    Rewrite a news article into a full analytical article.
    Returns: {"title": ..., "content": "... (HTML)", "excerpt": "..."}
    """
    # Truncate input if too long
    max_input = 8000
    truncated = text[:max_input] if len(text) > max_input else text
    title_hint = f'Original title: "{original_title}"\n\n' if original_title else ""

    system_prompt = (
        "You are a professional financial analyst and content editor for "
        "the Russian investment website инвестиционно.рф. "
        "Rewrite news into analytical articles: investment style, facts, figures, context, market impact. "
        "Use Russian language. Output strict JSON with keys: title, content, excerpt. "
        "Content should be HTML: <p>, <h2>, <h3>, <ul>, <li>, <strong>, <a>."
    )

    user_prompt = f"""{title_hint}Rewrite the following news into a full analytical article (800-1500 words).

Requirements:
- Style: professional investment analysis, specific facts, market impact
- Structure: intro → context → event details → market/industry impact → conclusions/forecasts
- Preserve all factual data: numbers, dates, names, tickers
- Use investment terminology in Russian
- Title: catchy, informative, max 100 characters
- Language: Russian

Original text:
{truncated}

Return ONLY JSON:
{{"title": "...", "content": "<p>...</p>", "excerpt": "..."}}"""

    raw = await _chat(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=config.REWRITE_MAX_TOKENS,
    )

    # Parse JSON from response
    result = _extract_json(raw)
    return {
        "title": result.get("title", original_title or "Без заголовка"),
        "content": result.get("content", text),
        "excerpt": result.get("excerpt", ""),
    }


async def categorize(
    article_text: str, taxonomies: dict[str, list[dict[str, Any]]]
) -> dict[str, list[int]]:
    """
    Given article text and available taxonomy terms, return selected term IDs.
    taxonomies: {taxonomy_name: [{"id": int, "name": str, "slug": str, "count": int}, ...]}
    Returns: {taxonomy_name: [term_id, ...]}
    """
    # Build compact taxonomy reference
    lines: list[str] = []
    tax_key_mapping: dict[str, str] = {
        "categories": "categories",
        "tags": "tags",
        "industriya": "industriya",
        "kompaniya": "kompaniya",
        "tiker": "tiker",
        "trend": "trend",
        "strategiya-investirovaniya": "strategiya_investirovaniya",
        "stadiya-sdelki": "stadiya_sdelki",
        "stadiya-proekta": "stadiya_proekta",
        "etapy-sdelki": "etapy_sdelki",
        "klassifikaciya-po-rynkam": "klassifikaciya_po_rynkam",
        "obuchenie": "obuchenie",
        "partnyor": "partnyor",
    }

    for tax_name, terms in taxonomies.items():
        if not terms:
            continue
        line = f"{tax_name}: " + ", ".join(
            f"{t.get('term_id', t.get('id', '?'))}={t['name']}" for t in terms[:50]
        )
        lines.append(line)

    taxonomy_str = "\n".join(lines)
    text_preview = article_text[:3000]  # Limit text length

    system_prompt = (
        "You are a content categorization assistant. "
        "Given an article and a list of taxonomy terms with IDs, "
        "select the most relevant terms for the article. "
        "Return ONLY a JSON object mapping taxonomy names to arrays of selected term IDs. "
        "Select only from the provided terms. Use empty array if none fit."
    )

    user_prompt = f"""Article (first 3000 chars):
{text_preview}

Available taxonomy terms (format: taxonomy_name: id=name, id=name, ...):
{taxonomy_str}

Return ONLY JSON with these exact keys (snake_case):
{{"categories": [], "tags": [], "industriya": [], "kompaniya": [], "tiker": [], "trend": [], "strategiya_investirovaniya": [], "stadiya_sdelki": [], "stadiya_proekta": [], "etapy_sdelki": [], "klassifikaciya_po_rynkam": [], "obuchenie": [], "partnyor": []}}"""

    raw = await _chat(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=2048,
    )

    result = _extract_json(raw)

    # Validate: only return IDs that exist in our taxonomy cache
    validated: dict[str, list[int]] = {}
    for tax_key in tax_key_mapping.values():
        terms = taxonomies.get(tax_key, [])
        valid_ids = {t.get("term_id", t.get("id")) for t in terms}
        selected = result.get(tax_key, [])
        if isinstance(selected, list):
            validated_ids = []
            for sid in selected:
                try:
                    # Handle LLM returning "67=AI" instead of 67
                    sid_str = str(sid)
                    if "=" in sid_str:
                        sid_str = sid_str.split("=")[0]
                    sid_int = int(sid_str)
                    if sid_int in valid_ids:
                        validated_ids.append(sid_int)
                except (ValueError, TypeError):
                    continue
            validated[tax_key] = validated_ids
        else:
            validated[tax_key] = []

    return validated


def _extract_json(text: str) -> dict[str, Any]:
    """Extract and parse JSON from LLM response (handles markdown fences)."""
    # Try to find JSON block
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        text = text[start:end].strip()
    elif "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start)
        text = text[start:end].strip()

    # Try to find JSON object boundaries
    text = text.strip()
    if not text.startswith("{"):
        idx = text.find("{")
        if idx >= 0:
            text = text[idx:]
    if not text.endswith("}"):
        idx = text.rfind("}")
        if idx >= 0:
            text = text[: idx + 1]

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        # Last resort: try fixing common issues
        text = text.replace("\n", " ").replace("\t", " ")
        # Remove trailing commas before } or ]
        import re
        text = re.sub(r",(\s*[}\]])", r"\1", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise RuntimeError(f"Failed to parse LLM JSON response: {exc}\nText: {text[:500]}")
