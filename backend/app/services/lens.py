"""Reverse image search ("Lens") providers.

  serpapi  – real Google Lens results through SerpApi (engine=google_lens): exact matches with
             shop links and prices. Free plan: 250 searches / month.
  vision   – Google Cloud Vision WEB_DETECTION: pages that contain the same photo (full or
             partial match). Official Google API; first 1,000 images / month free.

Keys come from the environment (SERPAPI_KEY, GOOGLE_VISION_KEY) or, on AWS, from SSM Parameter
Store under /styletrail/ (SecureString), re-read every few minutes so a key added in the AWS
console starts working without a redeploy. Key values are never logged or returned by the API.
"""
from __future__ import annotations

import asyncio
import base64
import html
import logging
import re
import time

import httpx

from ..config import Settings
from .serp_parse import Listing, extract_listings

log = logging.getLogger(__name__)

SSM_PREFIX = "/styletrail/"
_cache: dict = {"at": 0.0, "vals": {}}


def _ssm_values(region: str) -> dict:
    if time.monotonic() - _cache["at"] < 300:
        return _cache["vals"]
    vals: dict = {}
    try:
        import boto3
        from botocore.config import Config
        c = boto3.client("ssm", region_name=region, config=Config(connect_timeout=3, read_timeout=5,
                                                                    retries={"max_attempts": 1}))
        for page in c.get_paginator("get_parameters_by_path").paginate(Path=SSM_PREFIX, WithDecryption=True):
            for p in page.get("Parameters", []):
                vals[p["Name"][len(SSM_PREFIX):].upper()] = (p.get("Value") or "").strip().strip('"\'').strip()
    except Exception as e:  # no AWS credentials locally, no permission, …
        log.debug("parameter store unavailable: %s", e)
    _cache.update(at=time.monotonic(), vals=vals)
    return vals


async def keys(s: Settings) -> dict:
    ssm = await asyncio.to_thread(_ssm_values, s.aws_region)
    return {"serpapi": s.serpapi_key or ssm.get("SERPAPI_KEY", ""),
            "vision": s.google_vision_key or ssm.get("GOOGLE_VISION_KEY", "")}


async def configured(s: Settings) -> dict:
    k = await keys(s)
    return {name: bool(v) for name, v in k.items()}


# --------------------------------------------------------------------------- SerpApi Google Lens
def parse_serpapi(doc: dict) -> list[Listing]:
    out: list[Listing] = []
    for key, channel in (("exact_matches", "lens:exact_matches"), ("products", "lens:products"),
                         ("visual_matches", "lens:visual_matches")):
        if isinstance(doc.get(key), list):
            out += extract_listings({key: doc[key]}, channel)
    return out


async def serpapi_lens(client: httpx.AsyncClient, key: str, image_url: str, s: Settings) -> list[Listing]:
    out: list[Listing] = []
    for typ in [t.strip() for t in s.serpapi_lens_types.split(",") if t.strip()]:
        try:
            r = await client.get("https://serpapi.com/search.json", params={
                "engine": "google_lens", "url": image_url, "type": typ, "country": s.country,
                "hl": s.language, "api_key": key}, timeout=60)
            doc = r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("serpapi lens %s failed: %s", typ, type(e).__name__)
            continue
        if doc.get("error"):
            log.warning("serpapi lens %s: %s", typ, str(doc["error"])[:200])
            continue
        out += parse_serpapi(doc)
    return out


# --------------------------------------------------------------------------- Cloud Vision web detection
def parse_vision(doc: dict) -> list[Listing]:
    out: list[Listing] = []
    resp = (doc.get("responses") or [{}])[0]
    web = resp.get("webDetection") or {}
    for i, page in enumerate(web.get("pagesWithMatchingImages") or []):
        url = page.get("url") or ""
        if not url.startswith("http"):
            continue
        full = page.get("fullMatchingImages") or []
        part = page.get("partialMatchingImages") or []
        img = (full or part or [{}])[0].get("url")
        title = re.sub(r"<[^>]+>", "", html.unescape(page.get("pageTitle") or "")).strip()
        out.append(Listing(url=url, title=title[:300] or url, image_url=img, price=None,
                           channel="lens:exact_matches" if full else "lens:products",
                           position=i + 1, extra={"via": "vision"}))
    return out


async def vision_web(client: httpx.AsyncClient, key: str, image: bytes) -> list[Listing]:
    body = {"requests": [{"image": {"content": base64.b64encode(image).decode()},
                          "features": [{"type": "WEB_DETECTION", "maxResults": 60}],
                          "imageContext": {"webDetectionParams": {"includeGeoResults": True}}}]}
    try:
        r = await client.post("https://vision.googleapis.com/v1/images:annotate", params={"key": key},
                              json=body, timeout=40)
        doc = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("vision web detection failed: %s", type(e).__name__)
        return []
    err = (doc.get("error") or (doc.get("responses") or [{}])[0].get("error"))
    if err:
        log.warning("vision web detection: %s", str(err.get("message", err))[:200])
        return []
    return parse_vision(doc)


async def search(s: Settings, image: bytes, image_url: str | None) -> list[Listing]:
    k = await keys(s)
    if not (k["serpapi"] or k["vision"]):
        return []
    async with httpx.AsyncClient() as client:
        jobs = []
        if k["serpapi"] and image_url:
            jobs.append(serpapi_lens(client, k["serpapi"], image_url, s))
        if k["vision"]:
            jobs.append(vision_web(client, k["vision"], image))
        out: list[Listing] = []
        for res in await asyncio.gather(*jobs, return_exceptions=True):
            if isinstance(res, Exception):
                log.warning("lens provider failed: %s", type(res).__name__)
            else:
                out += res
        return out
