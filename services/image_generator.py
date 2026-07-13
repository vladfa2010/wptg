"""
Image generation via Kimi / compatible API.
Generates a featured image based on article title and excerpt.
"""
import aiohttp

import config

# Kimi image generation endpoint (adjust if different)
_KIMI_IMAGE_URL = "https://api.moonshot.cn/v1/images/generations"


async def generate_image(title: str, excerpt: str = "") -> bytes | None:
    """
    Generate a featured image for the article.
    Returns image bytes (JPEG) or None if generation failed.
    """
    if not config.KIMI_API_KEY:
        return None

    prompt = (
        f"Professional investment-themed illustration for article: {title}. "
        f"Modern, clean, corporate style. Dark background with subtle tech elements. "
        f"Abstract, no text in image. High quality digital art."
    )

    payload = {
        "model": "kimi-image",
        "prompt": prompt,
        "size": "1024x576",  # 16:9 aspect ratio
        "quality": "standard",
        "n": 1,
    }
    headers = {
        "Authorization": f"Bearer {config.KIMI_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=60)
        ) as session:
            async with session.post(
                _KIMI_IMAGE_URL, headers=headers, json=payload
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                # Kimi returns URL or base64
                if "data" in data and len(data["data"]) > 0:
                    img_data = data["data"][0]
                    if "url" in img_data:
                        # Download from URL
                        async with session.get(img_data["url"]) as img_resp:
                            return await img_resp.read()
                    elif "b64_json" in img_data:
                        import base64
                        return base64.b64decode(img_data["b64_json"])
                return None
    except Exception:
        return None


async def generate_image_prompt_only(title: str, excerpt: str = "") -> str:
    """Return just the prompt text (for debugging or manual generation)."""
    return (
        f"Professional investment-themed illustration for article: {title}. "
        f"Modern, clean, corporate style. Dark background with subtle tech elements. "
        f"Abstract, no text in image. High quality digital art."
    )
