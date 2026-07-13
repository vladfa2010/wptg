"""
Image generation via Kimi / compatible API.
Generates a featured image based on article title and excerpt.
"""
import logging

import aiohttp

import config

logger = logging.getLogger(__name__)

# Kimi image generation endpoint (adjust if different)
_KIMI_IMAGE_URL = "https://api.moonshot.ai/v1/images/generations"


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
            logger.info("Requesting image generation from %s", _KIMI_IMAGE_URL)
            async with session.post(
                _KIMI_IMAGE_URL, headers=headers, json=payload
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    logger.error("Image generation failed: status=%s response=%s", resp.status, data)
                    return None
                logger.info("Image generation response: %s", data)
                if "data" in data and len(data["data"]) > 0:
                    img_data = data["data"][0]
                    if "url" in img_data:
                        logger.info("Downloading image from URL: %s", img_data["url"])
                        async with session.get(img_data["url"]) as img_resp:
                            return await img_resp.read()
                    elif "b64_json" in img_data:
                        import base64
                        return base64.b64decode(img_data["b64_json"])
                    elif "revised_prompt" in img_data:
                        logger.warning("Image API returned revised_prompt but no image data")
                logger.error("No image data in response: %s", data)
                return None
    except Exception as exc:
        logger.exception("Image generation exception: %s", exc)
        return None


async def generate_image_prompt_only(title: str, excerpt: str = "") -> str:
    """Return just the prompt text (for debugging or manual generation)."""
    return (
        f"Professional investment-themed illustration for article: {title}. "
        f"Modern, clean, corporate style. Dark background with subtle tech elements. "
        f"Abstract, no text in image. High quality digital art."
    )
