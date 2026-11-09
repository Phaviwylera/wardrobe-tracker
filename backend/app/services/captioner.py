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
  "garment": "the product type as Myntra names it, e.g. shirt style top, regular top, crop top, tunic,
              kurta, anarkali kurta set, co-ords, A-line dress, bodycon dress, saree, palazzo trousers",
  "gender": "women | men | girls | boys | unisex",
  "colours": ["primary colour", "secondary colour"],
  "pattern": "solid / floral print / ikat / bandhani / block print / embroidered / ...",
  "fabric": "best guess or null",
  "details": ["neckline", "sleeve length", "silhouette", "embellishment", "..."],
  "brand": "only if a logo, label or watermark is clearly readable, else null",
  "queries": ["6 shopping search queries a buyer would type on Amazon.in, Flipkart or Myntra to find
              this exact item, each 3 to 7 words, Indian retail vocabulary. Start with the most specific
              (brand if readable, colour, print or work type, garment). Indian stores often name
              colours loosely, so add variants with the neighbouring colour names they use (for example
              red / maroon / pink / rust, blue / navy / teal). If you cannot tell printed from embroidered,
              include both. End with one broad query (colour + garment)."],
  "store_queries": ["3 queries written exactly like Myntra / AJIO product titles, without a brand:
              'Women <Colour> <Pattern or work> <Product type>', e.g. 'Women Green Solid Shirt Style Top',
              'Green Solid Shirt Style Top', 'Women Maroon Embroidered Anarkali Kurta'. Use Myntra's product
              type words (Shirt Style Top, Regular Top, Kurta, A-Line Dress, Co-Ords, ...)."]
}"""

VERIFY = """Image 1 is a photo a shopper took or saved. Image 2 is a product listing from a store.
Is image 2 the exact same product as the garment in image 1? Judge only the garment, not the person,
pose, background or photo quality.
First list the distinguishing details of each garment (colours, print or embroidery motifs and where
they sit, neckline, yoke, sleeves, hem or border, dupatta). Then compare them point by point.
"same" means the identical catalogue design: every visible distinguishing detail matches. Same garment
type and colour alone is NOT enough.
"similar" means same type and overall look but at least one design detail differs or cannot be confirmed.
"different" means a different kind of garment or clearly unlike it.
End your reply with a JSON line: {"verdict": "same" | "similar" | "different", "confidence": 0.0-1.0}"""


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


_CLIENTS: dict[str, object] = {}


def _client_for(region: str):
    if region not in _CLIENTS:
        import boto3
        from botocore.config import Config

        _CLIENTS[region] = boto3.client("bedrock-runtime", region_name=region,
                                        config=Config(read_timeout=60, retries={"max_attempts": 4, "mode": "adaptive"}))
    return _CLIENTS[region]


def _bedrock_call(s: Settings, images: list[bytes], prompt: str, max_tokens: int,
                  model: str | None = None, region: str | None = None) -> str:
    content = [{"image": {"format": "jpeg", "source": {"bytes": b}}} for b in images]
    content.append({"text": prompt})
    r = _client_for(region or s.bedrock_region or s.aws_region).converse(
        modelId=model or s.bedrock_model, messages=[{"role": "user", "content": content}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": 0})
    return "".join(c.get("text", "") for c in r["output"]["message"]["content"])


def judges(s: Settings) -> list[tuple[str, str]]:
    """(model, region) pairs that verify matches; defaults to the describing model."""
    out = []
    for part in (s.verify_models or "").split(","):
        part = part.strip()
        if part:
            model, _, region = part.partition("@")
            out.append((model.strip(), (region or s.bedrock_region or s.aws_region).strip()))
    if not out and s.llm_provider == "bedrock":
        out = [(s.bedrock_model, s.bedrock_region or s.aws_region)]
    return out


async def _anthropic_call(s: Settings, images: list[bytes], prompt: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=s.anthropic_api_key)
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                            "data": base64.b64encode(b).decode()}} for b in images]
    content.append({"type": "text", "text": prompt})
    msg = await client.messages.create(model=s.anthropic_model, max_tokens=max_tokens, temperature=0,
                                       messages=[{"role": "user", "content": content}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


async def _ask(s: Settings, images: list[bytes], prompt: str, max_tokens: int, timeout: float,
               model: str | None = None, region: str | None = None) -> dict | None:
    try:
        if s.llm_provider == "bedrock":
            text = await asyncio.wait_for(
                asyncio.to_thread(_bedrock_call, s, images, prompt, max_tokens, model, region), timeout)
        else:
            text = await asyncio.wait_for(_anthropic_call(s, images, prompt, max_tokens), timeout)
        # The verifier reasons first and ends with a JSON line; take the last JSON object.
        tail = re.findall(r"\{[^{}]*\"verdict\"[^{}]*\}", text)
        if tail:
            return json.loads(tail[-1])
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
    data = await _ask(s, [_small_jpeg(image_jpeg, 1024)], DESCRIBE, 900, 60)
    if data:
        seen, qs = set(), []
        for q in data.get("queries") or []:
            if isinstance(q, str) and q.strip() and q.strip().lower() not in seen:
                seen.add(q.strip().lower())
                qs.append(q.strip())
        data["queries"] = qs[:6]
        sq = []
        for q in data.get("store_queries") or []:
            if isinstance(q, str) and q.strip() and q.strip().lower() not in {x.lower() for x in sq}:
                sq.append(q.strip())
        data["store_queries"] = sq[:3]
    return data


def combine(votes: list[tuple[str, float]], judges_configured: int) -> tuple[str, float] | None:
    """Merge judges' verdicts. exact needs every answering judge to say same (and two answers when
    two or more judges are configured); one "same" against "similar" is "close"."""
    if not votes:
        return None
    verdicts = [v for v, _ in votes]
    conf = min(c for _, c in votes)
    if all(v == "same" for v in verdicts):
        if len(votes) >= min(2, judges_configured):
            return "same", conf
        return "close", conf
    if "same" in verdicts and "different" not in verdicts:
        return "close", conf
    if all(v == "different" for v in verdicts):
        return "different", conf
    return "similar", conf


async def verify(image_jpeg: bytes, candidates: list[bytes | None],
                 settings: Settings | None = None) -> dict[int, tuple[str, float]]:
    """Compare each candidate photo with the shopper's photo, one pair per call, with every judge.

    Returns {index: (verdict, confidence)}; verdict is same / close / similar / different.
    """
    s = settings or get_settings()
    if not enabled(s) or not candidates:
        return {}
    query = _small_jpeg(image_jpeg, 768)
    js = judges(s) if s.llm_provider == "bedrock" else [(None, None)]
    sems = {j: asyncio.Semaphore(10) for j in js}

    async def one(i: int, b: bytes | None):
        if not b:
            return i, []
        try:
            cand = _small_jpeg(b, 640)
        except Exception:
            return i, []

        async def ask(j):
            async with sems[j]:
                res = await _ask(s, [query, cand], VERIFY, 550, 60, model=j[0], region=j[1])
            if not res:
                return None
            v = str(res.get("verdict", "")).lower().strip()
            if v not in ("same", "similar", "different"):
                return None
            c = res.get("confidence")
            return v, float(c) if isinstance(c, (int, float)) else 0.5

        votes = [v for v in await asyncio.gather(*[ask(j) for j in js]) if v]
        return i, votes

    out: dict[int, tuple[str, float]] = {}
    for i, votes in await asyncio.gather(*[one(i, b) for i, b in enumerate(candidates)]):
        merged = combine(votes, len(js))
        if merged:
            out[i] = merged
    return out
