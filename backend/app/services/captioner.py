"""Claude reads the photo (search queries) and double-checks matches (same item or not).

Default provider is Amazon Bedrock through the server's IAM role, so no API key is needed.
LLM_PROVIDER=anthropic + ANTHROPIC_API_KEY also works. LLM_PROVIDER=none disables both.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re

from PIL import Image

from ..config import Settings, get_settings

log = logging.getLogger(__name__)

DESCRIBE = """You are a fashion merchandiser for Indian e-commerce (Myntra, AJIO, Amazon.in, Flipkart).
Look at the clothing in the photo. If several garments are visible, describe the most prominent one
(for a set, describe the set). Reply with ONLY a JSON object:
{
  "garment": "e.g. anarkali kurta set, co-ord set, saree, bodycon dress, crop top, palazzo",
  "gender": "women | men | girls | boys | unisex",
  "colours": ["primary colour", "secondary colour"],
  "pattern": "solid / floral print / ikat / bandhani / block print / embroidered / ...",
  "fabric": "best guess or null",
  "details": ["neckline", "sleeve length", "silhouette", "embellishment", "..."],
  "brand": "only if a logo, label or watermark is clearly readable, else null",
  "queries": ["4 shopping search queries a buyer would type on Amazon.in or Flipkart to find this
              exact item: most specific first (brand if known, colour, print, garment, key detail),
              then progressively broader; each 3 to 8 words, Indian retail vocabulary"]
}"""

VERIFY = """The FIRST image is a photo a shopper took or saved. The following images are product
listings, numbered in order starting at 1. For each listing decide whether it is the SAME product
as the shopper's photo: same design, same print/embroidery pattern, same colour, same cut. A
different colour of the same design is NOT the same product. A similar style is NOT the same product.
Ignore background, model, pose, crop and lighting differences.
Reply with ONLY a JSON object mapping each listing number to {"same": true|false, "confidence": 0.0-1.0},
for example {"1": {"same": true, "confidence": 0.9}, "2": {"same": false, "confidence": 0.8}}"""


def enabled(s: Settings) -> bool:
    if s.llm_provider == "bedrock":
        return bool(s.bedrock_model)
    if s.llm_provider == "anthropic":
        return bool(s.anthropic_api_key)
    return False


def _small_jpeg(data: bytes, side: int) -> bytes:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((side, side))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()


def _bedrock_call(s: Settings, images: list[bytes], prompt: str, max_tokens: int) -> str:
    import boto3
    from botocore.config import Config

    client = boto3.client("bedrock-runtime", region_name=s.aws_region,
                          config=Config(read_timeout=60, retries={"max_attempts": 3, "mode": "adaptive"}))
    content = [{"image": {"format": "jpeg", "source": {"bytes": b}}} for b in images]
    content.append({"text": prompt})
    r = client.converse(modelId=s.bedrock_model, messages=[{"role": "user", "content": content}],
                        inferenceConfig={"maxTokens": max_tokens, "temperature": 0})
    return "".join(c.get("text", "") for c in r["output"]["message"]["content"])


async def _anthropic_call(s: Settings, images: list[bytes], prompt: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=s.anthropic_api_key)
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                            "data": base64.b64encode(b).decode()}} for b in images]
    content.append({"type": "text", "text": prompt})
    msg = await client.messages.create(model=s.anthropic_model, max_tokens=max_tokens, temperature=0,
                                       messages=[{"role": "user", "content": content}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


async def _ask(s: Settings, images: list[bytes], prompt: str, max_tokens: int, timeout: float) -> dict | None:
    try:
        if s.llm_provider == "bedrock":
            text = await asyncio.wait_for(asyncio.to_thread(_bedrock_call, s, images, prompt, max_tokens), timeout)
        else:
            text = await asyncio.wait_for(_anthropic_call(s, images, prompt, max_tokens), timeout)
        m = re.search(r"\{.*\}", text, re.S)
        data = json.loads(m.group(0)) if m else None
        return data if isinstance(data, dict) else None
    except Exception as e:
        log.warning("claude call failed: %s", str(e)[:300])
        return None


async def describe(image_jpeg: bytes, settings: Settings | None = None) -> dict | None:
    s = settings or get_settings()
    if not enabled(s):
        return None
    data = await _ask(s, [_small_jpeg(image_jpeg, 1024)], DESCRIBE, 700, 60)
    if data:
        data["queries"] = [q.strip() for q in data.get("queries") or [] if isinstance(q, str) and q.strip()][:4]
    return data


async def verify(image_jpeg: bytes, candidates: list[bytes | None],
                 settings: Settings | None = None) -> dict[int, tuple[bool, float]]:
    """Ask Claude which candidate photos show the same product. Returns {index: (same, confidence)}."""
    s = settings or get_settings()
    if not enabled(s) or not candidates:
        return {}
    query = _small_jpeg(image_jpeg, 768)
    usable: list[tuple[int, bytes]] = []
    for i, b in enumerate(candidates):
        if not b:
            continue
        try:
            usable.append((i, _small_jpeg(b, 512)))
        except Exception:
            continue
    batches = [usable[k:k + 10] for k in range(0, len(usable), 10)]
    results = await asyncio.gather(*[_ask(s, [query] + [b for _, b in chunk], VERIFY, 900, 90)
                                     for chunk in batches])
    out: dict[int, tuple[bool, float]] = {}
    for chunk, res in zip(batches, results):
        if not res:
            continue
        for k, v in res.items():
            try:
                pos = int(k) - 1
            except ValueError:
                continue
            if 0 <= pos < len(chunk) and isinstance(v, dict):
                conf = v.get("confidence")
                out[chunk[pos][0]] = (bool(v.get("same")), float(conf) if isinstance(conf, (int, float)) else 0.5)
    return out
