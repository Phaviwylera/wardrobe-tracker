"""Optional: Claude describes the garment so we can also run text searches.

Google Lens is excellent at the identical photo, weaker when the shopper's picture is a
street/mirror shot. A precise text description ("mustard yellow cotton anarkali kurta,
mirror work yoke, flared, 3/4 sleeves") run against Google Shopping on Indian stores
recovers listings Lens misses. Disabled unless LLM_PROVIDER is set.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re

from ..config import Settings, get_settings

log = logging.getLogger(__name__)

PROMPT = """You are a fashion merchandiser for Indian e-commerce (Myntra, AJIO, Nykaa Fashion).
Look at the clothing item in the photo. If several garments are visible, describe the most
prominent one. Reply with ONLY a JSON object:
{
  "garment": "e.g. anarkali kurta, co-ord set, saree, bodycon dress, crop top, palazzo",
  "gender": "women | men | girls | boys | unisex",
  "colours": ["primary colour", "secondary colour"],
  "pattern": "solid / floral print / ikat / bandhani / embroidered / striped / ...",
  "fabric": "best guess or null",
  "details": ["neckline", "sleeve length", "silhouette", "embellishment", ...],
  "brand": "only if a logo/label is clearly readable, else null",
  "queries": ["3 to 4 short shopping search queries a buyer would type to find this exact item,
              most specific first, each under 10 words, Indian retail vocabulary"]
}"""


def _client(s: Settings):
    import anthropic

    if s.llm_provider == "bedrock":
        return anthropic.AsyncAnthropicBedrock(aws_region=s.aws_region), s.bedrock_model
    return anthropic.AsyncAnthropic(api_key=s.anthropic_api_key), s.anthropic_model


async def describe(image_jpeg: bytes, settings: Settings | None = None) -> dict | None:
    s = settings or get_settings()
    if s.llm_provider not in ("anthropic", "bedrock"):
        return None
    if s.llm_provider == "anthropic" and not s.anthropic_api_key:
        return None
    if s.llm_provider == "bedrock" and not s.bedrock_model:
        log.warning("LLM_PROVIDER=bedrock but BEDROCK_MODEL is empty; captioning skipped")
        return None
    try:
        client, model = _client(s)
        msg = await asyncio.wait_for(client.messages.create(
            model=model,
            max_tokens=600,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.b64encode(image_jpeg).decode()}},
                {"type": "text", "text": PROMPT},
            ]}],
        ), timeout=45)
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        m = re.search(r"\{.*\}", text, re.S)
        data = json.loads(m.group(0)) if m else None
        if isinstance(data, dict):
            data["queries"] = [q for q in data.get("queries") or [] if isinstance(q, str)][:4]
            return data
    except Exception as e:
        log.warning("captioning failed: %s", e)
    return None
