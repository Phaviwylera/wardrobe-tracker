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
from .serp_parse import Listing, extract_listings, parse_price
from .stores import STORES, Store, is_product_url, store_for

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


# --------------------------------------------------------------------------- Google Shopping (store feeds)
# Stores such as Myntra, AJIO and Nykaa Fashion publish their catalogue (photo, title, price) to Google
# Shopping themselves. Results carry no store link, so each gets a placeholder URL on the store's own
# domain; the few that match the photo are resolved to the real product link afterwards (resolve()).
GSHOP = "/gshop/p"


def _store_by_source(source: str) -> Store | None:
    n = re.sub(r"\.(com|in|co\.in|org)$", "", (source or "").strip().lower())
    n = re.sub(r"[^a-z0-9]", "", n)
    if not n:
        return None
    for st in STORES:
        names = {re.sub(r"[^a-z0-9]", "", st.name.lower()), st.key, st.domains[0].split(".")[0]}
        if n in names:
            return st
    return None


def is_placeholder(url: str) -> bool:
    return GSHOP in url


def parse_shopping(doc: dict) -> list[Listing]:
    out: list[Listing] = []
    seen_thumbs: set = set()
    for i, it in enumerate(doc.get("shopping_results") or []):
        st = _store_by_source(it.get("source") or "")
        pid, token = it.get("product_id"), it.get("immersive_product_page_token")
        if not st or not pid or not token:
            continue
        price, cur = parse_price(it.get("extracted_price") or it.get("price"))
        if not price:
            continue
        mrp, _ = parse_price(it.get("extracted_old_price") or it.get("old_price"))
        title = re.sub(r"['\"()]*\s*by\s+" + re.escape(st.name) + r"['\"]*\s*$", "", it.get("title") or "", flags=re.I)
        title = re.sub(r"['\"(]+\s*[A-Za-z0-9]{0,4}\s*[)'\"]*$", "", title).strip()   # size left over by the feed: '('M
        thumb = it.get("thumbnail")
        if thumb in seen_thumbs:      # one feed row per size; keep the first
            continue
        seen_thumbs.add(thumb)
        out.append(Listing(url=f"https://www.{st.domains[0]}{GSHOP}{pid}", title=title[:300], price=price,
                           currency="INR" if (it.get("price") or "").strip().startswith("₹") else (cur or "INR"),
                           image_url=it.get("thumbnail"), source=st.name, channel="shopping", position=i + 1,
                           extra={"mrp": mrp, "immersive": token, "store_key": st.key}))
    return out


async def shopping_search(s: Settings, queries: list[str]) -> list[Listing]:
    k = await keys(s)
    queries = [q for q in queries if q][: s.serpapi_shopping_queries]
    if not k["serpapi"] or not queries:
        return []
    out: list[Listing] = []
    async with httpx.AsyncClient() as client:
        async def one(q: str) -> list[Listing]:
            try:
                r = await client.get("https://serpapi.com/search.json", params={
                    "engine": "google_shopping", "q": q, "gl": s.country, "hl": s.language,
                    "location": "India", "api_key": k["serpapi"]}, timeout=60)
                doc = r.json()
            except (httpx.HTTPError, ValueError) as e:
                log.warning("serpapi shopping failed: %s", type(e).__name__)
                return []
            if doc.get("error"):
                log.warning("serpapi shopping: %s", str(doc["error"])[:200])
                return []
            return parse_shopping(doc)
        for res in await asyncio.gather(*[one(q) for q in queries]):
            out += res
    return out


def parse_immersive(doc: dict) -> list[dict]:
    """Stores selling one Google Shopping product: [{url, price, mrp, store}] for product pages of known stores."""
    out: list[dict] = []
    seen: set[str] = set()
    stack = [doc.get("product_results") or doc]
    while stack:
        cur = stack.pop()
        if isinstance(cur, list):
            stack.extend(cur)
        elif isinstance(cur, dict):
            link = cur.get("link")
            if isinstance(link, str) and link.startswith("http") and cur.get("name"):
                link = re.sub(r"^http://", "https://", link.split("?srsltid=")[0].split("&srsltid=")[0])
                st = store_for(link)
                if st.key != "other" and is_product_url(link) and link not in seen:
                    seen.add(link)
                    price, _ = parse_price(cur.get("extracted_price") or cur.get("price"))
                    mrp, _ = parse_price(cur.get("extracted_original_price") or cur.get("original_price"))
                    out.append({"url": link, "price": price, "mrp": mrp, "store": st.key})
            stack.extend(v for v in cur.values() if isinstance(v, (dict, list)))
    return out


async def resolve(s: Settings, token: str) -> list[dict]:
    k = await keys(s)
    if not k["serpapi"]:
        return []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("https://serpapi.com/search.json", params={
                "engine": "google_immersive_product", "page_token": token, "gl": s.country, "hl": s.language,
                "api_key": k["serpapi"]}, timeout=60)
            doc = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("serpapi product lookup failed: %s", type(e).__name__)
        return []
    if doc.get("error"):
        log.warning("serpapi product lookup: %s", str(doc["error"])[:200])
        return []
    return parse_immersive(doc)


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
